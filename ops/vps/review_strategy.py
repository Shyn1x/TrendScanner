"""Fetch only public Bybit candles; never access DB, credentials, or private API."""
import ast
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

HOUR = 3_600_000
MINUTE = 60_000
DAY = 24 * HOUR
CUTOFF = 1793103504260  # Fixed prior audit, 2026-10-27 12:18:24 UTC.
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


def main():
    # Only public candles leave the VPS. No DB, private API, or credential access.
    workspace = Path.cwd()
    universe = read_universe(workspace)
    start = (CUTOFF//HOUR - 21*24)*HOUR
    end = CUTOFF//HOUR*HOUR
    hourly = {symbol:fetch_klines('/v5/market/kline',symbol,'60',start,end) for symbol in universe}
    minute_cases = {}
    for symbol,ready in CASES:
        minute_cases[symbol] = {kind:fetch_klines(path,symbol,'1',ready+HOUR-MINUTE,ready+13*HOUR+MINUTE)
                               for kind,path in [('trade','/v5/market/kline'),('mark','/v5/market/mark-price-kline')]}
    report = {'public_market_data_only':True,'source':'https://api.bybit.com',
              'fixed_cutoff_ms':CUTOFF,'fetched_at_ms':int(time.time()*1000),
              'universe':universe,'hourly':hourly,'minute_cases':minute_cases}
    raw=json.dumps(report,sort_keys=True)
    print('PUBLIC_CANDLE_REVIEW_JSON='+raw)
    print('PUBLIC CANDLE REVIEW: PASS; sha256='+hashlib.sha256(raw.encode()).hexdigest())
    return False


if __name__ == '__main__':
    raise SystemExit(main())
