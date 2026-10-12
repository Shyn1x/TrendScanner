"""Read-only Demo fills versus the same prospective event's 12-bar outcome."""
import json
import os
import re
import time
from collections import Counter
from decimal import Decimal

DAY = 86_400_000
HOUR = 3_600_000
READ_PATHS = {'/v5/position/list', '/v5/order/history', '/v5/execution/list',
              '/v5/position/closed-pnl', '/v5/account/transaction-log'}
STOP_VERSION = 'lower-tf-stop-v1-fixed-bps-50-75-100-150'
TRADE_FIELDS = ('experiment_version', 'symbol', 'ready_timestamp', 'signal_close_ms',
                'exit_due_ms', 'order_link_id', 'exit_order_link_id', 'status',
                'risk_amount', 'reference_price', 'quantity', 'stop_price',
                'entry_order_id', 'exit_order_id', 'avg_entry_price', 'closed_pnl', 'submitted_ms', 'last_error')


def failure_code(value):
    # Stored exceptions can contain sensitive text. Expose only known error codes.
    match = re.match(r'^(?:BybitAPIError:)?BYBIT_(-?\d+):', str(value or ''))
    if match:
        return 'BYBIT_' + match.group(1)
    if value in {'ENTRY_Rejected', 'ENTRY_Cancelled', 'ENTRY_Deactivated'}:
        return value
    return 'UNKNOWN_REQUIRES_PRIVATE_LOG_REVIEW'


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('NONFINITE_NUMBER')
    return result


class Reader:
    """No write API surface; restrict both the host and endpoint allowlist."""
    def __init__(self, client):
        if client.base_url != 'https://api-demo.bybit.com':
            raise ValueError('DEMO_ENDPOINT_REQUIRED')
        self.client = client

    def rows(self, path, params):
        if path not in READ_PATHS:
            raise ValueError('READ_ENDPOINT_REQUIRED')
        rows, cursors = {}, set()
        values = dict(params)
        for _ in range(100):
            result = self.client.request('GET', path, values)
            for row in result['list']:
                # Full row identity avoids deleting several fills of one order.
                rows[json.dumps(row, sort_keys=True)] = row
            cursor = result.get('nextPageCursor')
            if not cursor:
                return list(rows.values())
            if cursor in cursors:
                raise ValueError('REPEATED_PAGE_CURSOR')
            cursors.add(cursor)
            values['cursor'] = cursor
        raise ValueError('PAGE_LIMIT_EXCEEDED')

    def window(self, path, params, start, end):
        if end - start > 31 * DAY:
            raise ValueError('AUDIT_WINDOW_EXCEEDS_31_DAYS')
        rows = {}
        while start <= end:
            boundary = min(end, start + 7 * DAY - 1)
            for row in self.rows(path, {**params, 'startTime': start, 'endTime': boundary}):
                rows[json.dumps(row, sort_keys=True)] = row
            start = boundary + 1
        return list(rows.values())


def load_journal(connection, executor_version, experiment):
    # The server enforces read-only mode before the first statement; no bootstrap.
    connection.read_only = True
    with connection.cursor() as cur:
        cur.execute('SET LOCAL statement_timeout = 30000')
        cur.execute('SELECT to_regclass(%s), to_regclass(%s)',
                    ('bybit_demo_strategy_trades', 'lower_tf_prospective_stop_outcomes'))
        trades_exist, outcomes_exist = cur.fetchone()
        if not trades_exist:
            return [], {}
        cur.execute('SELECT ' + ', '.join(TRADE_FIELDS) +
                    ' FROM bybit_demo_strategy_trades WHERE executor_version=%s AND experiment_version=%s'
                    ' ORDER BY ready_timestamp, symbol', (executor_version, experiment))
        trades = [dict(zip(TRADE_FIELDS, row)) for row in cur.fetchall()]
        models = {}
        if outcomes_exist:
            cur.execute('SELECT symbol, ready_timestamp, entry_reference_price, stop_hit, bars_to_stop,'
                        ' stop_adjusted_return_pct FROM lower_tf_prospective_stop_outcomes'
                        ' WHERE stop_outcome_version=%s AND experiment_version=%s AND timeframe=%s'
                        ' AND direction=%s AND horizon_bars=12 AND stop_loss_bps=150',
                        (STOP_VERSION, experiment, '1h', 'LONG'))
            for symbol, ready, price, hit, bars, ret in cur.fetchall():
                models[symbol, int(ready)] = {'entry_price': price, 'stop_hit': hit,
                                             'bars_to_stop': bars, 'gross_return_pct': ret}
    return trades, models


