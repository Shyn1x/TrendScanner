"""Prospective paired 1h/12h exit research. GET-only exchange, read-only DB.

Enroll at most 50 newly executed Demo entries after activation. Persist the
cohort before evaluating outcomes. No orders, DDL, journal writes or strategy
changes. Results remain in private local state; stdout contains counts only.
"""
import argparse
from collections import Counter
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request

from runtime import atomic_json

VERSION = 'demo-exit-shadow-v1-actual-entry-mark-1m'
HOUR = 3_600_000
MINUTE = 60_000
TARGET = 50
CONTRACT = {'version': VERSION, 'target': TARGET, 'horizons_hours': [1, 12],
            'clock': 'signal_close', 'stop': 'journal_actual_stop_mark_price',
            'entry': 'exact_demo_buy_fills_vwap_last_fill_time',
            'fee_bps_per_side': 6, 'slippage_bps_per_side': 2,
            'funding': 'not_in_model; actual_12h_closed_pnl_kept_separately',
            'intrabar': 'conservative_entry_minute_plus_optimistic_bound',
            'selection': 'first_50_confirmed_fills_observed_before_outcome_evaluation'}
CONTRACT_HASH = hashlib.sha256(json.dumps(CONTRACT, sort_keys=True).encode()).hexdigest()
PUBLIC_HOST = 'https://api.bybit.com'
PUBLIC_PATHS = {'/v5/market/kline', '/v5/market/mark-price-kline'}


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('NONFINITE_NUMBER')
    return result


