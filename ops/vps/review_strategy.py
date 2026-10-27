"""Read-only fixed-contract signal review and two execution case reconstructions.

No executor cycle, schema bootstrap, order mutation, or experiment change.
"""
import ast
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

HOUR = 3_600_000
MINUTE = 60_000
DAY = 24 * HOUR
CUTOFF = 1793103504260  # Fixed prior audit, 2026-10-27 12:18:24 UTC.
STOP_VERSION = 'lower-tf-stop-v1-fixed-bps-50-75-100-150'
PATHS = {'/v5/market/kline', '/v5/market/mark-price-kline'}
CASES = (('AAVE/USDT', 1792501200000), ('FIL/USDT', 1792702800000))


def fetch_klines(path, symbol, interval, start, end, opener=urllib.request.urlopen):
    if path not in PATHS or not re.fullmatch(r'[A-Z0-9]+/USDT', symbol):
        raise ValueError('PUBLIC_KLINE_ALLOWLIST')
    step = {'1': MINUTE, '60': HOUR}[interval]
    if start % step or end % step or not 0 < end-start <= 31*DAY:
        raise ValueError('KLINE_BOUNDARY')
    rows = {}
    boundary = end - 1
    while boundary >= start:
        params = dict(category='linear', symbol=symbol.replace('/', ''),
                      interval=interval, start=start, end=boundary, limit=1000)
        url = 'https://api.bybit.com' + path + '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'Accept': 'application/json'}, method='GET')
        with opener(request, timeout=20) as response:
            payload = json.load(response)
        if payload.get('retCode') != 0:
            raise ValueError('BYBIT_KLINE_ERROR:' + str(payload.get('retCode')))
        batch = payload['result']['list']
        if not batch:
            break
        earliest = min(int(row[0]) for row in batch)
        if earliest > boundary:
            raise ValueError('KLINE_PAGE_DID_NOT_ADVANCE')
        for row in batch:
            timestamp = int(row[0])
            if not start <= timestamp < end:
                continue
            record = [timestamp] + [float(x) for x in row[1:5]]
            if not all(__import__('math').isfinite(x) and x > 0 for x in record[1:]):
                raise ValueError('KLINE_NONFINITE_OR_NONPOSITIVE')
            if record[3] > min(record[1], record[4]) or record[2] < max(record[1], record[4]):
                raise ValueError('KLINE_INVALID_OHLC')
            if timestamp in rows and rows[timestamp] != record:
                raise ValueError('KLINE_CONFLICTING_DUPLICATE')
            rows[timestamp] = record
        boundary = earliest - 1
        time.sleep(.25)
    expected = list(range(start, end, step))
    if sorted(rows) != expected:
        raise ValueError('KLINE_GAP:' + str(len(expected)-len(rows)))
    return [rows[t] for t in expected]


def long_outcome(candles, ready):
    """Same closed signal candle plus exactly 12 future 1h candles."""
    window = [candles.get(ready + i*HOUR) for i in range(13)]
    if any(row is None for row in window):
        return None
    entry = window[0][4]
    stop = entry * .985
    hit = any(row[3] <= stop for row in window[1:])
    gross = -1.5 if hit else (window[-1][4]/entry - 1)*100
    return {'entry': entry, 'stop_hit': hit, 'gross_pct': gross, 'net_pct': gross-.16}


def first_touch(candles, stop, start, end):
    """An intraminute entry cannot establish order of events in that minute."""
    for row in candles:
        timestamp = row[0]
        if timestamp + MINUTE <= start or timestamp >= end:
            continue
        if row[3] <= stop:
            return {'bar_start_ms': timestamp, 'bar_end_ms': timestamp+MINUTE,
                    'low': row[3], 'entry_minute_ambiguous': timestamp < start}
    return None


