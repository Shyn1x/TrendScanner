"""One resumable, read-only VPS snapshot for all Demo journal trades."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import time

from audit_demo import DAY, Reader, compare_trade, failure_code, load_journal, number
from hourly_shadow import counts, validate_state
from monitor import evaluate, snapshot
from runtime import atomic_json

WINDOW = 7 * DAY
# Demo retains 30 days. Start the first request at the retention boundary.
RETENTION = 30 * DAY


class BudgetExhausted(RuntimeError):
    pass


class BulkReader(Reader):
    def __init__(self, client, root, start, cutoff, *, seconds=230, max_requests=500):
        super().__init__(client)
        self.root = root
        self.start = start
        self.cutoff = cutoff
        self.deadline = time.monotonic() + seconds
        self.max_requests = max_requests
        self.requests = 0
        self.last_request = None
        self.cache = {}

    def request_page(self, path, params):
        if self.last_request is not None:
            time.sleep(max(0, .22 - (time.monotonic() - self.last_request)))
        if time.monotonic() >= self.deadline or self.requests >= self.max_requests:
            raise BudgetExhausted
        self.requests += 1
        self.last_request = time.monotonic()
        return super().request_page(path, params)

    def window(self, path, params, start, end):
        requested_order = params.get('orderId')
        query = {key: val for key, val in params.items() if key != 'orderId'}
        # Share all execution history across orders and all settlement history
        # across trades. Order history and closed P&L retain their symbol filter.
        first = max(start, self.start)
        end = min(end, self.cutoff)
        if first > end:
            return []
        bucket = self.start + ((first - self.start) // WINDOW) * WINDOW
        result = {}
        while bucket <= end:
            last = min(bucket + WINDOW - 1, self.cutoff)
            key = hashlib.sha256(json.dumps([path, query, bucket, last], sort_keys=True).encode()).hexdigest()
            path_cache = self.root / (key + '.json')
            if key not in self.cache:
                if path_cache.is_file():
                    rows = json.loads(path_cache.read_text())['rows']
                else:
                    rows = self.rows(path, {**query, 'startTime': bucket, 'endTime': last})
                    atomic_json(path_cache, {'rows': rows})
                self.cache[key] = rows
            for row in self.cache[key]:
                if requested_order and row.get('orderId') != requested_order:
                    continue
                if path in {'/v5/execution/list', '/v5/account/transaction-log'}:
                    stamp = int(row['execTime' if path == '/v5/execution/list' else 'transactionTime'])
                    if not first <= stamp <= end:
                        continue
                result[json.dumps(row, sort_keys=True)] = row
            bucket += WINDOW
        return list(result.values())


def database_snapshot(connection, executor, experiment, four_version):
    connection.read_only = True
    trades, models = load_journal(connection, executor, experiment)
    coverage = {}
    with connection.cursor() as cur:
        cur.execute("SELECT (inet_server_addr() IS NULL OR inet_server_addr() <<= inet '127.0.0.0/8' OR inet_server_addr() = inet '::1')")
        local_database = cur.fetchone()[0]
        for timeframe, step, table, column, version in (
            ('15m', 900_000, 'lower_tf_prospective_coverage', 'experiment_version', experiment),
            ('1h', 3_600_000, 'lower_tf_prospective_coverage', 'experiment_version', experiment),
            ('4h', 14_400_000, 'market_regime_shadow_coverage', 'shadow_version', four_version),
        ):
            cur.execute('SELECT to_regclass(%s)', (table,))
            if not cur.fetchone()[0]:
                coverage[timeframe] = None
                continue
            cur.execute(f'SELECT ready_timestamp, expected_evaluations, observed_evaluations, gap_candles'
                        f' FROM {table} WHERE {column}=%s AND timeframe=%s ORDER BY ready_timestamp',
                        (version, timeframe))
            rows = cur.fetchall()
            coverage[timeframe] = None if not rows else {
                'first_ms': rows[0][0], 'last_ms': rows[-1][0], 'rows': len(rows),
                'missing_slots': (rows[-1][0] - rows[0][0]) // step + 1 - len(rows),
                'incomplete_rows': sum(expected != actual for _, expected, actual, _ in rows),
                'reported_gap_slots': sum(gaps for _, _, _, gaps in rows),
            }
    return trades, models, coverage, local_database


def collect(args):
    from bybit_demo_executor import BybitDemoClient, EXECUTOR_VERSION, EXPERIMENT_VERSION, _connect

    args.checkpoint.mkdir(mode=0o700, parents=True, exist_ok=True)
    frozen_path = args.checkpoint / 'snapshot.json'
    if not frozen_path.exists():
        from psycopg import IsolationLevel
        now = int(time.time() * 1000)
        with _connect(os.environ['DATABASE_URL']) as connection:
            connection.isolation_level = IsolationLevel.REPEATABLE_READ
            trades, models, coverage, local_db = database_snapshot(
                connection, EXECUTOR_VERSION, EXPERIMENT_VERSION, args.four_version)
        clock_ok = os.environ['AUDIT_CLOCK_SYNC'] == 'yes'
        health = snapshot(args.runtime_root, now // 1000, clock_ok)
        shadow_path = args.runtime_root / 'state/hourly-shadow.json'
        shadow = None
        if shadow_path.is_file():
            state = json.loads(shadow_path.read_text())
            validate_state(state)
            shadow = {**counts(state), 'cohort': [state['candidates'][key] for key in state['cohort']]}
        for trade in trades:
            trade['failure_code'] = failure_code(trade.pop('last_error', None)) if trade['status'] == 'FAILED' else None
        frozen = {'cutoff_ms': now, 'journal': trades, 'models': [[*key, model] for key, model in models.items()],
                  'coverage': coverage, 'local_database': local_db, 'health': health,
                  'health_evaluation': evaluate(health, now // 1000), 'shadow': shadow}
        atomic_json(frozen_path, json.loads(json.dumps(frozen, default=str)))
    frozen = json.loads(frozen_path.read_text())
    now = frozen['cutoff_ms']
    client = BybitDemoClient(os.environ['BYBIT_DEMO_API_KEY'],
                             os.environ['BYBIT_DEMO_API_SECRET'], timeout=6)
    reader = BulkReader(client, args.checkpoint, now - RETENTION, now)
    positions_path = args.checkpoint / 'position-count.json'
    if not positions_path.exists():
        positions = reader.rows('/v5/position/list', {'category': 'linear', 'settleCoin': 'USDT', 'limit': 200})
        atomic_json(positions_path, {'observed_ms': int(time.time() * 1000),
                                    'open': sum(number(p['size']) > 0 for p in positions)})
    models = {(symbol, stamp): model for symbol, stamp, model in frozen.pop('models')}
    reports, errors = [], []
    claims = defaultdict(list)
    for trade in frozen['journal']:
        if trade['status'] != 'CLOSED':
            continue
        key = (trade['symbol'], trade['ready_timestamp'])
        if int(trade['signal_close_ms']) < reader.start:
            errors.append([*key, 'BEFORE_DEMO_RETENTION_WINDOW'])
            continue
        try:
            ids = set()
            report = compare_trade(reader, trade, models.get(key), now, execution_ids=ids)
            reports.append(report)
            for execution_id in ids:
                claims[execution_id].append(report)
        except BudgetExhausted:
            errors.append([*key, 'BUDGET_EXHAUSTED'])
        except Exception as exc:
            errors.append([*key, type(exc).__name__, getattr(exc, 'code', None)])
    for owners in claims.values():
        if len(owners) > 1:
            for report in owners:
                if 'EXECUTION_CLAIMED_BY_MULTIPLE_TRADES' not in report['issues']:
                    report['issues'].append('EXECUTION_CLAIMED_BY_MULTIPLE_TRADES')
    # The encrypted export needs research metrics and matched fills, never raw order IDs.
    for trade in frozen['journal']:
        for field in ('entry_order_id', 'exit_order_id', 'order_link_id', 'exit_order_link_id'):
            trade.pop(field)
    frozen.update(reports=reports, errors=errors, api_requests=reader.requests,
                  positions=json.loads(positions_path.read_text()))
    atomic_json(args.output, json.loads(json.dumps(frozen, default=str)))
    print(f'DEMO BULK AUDIT: closed evidence {len(reports)}, errors {len(errors)}, API requests {reader.requests}')
    retryable = any(row[2] != 'BEFORE_DEMO_RETENTION_WINDOW' for row in errors)
    return bool(retryable or any('FUNDING_NOT_AVAILABLE' in report['issues'] for report in reports))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--four-version', required=True)
    args = parser.parse_args()
    try:
        return collect(args)
    except Exception as exc:
        print('DEMO BULK AUDIT FAILED: ' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
