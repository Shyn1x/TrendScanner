"""Research only: original period context vs trailing shadow candles at READY times.

Delivery revision: shadow-rows-optimized-v4.
No replay or strategy changes. Network is used only by explicit CLI execution;
--candles-dir reuses CSVs read-only; --download-missing explicitly permits missing files to be fetched.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import time
import statistics

import numpy as np
import pandas as pd
import ready_market_regime_analysis as market
from ready_outcome_pilot import SYMBOLS
from research_data import get_research_data_before

BASE = 'dfd45d33858eaddb0801e109186c3e22600be75c'
FILES = {
    'CURRENT': 'ready_outcome_results_post_temporal_fix.json',
    'HOLDOUT1': 'ready_outcome_holdout_results_post_temporal_fix.json',
    'HOLDOUT2': 'ready_outcome_holdout2_results_post_temporal_fix.json',
}
TF = market.TIMEFRAME_MS
LONG_ROWS = market.WINDOW_BARS + market.CONTEXT_BARS
SHADOW_CONTEXT_ROWS = LONG_ROWS  # Standalone helper; no dependency on shadow module version.


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def read_inputs(directory):
    payloads, bounds, records, hashes = {}, {}, {}, {}
    # Check ALL files before downloading anything.
    for period, filename in FILES.items():
        path = directory / filename
        require(path.is_file(), f'Missing corrected input: {path}')
        raw = path.read_bytes()
        payloads[period] = json.loads(raw)
        hashes[period] = hashlib.sha256(raw).hexdigest()
    for period, payload in payloads.items():
        config = payload['config']
        require(config['symbols'] == SYMBOLS and config['timeframes'] == ['4h'], f'{period}: changed universe/timeframe')
        require(config['warmup_bars'] == 120 and config['max_replay_bars'] == 880 and tuple(config['horizons']) == (3, 5, 10), f'{period}: changed replay settings')
        runs = payload['runs']
        require(len(runs) == 25 and {r['symbol'] for r in runs} == set(SYMBOLS), f'{period}: incomplete universe')
        records[period], seen = [], set()
        for run in runs:
            symbol = run['symbol']
            require(run['status'] == 'OK' and run['timeframe'] == '4h' and run['replay_meta']['total_bars'] == 1000, f'{period}/{symbol}: invalid run')
            if period == 'CURRENT':
                start = market.derive_current_first_timestamp(payload, symbol, '4h')
                end = start + 999 * TF
            else:
                prefix = 'holdout' if period == 'HOLDOUT1' else 'holdout2'
                window = run[prefix + '_window']
                start, end = window[prefix + '_first_timestamp'], window[prefix + '_last_timestamp']
                require(window[prefix + '_rows'] == 1000, f'{period}/{symbol}: wrong row count')
            require(isinstance(start, int) and isinstance(end, int) and end-start == 999*TF, f'{period}/{symbol}: invalid bounds')
            bounds[(period, symbol)] = (start, end)
            for event in run['events']:
                t = event['ready_timestamp']
                require(event['symbol'] == symbol and event['timeframe'] == '4h' and event['direction'] in ('LONG', 'SHORT'), 'Invalid event identity')
                require(isinstance(t, int) and start <= t <= end and (t-start) % TF == 0, 'Event outside window')
                key = (symbol, event['direction'], t)
                require(key not in seen, 'Duplicate READY event')
                seen.add(key)
                # All READY events, including those without an available H5.
                records[period].append(dict(event, period=period))
    for symbol in SYMBOLS:
        require(bounds['HOLDOUT2', symbol][1]+TF <= bounds['HOLDOUT1', symbol][0], 'HOLDOUT2/HOLDOUT1 overlap')
        require(bounds['HOLDOUT1', symbol][1]+TF <= bounds['CURRENT', symbol][0], 'HOLDOUT1/CURRENT overlap')
    return records, bounds, hashes


def check_frame(frame, start, end, shadow_rows=SHADOW_CONTEXT_ROWS):
    expected = list(range(start-max(market.CONTEXT_BARS, shadow_rows-1)*TF, end+TF, TF))
    require(len(frame) == len(expected) and frame['time'].tolist() == expected, 'OHLCV timestamps are not the exact original context window')
    values = frame[['open', 'high', 'low', 'close', 'volume']].to_numpy(dtype=float)
    require(np.isfinite(values).all() and (values[:, :4] > 0).all() and (values[:, 4] >= 0).all(), 'Invalid OHLCV')
    op, hi, lo, close = (values[:, i] for i in range(4))
    require((lo <= np.minimum(op, close)).all() and (hi >= np.maximum(op, close)).all(), 'Invalid candle ranges')


def _rolling_emas(close, ends, rows):
    """Same seeded recurrence/order as market._ema, batched across windows.

    Do NOT subtract an old EMA or use a closed-form geometric sum: both change
    floating-point rounding and can change classification at exact thresholds.
    Each window is seeded with its own first close, exactly as the old script.
    Separate numpy multiply/add operations preserve the original operation order.
    """
    starts = np.asarray(ends, dtype=np.int64) - rows + 1
    require((starts >= 0).all(), 'Insufficient trailing shadow history')
    ema50 = close[starts].copy()
    ema200 = ema50.copy()
    prior50 = None
    a50, a200 = 2 / 51, 2 / 201
    for offset in range(1, rows):
        values = close[starts + offset]
        ema50 = a50 * values + (1 - a50) * ema50
        ema200 = a200 * values + (1 - a200) * ema200
        if offset == rows - 21:
            prior50 = ema50.copy()
    return ema50, ema200, prior50


def _rolling_atr_features(frame, ends, rows):
    """Precompute shared ATR observations once, preserving statistics.mean.

    Preserve even the original 214-row edge behavior: its first prior ATR
    reads close[-1] for position zero. Correcting that here would NOT be parity.
    """
    close, high, low = (frame[k].astype(float).tolist() for k in ('close', 'high', 'low'))
    tr = [high[0]-low[0]] + [max(high[i]-low[i], abs(high[i]-close[i-1]),
                                  abs(low[i]-close[i-1])) for i in range(1, len(close))]
    atr = {i: statistics.mean(tr[i-13:i+1])/close[i]*100 for i in range(13, len(close))}
    result = []
    for end in ends:
        past = [atr[i] for i in range(end-200, end)]
        if rows == 214:
            start = end-rows+1
            first_tr = max(high[start]-low[start], abs(high[start]-close[end]), abs(low[start]-close[end]))
            past[0] = statistics.mean([first_tr] + tr[start+1:start+14])/close[start+13]*100
        percentile = sum(value <= atr[end] for value in past)/200*100
        result.append({'atr_pct': atr[end], 'prior_atr_observations': len(past),
                       'atr_percentile_past_200': percentile,
                       'volatility': 'LOW' if percentile <= 30 else 'HIGH' if percentile >= 70 else 'NORMAL'})
    return result


def _build_contexts(period, records, frames, shadow_rows):
    long_context, short_context = {}, {}
    targets = sorted({r['ready_timestamp'] for r in records})
    for symbol, frame in frames.items():
        # Exactly one shared full feature calculation per symbol/period.
        features = market._frame_features(frame.iloc[-LONG_ROWS:].copy())
        long_context[period, symbol] = features
        short_context[period, symbol] = {}
        positions = {int(t): i for i, t in enumerate(frame['time'])}
        available = [t for t in targets if t in features]
        if not available:
            continue
        close = frame['close'].to_numpy(dtype=float)
        ends = [positions[t] for t in available]
        ema50, ema200, prior50 = _rolling_emas(close, ends, shadow_rows)
        atr_features = _rolling_atr_features(frame, ends, shadow_rows)
        for j, t in enumerate(available):
            # Reuse price-return fields. EMA seeds and ATR edge handling are
            # window-dependent and are replaced with their exact rolling values.
            feature = dict(features[t])
            feature.update(atr_features[j])
            e50, e200, old50 = float(ema50[j]), float(ema200[j]), float(prior50[j])
            feature.update(ema50=e50, ema200=e200,
                           ema_distance=(e50-e200)/feature['close']*100,
                           ema50_slope_20=(e50-old50)/feature['close']*100)
            require(feature['prior_atr_observations'] == 200, 'ATR history mismatch')
            short_context[period, symbol][t] = feature
    return long_context, short_context


def _reference_contexts(period, records, frames, shadow_rows):
    """Unoptimized v3 calculation, ONLY for small offline parity tests."""
    long_context, short_context = {}, {}
    targets = sorted({r['ready_timestamp'] for r in records})
    for symbol, frame in frames.items():
        features = market._frame_features(frame.iloc[-LONG_ROWS:].copy())
        long_context[period, symbol] = features
        short_context[period, symbol] = {}
        positions = {int(t): i for i, t in enumerate(frame['time'])}
        for t in targets:
            # Identical exact-timestamp symbol support in both contexts.
            if t not in features:
                continue
            i = positions[t]
            local = frame.iloc[i-shadow_rows+1:i+1].copy()
            require(len(local) == shadow_rows, 'Insufficient trailing shadow history')
            feature = market._frame_features(local)[t]
            require(feature['prior_atr_observations'] == features[t]['prior_atr_observations'] == 200, 'ATR history mismatch')
            short_context[period, symbol][t] = feature
    return long_context, short_context


def compare_period(period, records, frames, shadow_rows=SHADOW_CONTEXT_ROWS, *, _builder=_build_contexts):
    require(shadow_rows in (214, 1213), 'Unsupported shadow rows')
    long_context, short_context = _builder(period, records, frames, shadow_rows)
    left, left_diag = market._attach_market(records, long_context)
    right, right_diag = market._attach_market(records, short_context)
    key = lambda r: (r['symbol'], r['timeframe'], r['direction'], r['ready_timestamp'])
    indexed = {key(r): r for r in right}
    require({key(r) for r in left} == set(indexed), 'Different context coverage')
    p3 = lambda r: r['direction'] == 'LONG' and r['market_regime'] == 'MIXED' and r['volatility'] == 'NORMAL'
    count = Counter(total_events=len(records), compared=len(left), unclassified=len(records)-len(left))
    examples = []
    for a in left:
        b = indexed[key(a)]
        require(a['available_symbols'] == b['available_symbols'], 'Breadth support differs')
        same = (a['market_regime'], a['volatility']) == (b['market_regime'], b['volatility'])
        count['identical' if same else 'changed'] += 1
        count['regime_changed'] += a['market_regime'] != b['market_regime']
        count['volatility_changed'] += a['volatility'] != b['volatility']
        count['p3_1213'] += p3(a)
        count['p3_retained'] += p3(a) and p3(b)
        count['p3_lost'] += p3(a) and not p3(b)
        count['p3_new_shadow_only'] += not p3(a) and p3(b)
        if not same and len(examples) < 10:
            fields = ('market_regime', 'volatility', 'ema200', 'close', 'breadth_above_ema50', 'available_symbols')
            examples.append({'symbol': a['symbol'], 'direction': a['direction'], 'ready_timestamp': a['ready_timestamp'],
                             'historical': {f:a[f] for f in fields}, 'shadow': {f:b[f] for f in fields}})
    for field in ('identical','changed','regime_changed','volatility_changed','p3_1213','p3_retained','p3_lost','p3_new_shadow_only'):
        count.setdefault(field, 0)
    return {'counts': dict(count), 'match_pct_of_compared': count['identical']/len(left)*100 if left else None,
            'diagnostics_1213': left_diag, 'diagnostics_shadow': right_diag, 'examples': examples}


def load_context_frame(period, symbol, start, end, shadow_rows, candles_dir,
                       output_dir, download_missing=False, loader=get_research_data_before):
    filename = f'{period}_{symbol.replace("/", "_")}_4h.csv'
    source = candles_dir / filename if candles_dir else None
    cached = source is not None and source.is_file()
    if cached:
        frame = pd.read_csv(source)
    else:
        require(source is None or download_missing, f'Missing cached CSV: {source}; use --download-missing to fetch it')
        frame = loader(symbol, '4h', end+TF,
                       total_limit=market.WINDOW_BARS + max(market.CONTEXT_BARS, shadow_rows-1))
    # An invalid existing CSV is an error, NOT permission to replace/redownload it.
    check_frame(frame, start, end, shadow_rows)
    destination = output_dir / filename
    require(not destination.exists(), f'Output CSV already exists: {destination}')
    if cached:
        shutil.copyfile(source, destination)  # Preserve original bytes.
    else:
        frame.to_csv(destination, index=False)
    print(period, symbol, 'CACHE' if cached else 'DOWNLOADED', 'context OK', flush=True)
    return frame


def self_test():
    """No network, no project outputs. Exact dict/JSON equality, not tolerance."""
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    checks = 0
    # Test batched IEEE-754 recurrence against the original scalar recurrence.
    for seed in (0, 7, 101):
        rng = np.random.default_rng(seed)
        close = 100 + np.cumsum(rng.normal(0, .1, 2212))
        for rows in (214, 1213):
            ends = [1212, 1500, 2211]
            e50, e200, old50 = _rolling_emas(close, ends, rows)
            for j, end in enumerate(ends):
                values = close[end-rows+1:end+1].tolist()
                ref50, ref200 = market._ema(values, 50), market._ema(values, 200)
                actual = np.array([e50[j], e200[j], old50[j]], dtype=np.float64)
                expected = np.array([ref50[-1], ref200[-1], ref50[-21]], dtype=np.float64)
                require(np.array_equal(actual.view(np.uint64), expected.view(np.uint64)), 'EMA bitwise mismatch')
            checks += 1
    print('EMA bitwise checks passed:', checks, flush=True)
    # Adversarial boundary: first prior ATR uses last close in shared code.
    # Ensure optimized code preserves this existing behavior exactly.
    for rows in (214, 1213):
        rng = np.random.default_rng(29)
        c = 100 + np.cumsum(rng.normal(0, 1, 1400))
        f = pd.DataFrame({'close': c, 'high': c+.1, 'low': c-.1})
        end = 1399
        expected = market._frame_features(f.iloc[end-rows+1:end+1].assign(time=np.arange(rows)))[rows-1]
        actual = _rolling_atr_features(f, [end], rows)[0]
        require(actual == {k: expected[k] for k in actual}, 'Exact rolling ATR boundary mismatch')
        checks += 1


    n = 2212
    rng = np.random.default_rng(3)
    close = np.r_[np.full(999, 200.), np.linspace(100., 110., 1213)]
    width = rng.uniform(.2, 3., n)
    frame = pd.DataFrame({'time': np.arange(n)*TF, 'open': close,
                          'high': close+width, 'low': close-width,
                          'close': close, 'volume': np.full(n, 1000.)})
    original_frame = frame.copy(deep=True)
    frames = {'BTC/USDT': frame}
    records = [dict(period='TEST', symbol='BTC/USDT', timeframe='4h', direction=d,
                    ready_timestamp=i*TF)
               for i in (1212, 1400, 1700, 2211) for d in ('LONG', 'SHORT')]
    for rows in (214, 1213):
        started = time.perf_counter()
        reference_contexts = _reference_contexts('TEST', records, frames, rows)
        reference_seconds = time.perf_counter()-started
        with patch.object(market, '_frame_features', wraps=market._frame_features) as feature_calls:
            started = time.perf_counter()
            fast_contexts = _build_contexts('TEST', records, frames, rows)
            fast_seconds = time.perf_counter()-started
            require(feature_calls.call_count == len(frames), 'Features calculated more than once per symbol')
        require(reference_contexts == fast_contexts, 'Exact feature dictionary mismatch')
        a = compare_period('TEST', records, frames, rows, _builder=lambda *args: reference_contexts)
        b = compare_period('TEST', records, frames, rows, _builder=lambda *args: fast_contexts)
        require(json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False), 'Exact report mismatch')
        checks += 1
        print(f'rows={rows}: exact features + report PASS; old={reference_seconds:.3f}s optimized={fast_seconds:.3f}s speedup={reference_seconds/fast_seconds:.2f}x', flush=True)
    pd.testing.assert_frame_equal(frame, original_frame)

    # Cache-only, cache-miss policy, missing-only download and byte preservation.
    with TemporaryDirectory() as tmp:
        root = Path(tmp); cache_dir = root/'cache'; out = root/'new'
        cache_dir.mkdir(); out.mkdir()
        source = cache_dir/'CURRENT_BTC_USDT_4h.csv'
        frame.to_csv(source, index=False)
        before = source.read_bytes()
        start, end = 1212*TF, 2211*TF
        calls = []
        def loader(*args, **kwargs):
            calls.append((args, kwargs)); return frame.copy(deep=True)
        load_context_frame('CURRENT', 'BTC/USDT', start, end, 1213, cache_dir, out, True, loader)
        require(not calls and source.read_bytes() == before, 'Cache was changed or fetched')
        require((out/source.name).read_bytes() == before, 'CSV copy bytes differ')
        checks += 1
        try:
            load_context_frame('HOLDOUT2', 'BTC/USDT', start, end, 1213, cache_dir, out, False, loader)
        except ValueError:
            pass
        else:
            raise AssertionError('Missing cache silently downloaded')
        require(not calls, 'Network used without permission')
        checks += 1
        load_context_frame('HOLDOUT2', 'BTC/USDT', start, end, 1213, cache_dir, out, True, loader)
        require(len(calls) == 1 and calls[0][1]['total_limit'] == 2212, 'Wrong missing-only download')
        require(not (cache_dir/'HOLDOUT2_BTC_USDT_4h.csv').exists(), 'Source cache modified')
        checks += 1
        other = root/'next'; other.mkdir()
        load_context_frame('HOLDOUT2', 'BTC/USDT', start, end, 1213, out, other, True, loader)
        require(len(calls) == 1, 'Restart re-downloaded saved HOLDOUT2')
        checks += 1
        invalid = cache_dir/'HOLDOUT1_BTC_USDT_4h.csv'
        frame.iloc[:-1].to_csv(invalid, index=False)
        raw = invalid.read_bytes()
        try:
            load_context_frame('HOLDOUT1', 'BTC/USDT', start, end, 1213, cache_dir, out, True, loader)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid cache accepted')
        require(len(calls) == 1 and invalid.read_bytes() == raw, 'Invalid cache replaced')
        checks += 1
    print(f'OFFLINE TESTS: {checks} passed; no network calls', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, default=Path('artifacts/trendscanner'))
    parser.add_argument('--output-dir', type=Path, help='Must not exist; prevents overwriting earlier outputs')
    parser.add_argument('--candles-dir', type=Path, help='Existing CSV cache, never modified; missing files require --download-missing')
    parser.add_argument('--shadow-rows', type=int, choices=(214, 1213),
                        default=SHADOW_CONTEXT_ROWS, metavar='SHADOW_ROWS',
                        help='Trailing candles per READY timestamp: 214 or 1213 (default: 1213)')
    parser.add_argument('--download-missing', action='store_true', help='Fetch only missing cached CSVs into the NEW output directory')
    parser.add_argument('--self-test', action='store_true', help='Offline exact parity tests and small benchmark; no network')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    require(args.output_dir is not None, '--output-dir is required for analysis')
    if args.candles_dir:
        require(args.candles_dir.is_dir(), 'Cache directory does not exist')
        require(args.candles_dir.resolve() != args.output_dir.resolve(), 'Cache and output directories must differ')
    require(subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip() == BASE, 'Unexpected HEAD')
    records, bounds, hashes = read_inputs(args.inputs)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    reports = {}
    for period in FILES:
        frames = {}
        for symbol in SYMBOLS:
            start, end = bounds[period, symbol]
            frames[symbol] = load_context_frame(
                period, symbol, start, end, args.shadow_rows,
                args.candles_dir, args.output_dir, args.download_missing)
        reports[period] = compare_period(period, records[period], frames, args.shadow_rows)
        with (args.output_dir / f'comparison_{period}.json').open('x') as handle:
            json.dump(reports[period], handle, indent=2, allow_nan=False)
        print(period, json.dumps(reports[period]['counts']), flush=True)
    total = Counter()
    for report in reports.values():
        total.update(report['counts'])
    # Exact parity criterion, not a trading/regime threshold. Missing coverage
    # never counts as GOOD. Full counts allow assessment of mismatch magnitude.
    verdict = 'GOOD' if total['compared'] and not total['unclassified'] and not total['p3_lost'] and not total['p3_new_shadow_only'] else 'PROBLEM'
    result = {'base_commit': BASE, 'input_sha256': hashes,
              'shadow_context_rows': args.shadow_rows,
              'comparison': 'Original 1213-row period context (past-only prefixes) vs trailing shadow window at each identical READY timestamp',
              'periods': reports, 'total': dict(total),
              'match_pct_of_compared': total['identical']/total['compared']*100 if total['compared'] else None,
              'p3_context_match': verdict, 'verdict_policy': 'GOOD requires full coverage and zero P3 membership differences; this is exact parity, not a materiality threshold.'}
    with (args.output_dir/'comparison.json').open('x') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    expected = (101, 101, 0, 0)
    actual = tuple(total[k] for k in ('p3_1213', 'p3_retained', 'p3_lost', 'p3_new_shadow_only'))
    print('Expected P3 historical/retained/lost/new:', expected, flush=True)
    print('Actual:', actual, flush=True)
    print('EXPECTED COUNTS:', 'MATCH' if actual == expected else 'PROBLEM', flush=True)
    print('P3 CONTEXT MATCH:', verdict, flush=True)


if __name__ == '__main__':
    main()