def read_population(connection, experiment):
    connection.read_only = True
    with connection.cursor() as cur:
        cur.execute('SET LOCAL statement_timeout = 30000')
        cur.execute('SELECT symbol, ready_timestamp, entry_reference_price, stop_hit, '
                    'bars_to_stop, horizon_close_return_pct, stop_adjusted_return_pct, no_tick_fills '
                    'FROM lower_tf_prospective_stop_outcomes WHERE experiment_version=%s '
                    'AND stop_outcome_version=%s AND timeframe=%s AND direction=%s '
                    'AND horizon_bars=12 AND stop_loss_bps=150 '
                    'AND ready_timestamp + %s <= %s AND created_at <= to_timestamp(%s/1000.0) '
                    'ORDER BY ready_timestamp, symbol',
                    (experiment, STOP_VERSION, '1h', 'LONG', 13*HOUR, CUTOFF, CUTOFF))
        fields = ('symbol', 'ready', 'entry', 'stop_hit', 'bars_to_stop', 'close_pct', 'gross_pct', 'no_tick_fills')
        outcomes = [dict(zip(fields, row)) for row in cur.fetchall()]
        cur.execute('SELECT payload FROM lower_tf_prospective_events '
                    'WHERE experiment_version=%s ORDER BY ready_timestamp, symbol', (experiment,))
        events = [row[0] for row in cur.fetchall()]
    keys = [(r['symbol'], int(r['ready'])) for r in outcomes]
    if len(keys) != len(set(keys)):
        raise ValueError('DUPLICATE_POPULATION_KEYS')
    mature = [p for p in events if p.get('timeframe') == '1h' and p.get('direction') == 'LONG'
              and int(p['ready_timestamp']) + 13*HOUR <= CUTOFF]
    return outcomes, {'mature_events': len(mature), 'outcomes': len(outcomes),
                      'missing_outcome_keys': sorted(set((p['symbol'], int(p['ready_timestamp'])) for p in mature)-set(keys))}


