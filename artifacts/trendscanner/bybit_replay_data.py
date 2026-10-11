"""Public, immutable candle snapshot for an isolated offline Bybit replay."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import pandas as pd

from prospective_market_data import COLUMNS, INTERVALS, get_bybit_data_before

SNAPSHOT_VERSION = 'bybit-linear-replay-candles-v1'


def digest(rows):
    return hashlib.sha256(json.dumps(rows, separators=(',', ':'), sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_snapshot(bundle):
    manifest = bundle['manifest']
    if (manifest.get('snapshot_version') != SNAPSHOT_VERSION or
            manifest.get('market_source') != 'bybit_linear' or
            manifest.get('category') != 'linear' or manifest.get('price_type') != 'trade'):
        raise ValueError('WRONG_BYBIT_SNAPSHOT_SOURCE')
    timeframe = manifest['timeframe']
    tf_ms = INTERVALS[timeframe][1]
    boundary, bars = manifest['before_timestamp'], manifest['bars']
    if (type(boundary) is not int or type(bars) is not int or bars <= 0 or
            boundary % tf_ms or boundary < bars * tf_ms):
        raise ValueError('INVALID_BYBIT_SNAPSHOT_BOUNDARY')
    if not bundle['candles']:
        raise ValueError('EMPTY_BYBIT_SNAPSHOT')
    expected = list(range(boundary - bars * tf_ms, boundary, tf_ms))
    for symbol, item in bundle['candles'].items():
        rows = item['rows']
        if digest(rows) != item['sha256']:
            raise ValueError('BYBIT_SNAPSHOT_HASH_MISMATCH:' + symbol)
        if len(rows) != bars or any(len(row) != 6 for row in rows) or [row[0] for row in rows] != expected:
            raise ValueError('NONEXACT_BYBIT_SNAPSHOT_GRID:' + symbol)
        for row in rows:
            op, hi, lo, close, volume = row[1:]
            if (any(not math.isfinite(value) for value in row[1:]) or min(op, hi, lo, close) <= 0 or
                    volume < 0 or lo > min(op, close) or hi < max(op, close) or lo > hi):
                raise ValueError('INVALID_BYBIT_SNAPSHOT_OHLCV:' + symbol)
    return bundle


def fetch_snapshot(symbols, timeframe, bars, *, before_timestamp=None, loader=get_bybit_data_before):
    tf_ms = INTERVALS[timeframe][1]
    boundary = before_timestamp if before_timestamp is not None else int(time.time() * 1000) // tf_ms * tf_ms
    bundle = {'manifest': {'snapshot_version': SNAPSHOT_VERSION, 'market_source': 'bybit_linear',
                          'category': 'linear', 'price_type': 'trade', 'timeframe': timeframe,
                          'bars': bars, 'before_timestamp': boundary}, 'candles': {}}
    for symbol in symbols:
        frame = loader(symbol, timeframe, boundary, total_limit=bars)
        rows = [[int(row[0]), *map(float, row[1:])] for row in frame[COLUMNS].itertuples(index=False, name=None)]
        bundle['candles'][symbol] = {'rows': rows, 'sha256': digest(rows)}
        print(f'BYBIT SNAPSHOT: {symbol} {timeframe} rows={len(rows)} before={boundary}', flush=True)
    return validate_snapshot(bundle)


def load_snapshot(path, timeframe, bars):
    bundle = validate_snapshot(json.loads(Path(path).read_text()))
    if bundle['manifest']['timeframe'] != timeframe or bundle['manifest']['bars'] != bars:
        raise ValueError('BYBIT_SNAPSHOT_ARGUMENT_MISMATCH')
    return bundle


def snapshot_frame(bundle, symbol):
    frame = pd.DataFrame(bundle['candles'][symbol]['rows'], columns=COLUMNS)
    frame.attrs['market_source'] = 'bybit_linear'
    return frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', required=True)
    parser.add_argument('--timeframe', choices=('1h', '15m'), default='1h')
    parser.add_argument('--bars', type=int, default=1600)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 300 <= args.bars <= 5000:
        raise SystemExit('bars must be between 300 and 5000')
    bundle = fetch_snapshot(args.symbols.split(','), args.timeframe, args.bars)
    args.output.write_text(json.dumps(bundle, sort_keys=True, allow_nan=False))
    print('BYBIT CANDLE SNAPSHOT: PASS', flush=True)


if __name__ == '__main__':
    main()