def compare_trade(reader, trade, model, now):
    ready = int(trade['ready_timestamp'])
    report = {key: trade.get(key) for key in ('symbol', 'ready_timestamp', 'status', 'risk_amount',
                                             'avg_entry_price', 'stop_price', 'closed_pnl')}
    report['signal_close_ms'] = ready + HOUR
    report['time_exit_due_ms'] = ready + 13 * HOUR
    issues = []
    if (int(trade['signal_close_ms']) != ready + HOUR or
            int(trade['exit_due_ms']) != ready + 13 * HOUR):
        issues.append('JOURNAL_TIME_CONTRACT_MISMATCH')
    if model:
        risk = number(trade['risk_amount'])
        report['model'] = {**model, 'fee_bps_per_side': 6, 'slippage_bps_per_side': 2,
                           'net_pnl_at_journal_risk': risk * (number(model['gross_return_pct']) - Decimal('.16')) / Decimal('1.5')}
    else:
        issues.append('MODEL_OUTCOME_PENDING_OR_MISSING')
    start = min(int(trade.get('submitted_ms') or trade['signal_close_ms']), int(trade['signal_close_ms']))
    # History might lag; never mistake absent executions for zero fees.
    orders = reader.window('/v5/order/history', {'category': 'linear', 'symbol': trade['symbol'].replace('/', ''), 'limit': 50}, start, now)
    entry_orders = [o for o in orders if (o.get('orderId') == trade.get('entry_order_id')
                    if trade.get('entry_order_id') else o.get('orderLinkId') == trade['order_link_id'])]
    exits = [o for o in orders if o.get('side') == 'Sell' and o.get('reduceOnly') is True and
             ((trade.get('exit_order_id') and o.get('orderId') == trade['exit_order_id']) or
              o.get('orderLinkId') == trade['exit_order_link_id'] or
              o.get('parentOrderLinkId') == trade['order_link_id']) and number(o.get('cumExecQty') or '0') > 0]
    fills = []
    for order in entry_orders + exits:
        fills.extend(reader.window('/v5/execution/list', {'category': 'linear', 'orderId': order['orderId'], 'limit': 100}, start, now))
    unique = {f['execId']: f for f in fills if f.get('execType') == 'Trade'}
    buys = [f for f in unique.values() if f.get('side') == 'Buy']
    sells = [f for f in unique.values() if f.get('side') == 'Sell']
    for label, rows in (('entry', buys), ('exit', sells)):
        if rows:
            qty = sum(number(f['execQty']) for f in rows)
            price = sum(number(f['execQty']) * number(f['execPrice']) for f in rows) / qty
            report[label + '_fills'] = {'count': len(rows), 'quantity': qty, 'average_price': price,
                                        'first_ms': min(int(f['execTime']) for f in rows),
                                        'last_ms': max(int(f['execTime']) for f in rows)}
            report[label + '_fees_usdt'] = sum(number(f['execFee']) for f in rows) if all(f.get('execFee') not in (None, '') and f.get('feeCurrency') in (None, '', 'USDT') for f in rows) else None
            if report[label + '_fees_usdt'] is None:
                issues.append(label.upper() + '_FEES_MISSING_OR_NON_USDT')
        else:
            report[label + '_fees_usdt'] = None
            issues.append(label.upper() + '_FILLS_NOT_AVAILABLE')
    if buys and model:
        report['entry_gap_from_signal_close_bps'] = (report['entry_fills']['average_price'] / number(model['entry_price']) - 1) * 10000
        report['entry_delay_ms'] = report['entry_fills']['first_ms'] - (ready + HOUR)
    reasons = {'STOP_LOSS' if o.get('stopOrderType') == 'StopLoss' else
               'TIME_EXIT' if o.get('orderLinkId') == trade['exit_order_link_id'] else 'OTHER' for o in exits}
    report['exit_reason'] = next(iter(reasons)) if len(reasons) == 1 else 'UNKNOWN_OR_MIXED'
    report['stop_trigger_types'] = sorted({o.get('triggerBy') or o.get('slTriggerBy') or 'UNKNOWN' for o in exits if o.get('stopOrderType') == 'StopLoss'})
    if exits:
        exit_ids = {o['orderId'] for o in exits}
        pnl_rows = reader.window('/v5/position/closed-pnl', {'category': 'linear', 'symbol': trade['symbol'].replace('/', ''), 'limit': 100}, start, now)
        matched = [r for r in pnl_rows if r.get('orderId') in exit_ids]
        if {r['orderId'] for r in matched} == exit_ids:
            report['bybit_closed_pnl_usdt'] = sum(number(r['closedPnl']) for r in matched)
            if trade.get('closed_pnl') is not None and abs(number(trade['closed_pnl']) - report['bybit_closed_pnl_usdt']) > Decimal('.000001'):
                issues.append('JOURNAL_PNL_DIFFERS_FROM_MATCHED_EXIT_ORDERS')
        else:
            issues.append('MATCHED_CLOSED_PNL_NOT_AVAILABLE')
    # Funding is reported separately. Do not subtract fees again from closedPnl.
    report['funding_cashflow_usdt'] = None
    if buys and sells and sum(number(f['execQty']) for f in buys) == sum(number(f['execQty']) for f in sells):
        first, last = report['entry_fills']['first_ms'], report['exit_fills']['last_ms']
        try:
            settlement = reader.window('/v5/account/transaction-log', {'accountType': 'UNIFIED', 'category': 'linear', 'currency': 'USDT', 'type': 'SETTLEMENT', 'limit': 50}, first, last)
            matched_funding = [r for r in settlement if r.get('symbol') == trade['symbol'].replace('/', '')]
            if any(r.get('funding') in (None, '') for r in matched_funding):
                raise ValueError('FUNDING_FIELD_MISSING')
            report['funding_cashflow_usdt'] = sum(number(r['funding']) for r in matched_funding)
            report['funding_attribution'] = 'symbol_and_holding_interval; assumes no manual/shared position'
        except Exception:
            issues.append('FUNDING_NOT_AVAILABLE')
    elif trade['status'] == 'CLOSED':
        issues.append('CLOSED_TRADE_FILL_QUANTITY_INCOMPLETE')
    if model and trade.get('closed_pnl') is not None:
        report['journal_pnl_minus_model_pnl'] = number(trade['closed_pnl']) - report['model']['net_pnl_at_journal_risk']
    report['issues'] = issues
    return report