def read_universe(workspace):
    tree = ast.parse((workspace/'artifacts/trendscanner/ready_outcome_pilot.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SYMBOLS' for t in node.targets):
            symbols = ast.literal_eval(node.value)
            if len(symbols) != len(set(symbols)) or not symbols:
                raise ValueError('INVALID_UNIVERSE')
            return symbols
    raise ValueError('UNIVERSE_NOT_FOUND')


def build_baseline(outcomes, symbols, hourly):
    indexed = {s: {row[0]: row for row in hourly[s]} for s in symbols}
    times = sorted({int(row['ready']) for row in outcomes})
    all_ready = range(times[0], times[-1]+HOUR, HOUR)
    baseline = {}
    pooled = []
    for ready in all_ready:
        rows = [long_outcome(indexed[s], ready) for s in symbols]
        if any(row is None for row in rows):
            raise ValueError('BASELINE_INCOMPLETE_UNIVERSE')
        avg = sum(row['net_pct'] for row in rows)/len(rows)
        baseline[ready] = avg
        pooled.extend({'symbol': s, 'ready': ready, **row} for s, row in zip(symbols, rows))
    paired = []
    for row in outcomes:
        recomputed = long_outcome(indexed[row['symbol']], int(row['ready']))
        discrepancy = abs(recomputed['gross_pct']-float(row['gross_pct']))
        paired.append({**row, 'net_pct': float(row['gross_pct'])-.16,
                       'baseline_pct': baseline[int(row['ready'])],
                       'excess_pct': float(row['gross_pct'])-.16-baseline[int(row['ready'])],
                       'recomputed_gross_pct': recomputed['gross_pct'],
                       'recomputed_discrepancy_pct': discrepancy})
    return paired, pooled


def case_review(trade, model, reader, public):
    from audit_demo import compare_trade
    case = compare_trade(reader, trade, model, CUTOFF)
    entry = case['entry_fills']
    start = int(case['signal_close_ms']) - MINUTE
    end = int(case['time_exit_due_ms']) + MINUTE
    data = {kind: public(path, case['symbol'], '1', start, end) for kind, path in
            [('trade', '/v5/market/kline'), ('mark', '/v5/market/mark-price-kline')]}
    model_stop = float(model['entry_price'])*.985
    actual_stop = float(case['stop_price'])
    touches = {}
    for kind, series in data.items():
        for level, stop in [('model', model_stop), ('actual', actual_stop)]:
            for origin, timestamp in [('signal', case['signal_close_ms']), ('fill', entry['first_ms'])]:
                touches[kind+'/'+level+'/'+origin] = first_touch(series, stop, timestamp, case['time_exit_due_ms'])
    exit_ms = case['exit_fills']['first_ms']
    windows = {kind: [row for row in series if exit_ms//MINUTE*MINUTE-2*MINUTE <= row[0] <= exit_ms//MINUTE*MINUTE+2*MINUTE]
               for kind, series in data.items()}
    case.update(model_stop=model_stop, actual_stop=actual_stop, touches=touches, exit_window=windows,
                minute_coverage={kind: len(series) for kind, series in data.items()},
                holding_minima={kind: min(row[3] for row in series if entry['first_ms'] <= row[0] < case['time_exit_due_ms'])
                                for kind, series in data.items()})
    orders = reader.window('/v5/order/history', {'category':'linear', 'symbol':trade['symbol'].replace('/',''), 'limit':50},
                           case['signal_close_ms'], case['time_exit_due_ms']+HOUR)
    case['related_orders'] = [{k:o.get(k) for k in ('side','orderType','orderStatus','stopOrderType','triggerBy',
                               'slTriggerBy','triggerPrice','stopLoss','createdTime','updatedTime','avgPrice','cumExecQty')}
                              for o in orders if o.get('orderId') in {trade.get('entry_order_id'),trade.get('exit_order_id')}
                              or o.get('parentOrderLinkId')==trade['order_link_id']
                              or o.get('orderLinkId') in {trade['order_link_id'],trade['exit_order_link_id']}]
    return case, data


def main():
    from audit_demo import Reader, load_journal
    from bybit_demo_executor import _connect, BybitDemoClient, EXECUTOR_VERSION, EXPERIMENT_VERSION
    workspace = Path.cwd()
    with _connect(os.environ['DATABASE_URL']) as connection:
        outcomes, coverage = read_population(connection, EXPERIMENT_VERSION)
        trades, models = load_journal(connection, EXECUTOR_VERSION, EXPERIMENT_VERSION)
    if not outcomes:
        raise ValueError('NO_MATURE_OUTCOMES')
    universe = read_universe(workspace)
    start = min(int(r['ready']) for r in outcomes)
    end = max(int(r['ready']) for r in outcomes)+13*HOUR
    hourly = {}
    for symbol in universe:
        hourly[symbol] = fetch_klines('/v5/market/kline',symbol,'60',start,end)
    paired, baseline = build_baseline(outcomes, universe, hourly)
    reader = Reader(BybitDemoClient(os.environ['BYBIT_DEMO_API_KEY'],os.environ['BYBIT_DEMO_API_SECRET']))
    cases, candles, errors = [], {}, []
    for key in CASES:
        try:
            trade = next(t for t in trades if (t['symbol'],int(t['ready_timestamp']))==key)
            case, data = case_review(trade, models[key], reader, fetch_klines)
            cases.append(case); candles[key[0]] = data
        except Exception as exc:
            errors.append({'case':key,'error_type':type(exc).__name__})
    raw = json.dumps({'hourly':hourly,'minute_cases':candles},sort_keys=True).encode()
    output = Path(os.environ['STRATEGY_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    (output/'strategy-review-candles.json').write_bytes(raw)
    report = {'read_only':True,'review_version':'strategy-review-v1','fixed_cutoff_ms':CUTOFF,
              'fetched_at_ms':int(time.time()*1000),'experiment':EXPERIMENT_VERSION,'coverage':coverage,
              'universe':universe,'population':paired,'baseline':baseline,'cases':cases,'errors':errors,
              'candle_sha256':hashlib.sha256(raw).hexdigest()}
    (output/'strategy-review.json').write_text(json.dumps(report,sort_keys=True,default=str))
    # Pooled baseline can be reproduced from the candle artifact; keep logs bounded.
    print('STRATEGY_REVIEW_JSON='+json.dumps({**report,'baseline':{'n':len(baseline),'mean_net_pct':sum(r['net_pct'] for r in baseline)/len(baseline)}},sort_keys=True,default=str))
    print('READ-ONLY STRATEGY REVIEW: '+('FAIL' if errors else 'PASS'))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