def new_state(now_ms):
    # Freeze enrollment before querying the journal. Already-open signals cannot
    # sneak into the prospective cohort, including across restarts/upgrades.
    return {'contract': json.loads(json.dumps(CONTRACT)), 'contract_hash': CONTRACT_HASH,
            'activated_ms': now_ms, 'start_signal_close_ms': (now_ms // HOUR + 1) * HOUR,
            'candidates': {}, 'cohort': [], 'last_success_ms': None}


def validate_state(state):
    if state.get('contract_hash') != CONTRACT_HASH or state.get('contract') != CONTRACT:
        raise ValueError('SHADOW_CONTRACT_CHANGED_REQUIRES_NEW_EXPERIMENT')
    if len(state['cohort']) > TARGET or len(set(state['cohort'])) != len(state['cohort']):
        raise ValueError('INVALID_COHORT')
    for key in state['cohort']:
        if key not in state['candidates'] or 'entry' not in state['candidates'][key]:
            raise ValueError('MISSING_FROZEN_ENTRY')


def load_journal(connection, executor_version, experiment, start_ms):
    connection.read_only = True
    fields = ('symbol', 'ready_timestamp', 'signal_close_ms', 'exit_due_ms', 'status',
              'risk_amount', 'quantity', 'stop_price', 'entry_order_id', 'closed_pnl')
    with connection.cursor() as cur:
        cur.execute('SET LOCAL statement_timeout = 10000')
        cur.execute('SELECT ' + ', '.join(fields) + ' FROM bybit_demo_strategy_trades'
                    ' WHERE executor_version=%s AND experiment_version=%s'
                    ' AND timeframe=%s AND direction=%s AND signal_close_ms >= %s'
                    ' ORDER BY ready_timestamp, symbol',
                    (executor_version, experiment, '1h', 'LONG', start_ms))
        return [dict(zip(fields, row)) for row in cur.fetchall()]


class ExecutionOnlyClient:
    """Narrow the existing signed Demo transport to execution-history GET only."""
    base_url = 'https://api-demo.bybit.com'

    def __init__(self, client):
        if client.base_url != self.base_url:
            raise ValueError('DEMO_HOST_REQUIRED')
        self._request = client.request

    def request(self, method, endpoint, params=None):
        if method != 'GET' or endpoint != '/v5/execution/list':
            raise ValueError('SHADOW_DEMO_EXECUTION_GET_ONLY')
        return self._request('GET', endpoint, params, auth=True)


def freeze_entry(reader, trade, now_ms):
    if not trade.get('entry_order_id') or trade['status'] not in {'SUBMITTED', 'OPEN', 'EXIT_SUBMITTED', 'CLOSED'}:
        return None
    start = int(trade['signal_close_ms'])
    # A full 7-day window is sufficient per poll; older unobserved entries remain
    # explicit unresolved candidates, never invented fills.
    rows = reader.rows('/v5/execution/list', {'category': 'linear',
                       'orderId': trade['entry_order_id'], 'startTime': start,
                       'endTime': min(now_ms, start + 7 * 24 * HOUR - 1), 'limit': 100})
    fills = {r['execId']: r for r in rows if r.get('execType') == 'Trade'
             and r.get('orderId') == trade['entry_order_id'] and r.get('side') == 'Buy'}
    if not fills:
        return None
    qty = sum(number(f['execQty']) for f in fills.values())
    if qty <= 0 or qty != number(trade['quantity']):
        return None  # Partial/unreconciled fills: wait, do not resize retrospectively.
    if any(f.get('execFee') in (None, '') or f.get('feeCurrency') not in (None, '', 'USDT') for f in fills.values()):
        raise ValueError('ENTRY_FEES_UNKNOWN')
    price = sum(number(f['execQty']) * number(f['execPrice']) for f in fills.values()) / qty
    stop = number(trade['stop_price'])
    if not 0 < stop < price or abs((price - stop) / price - Decimal('.015')) > Decimal('.001'):
        raise ValueError('UNEXPECTED_JOURNAL_STOP')
    first_ms = min(int(f['execTime']) for f in fills.values())
    last_ms = max(int(f['execTime']) for f in fills.values())
    if not start <= first_ms <= last_ms <= now_ms:
        raise ValueError('INVALID_ENTRY_TIMING')
    return {'price': str(price), 'quantity': str(qty), 'stop_price': str(stop),
            'first_ms': first_ms, 'last_ms': last_ms,
            'fees_usdt': str(sum(number(f['execFee']) for f in fills.values())),
            'delay_ms': last_ms - start, 'fill_count': len(fills)}


def public_get(path, params):
    if path not in PUBLIC_PATHS:
        raise ValueError('PUBLIC_MARKET_GET_REQUIRED')
    request = urllib.request.Request(PUBLIC_HOST + path + '?' + urllib.parse.urlencode(params), method='GET')
    with urllib.request.urlopen(request, timeout=10) as response:
        if not response.geturl().startswith(PUBLIC_HOST + '/'):
            raise ValueError('PUBLIC_REDIRECT_REJECTED')
        payload = json.load(response)
    if payload.get('retCode') != 0:
        raise ValueError('PUBLIC_CANDLES_UNAVAILABLE')
    return payload['result']['list']


def minute_window(symbol, start, end, *, mark=False, get=public_get):
    if start % MINUTE or end % MINUTE or not 0 < end - start <= 12 * HOUR:
        raise ValueError('INVALID_MINUTE_WINDOW')
    path = '/v5/market/mark-price-kline' if mark else '/v5/market/kline'
    raw = get(path, {'category': 'linear', 'symbol': symbol.replace('/', ''),
                     'interval': '1', 'start': start, 'end': end - 1, 'limit': 1000})
    rows = {}
    for row in raw:
        ts = int(row[0])
        if not start <= ts < end:
            continue
        parsed = [ts] + [str(number(v)) for v in row[1:5]]
        if ts in rows and rows[ts] != parsed:
            raise ValueError('CONFLICTING_CANDLE')
        o, h, low, c = map(number, parsed[1:])
        if not 0 < low <= min(o, c) <= max(o, c) <= h:
            raise ValueError('INVALID_OHLC')
        rows[ts] = parsed
    expected = list(range(start, end, MINUTE))
    if sorted(rows) != expected:
        raise ValueError('MISSING_CLOSED_MINUTE')
    return [rows[ts] for ts in expected]


def outcome(entry, trade_candles, mark_candles, due_ms, *, optimistic=False):
    start = int(entry['last_ms']) // MINUTE * MINUTE
    expected = list(range(start, due_ms, MINUTE))
    if ([r[0] for r in trade_candles] != expected or [r[0] for r in mark_candles] != expected or not expected):
        raise ValueError('INCOMPLETE_OUTCOME_PATH')
    stop = number(entry['stop_price'])
    price = number(entry['price'])
    entry_ambiguous = (int(entry['last_ms']) != start and number(mark_candles[0][3]) <= stop)
    # The full fill minute may include an earlier stop touch. Keep both bounds.
    hits = [r for r in mark_candles if number(r[3]) <= stop
            and not (optimistic and entry_ambiguous and r[0] == start)]
    hit = hits[0] if hits else None
    if hit:
        trade_bar = trade_candles[(hit[0] - start) // MINUTE]
        # Mark is the trigger, Trade is the execution proxy. A gap below the stop
        # exits at the worse Trade open, rather than guaranteeing the stop price.
        exit_price = min(stop, number(trade_bar[1]))
    else:
        exit_price = number(trade_candles[-1][4])
    gross = (exit_price / price - 1) * 100
    net = gross - Decimal('.16')
    qty = number(entry['quantity'])
    return {'stop_hit': bool(hit), 'stop_minute_ms': hit[0] if hit else None,
            'exit_due_ms': due_ms, 'gross_price_pct': str(gross), 'net_price_pct': str(net),
            'model_pnl_usdt': str(price * qty * net / 100),
            'entry_minute_ambiguous': entry_ambiguous,
            'partial_fill_span_ambiguous': entry['first_ms'] // MINUTE != start,
            'funding_included': False, 'exit_is_model': True}


def counts(state):
    cohort = [state['candidates'][key] for key in state['cohort']]
    paired = [c for c in cohort if all(str(h) in c.get('outcomes', {}) for h in [1, 12])]
    return {'version': VERSION, 'start_signal_close_ms': state['start_signal_close_ms'],
            'last_success_ms': state.get('last_success_ms'),
            'target': TARGET, 'enrolled': len(cohort), 'paired_complete': len(paired),
            'actual_closed': sum(c.get('actual_status') == 'CLOSED' for c in cohort),
            'one_hour_complete': sum('1' in c.get('outcomes', {}) for c in cohort),
            'entry_minute_ambiguous': sum(c.get('outcomes', {}).get('12', {}).get('entry_minute_ambiguous', False) for c in cohort),
            'unpaired_reasons': dict(Counter(c['ineligible'] for c in state['candidates'].values() if c.get('ineligible'))),
            'unresolved_candidates': sum('entry' not in c and c.get('actual_status') not in {'FAILED', 'SKIPPED'} for c in state['candidates'].values()),
            'retry_errors': sum(bool(c.get('retry_error')) for c in state['candidates'].values()),
            'checkpoint_ready': len(paired) == TARGET and all(c.get('actual_status') == 'CLOSED' and 'actual_closed_pnl_usdt' in c for c in cohort)}


def update(state, trades, reader, save, *, now_ms, market=minute_window, budget_seconds=100):
    validate_state(state)
    deadline = time.monotonic() + budget_seconds
    # Finish discovery/enrollment before looking at any outcome in this poll.
    for trade in trades:
        if trade['signal_close_ms'] < state['start_signal_close_ms']:
            continue
        if trade['signal_close_ms'] != trade['ready_timestamp'] + HOUR or trade['exit_due_ms'] != trade['ready_timestamp'] + 13 * HOUR:
            raise ValueError('JOURNAL_TIME_CONTRACT_CHANGED')
        key = trade['symbol'] + ':' + str(trade['ready_timestamp'])
        if key not in state['candidates'] and len(state['cohort']) >= TARGET:
            continue
        candidate = state['candidates'].setdefault(key, {'symbol': trade['symbol'],
                    'ready_timestamp': trade['ready_timestamp'], 'signal_close_ms': trade['signal_close_ms'],
                    'first_seen_ms': now_ms, 'outcomes': {}})
        candidate['actual_status'] = trade['status']
        if trade['status'] == 'CLOSED' and trade.get('closed_pnl') is not None:
            candidate['actual_closed_pnl_usdt'] = str(number(trade['closed_pnl']))
        if 'entry' in candidate or candidate.get('ineligible') or len(state['cohort']) >= TARGET:
            continue
        if time.monotonic() >= deadline:
            break
        try:
            entry = freeze_entry(reader, trade, now_ms)
            if entry:
                candidate['entry'] = entry
                candidate['risk_amount_usdt'] = str(number(trade['risk_amount']))
                if entry['last_ms'] >= trade['signal_close_ms'] + HOUR:
                    candidate['ineligible'] = 'ENTRY_AFTER_1H_EXIT'
                else:
                    state['cohort'].append(key)
                    candidate.pop('retry_error', None)
        except Exception:
            candidate['retry_error'] = 'ENTRY_READ_FAILED'
        save(state)
    save(state)  # Durable, fixed identities before return-dependent calculations.
    for key in state['cohort']:
        candidate = state['candidates'][key]
        for horizon in [1, 12]:
            if str(horizon) in candidate['outcomes']:
                continue
            due = candidate['signal_close_ms'] + horizon * HOUR
            if now_ms < due + 120_000 or time.monotonic() >= deadline:
                continue  # Only complete, settled candles; no extrapolation.
            start = candidate['entry']['last_ms'] // MINUTE * MINUTE
            try:
                trade_bars = market(candidate['symbol'], start, due)
                mark_bars = market(candidate['symbol'], start, due, mark=True)
                primary = outcome(candidate['entry'], trade_bars, mark_bars, due)
                primary['optimistic_entry_minute_bound'] = outcome(candidate['entry'], trade_bars, mark_bars, due, optimistic=True)
                primary['observed_ms'] = now_ms
                candidate['outcomes'][str(horizon)] = primary
                candidate.pop('retry_error', None)
            except Exception:
                candidate['retry_error'] = 'CLOSED_CANDLES_UNAVAILABLE'
            save(state)
    state['last_success_ms'] = now_ms
    save(state)
    return counts(state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-runtime')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    path = args.root / 'state/hourly-shadow.json'
    if args.status:
        if path.exists():
            state = json.loads(path.read_text())
            validate_state(state)
            print(json.dumps(counts(state), sort_keys=True))
        else:
            print(json.dumps({'version': VERSION, 'initialized': False}))
        return
    from bybit_demo_executor import BybitDemoClient, EXECUTOR_VERSION, EXPERIMENT_VERSION, _connect
    from audit_demo import Reader
    with (args.root / 'state/hourly-shadow.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = int(time.time() * 1000)
        if args.preflight:
            state = json.loads(path.read_text()) if path.exists() else new_state(now)
        else:
            if not path.exists():
                atomic_json(path, new_state(now))
            state = json.loads(path.read_text())
        validate_state(state)
        with _connect(os.environ['DATABASE_URL']) as connection:
            trades = load_journal(connection, EXECUTOR_VERSION, EXPERIMENT_VERSION, state['start_signal_close_ms'])
        if args.preflight:
            print('HOURLY SHADOW PREFLIGHT: PASS')
            return
        reader = Reader(ExecutionOnlyClient(BybitDemoClient(os.environ['BYBIT_DEMO_API_KEY'], os.environ['BYBIT_DEMO_API_SECRET'])))
        summary = update(state, trades, reader, lambda s: atomic_json(path, s), now_ms=now)
        print(json.dumps(summary, sort_keys=True))
        if summary['retry_errors']:
            raise SystemExit('HOURLY SHADOW: RETRY REQUIRED')
        print('HOURLY SHADOW: PASS')


if __name__ == '__main__':
    main()