def audit(connection, reader, executor_version, experiment, now):
    trades, models = load_journal(connection, executor_version, experiment)
    positions = reader.rows('/v5/position/list', {'category': 'linear', 'settleCoin': 'USDT', 'limit': 200})
    active_positions = [p for p in positions if number(p.get('size') or '0') > 0]
    reports, errors = [], []
    omitted = 0
    for trade in trades:
        if min(int(trade.get('submitted_ms') or trade['signal_close_ms']), int(trade['signal_close_ms'])) < now - 30 * DAY:
            omitted += 1
            continue
        try:
            reports.append(compare_trade(reader, trade, models.get((trade['symbol'], int(trade['ready_timestamp']))), now))
        except Exception as exc:
            # No server error text or credential-bearing exceptions in the report.
            errors.append({'symbol': trade['symbol'], 'ready_timestamp': trade['ready_timestamp'],
                           'error_type': type(exc).__name__, 'code': getattr(exc, 'code', None)})
    return {'report_version': 'bybit-demo-execution-audit-v1', 'read_only': True,
            'checked_at_ms': now, 'signal_experiment': experiment, 'journal_trades': len(trades),
            'journal_status_counts': dict(Counter(t['status'] for t in trades)),
            'journal_failure_codes': dict(Counter(failure_code(t.get('last_error')) for t in trades if t['status'] == 'FAILED')),
            'detail_window_days': 30, 'older_trades_without_execution_detail': omitted,
            'demo_open_positions': len(active_positions),
            'journal_virtual_equity': Decimal(300) + sum(number(t['closed_pnl']) for t in trades if t.get('closed_pnl') is not None),
            'matched_closed_trades': sum(r['status'] == 'CLOSED' and 'model' in r and
                                         'bybit_closed_pnl_usdt' in r and not r['issues'] for r in reports),
            'comparison_status': 'ERROR' if errors else ('WAITING_FOR_DEMO_TRADES' if not trades else 'EVIDENCE_COLLECTED'),
            'limitations': ['trade-candle model versus MarkPrice Demo stops',
                            'signal-close versus actual entry includes execution delay and price drift',
                            'funding separate; never assumed zero when unavailable',
                            'journal closedPnl used as supplied; fees not subtracted twice',
                            'not a full wallet reconciliation; manual/shared positions invalidate funding attribution'],
            'trades': reports, 'errors': errors}


def main():
    from bybit_demo_executor import BybitDemoClient, EXECUTOR_VERSION, EXPERIMENT_VERSION, _connect
    client = BybitDemoClient(os.environ['BYBIT_DEMO_API_KEY'], os.environ['BYBIT_DEMO_API_SECRET'])
    with _connect(os.environ['DATABASE_URL']) as connection:
        result = audit(connection, Reader(client), EXECUTOR_VERSION, EXPERIMENT_VERSION, int(time.time() * 1000))
    print(json.dumps(result, sort_keys=True, default=str))
    print('BYBIT DEMO EXECUTION AUDIT: ' + ('FAIL' if result['errors'] else 'PASS'))
    return bool(result['errors'])


if __name__ == '__main__':
    raise SystemExit(main())
