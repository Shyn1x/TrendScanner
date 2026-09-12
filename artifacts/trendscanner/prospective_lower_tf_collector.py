import argparse
import os

import pandas as pd

from analysis import analyze_timeframe
from lower_tf_coverage import latest_coverage_timestamp, record_complete_candle
from lower_tf_shadow import EXPERIMENT_VERSION, TIMEFRAME_MS, observe_lower_tf
from ready_engine import evaluate_ready_candidate
from ready_outcome_pilot import SYMBOLS
from research_data import get_research_data_before
from scanner import _fetch_recent_ohlcv, _to_futures_symbol, exchange, get_data

TIMEFRAMES = ("1h", "15m")
EXPECTED_BARS = 200
CLOSED_BARS = EXPECTED_BARS - 1
FALLBACK_FETCH_BARS = 205
MAX_NO_TICK_FILLS = FALLBACK_FETCH_BARS - EXPECTED_BARS
MAX_CATCHUP_CANDLES = 64
EXPECTED_EVALUATIONS = 50
OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]
NO_TICK_FILL_POLICY = "previous_close_ohlc_zero_volume"
PLACEHOLDER_POLICY = "flat_open_at_signal_close"
SAFE_STATUSES = {
    "BASELINE_READY",
    "BASELINE_NOT_READY",
    "NOT_READY",
    "NO_TRANSITION",
    "TRANSITION",
    "STALE_OR_DUPLICATE",
}


def evaluate_lower_tf_candidate(symbol, timeframe, direction, result):
    """Apply the existing READY rules without changing production files."""
    if timeframe not in TIMEFRAMES:
        raise ValueError("UNSUPPORTED_LOWER_TIMEFRAME")
    compatibility_timeframe = "1h" if timeframe == "15m" else timeframe
    candidate = evaluate_ready_candidate(
        symbol,
        compatibility_timeframe,
        direction,
        result,
    )
    candidate["timeframe"] = timeframe
    return candidate


def _is_fixed_grid(df, timeframe):
    if df is None or len(df) != EXPECTED_BARS or "time" not in df.columns:
        return False
    times = [int(value) for value in df["time"]]
    tf_ms = TIMEFRAME_MS[timeframe]
    return (
        len(set(times)) == EXPECTED_BARS
        and times == sorted(times)
        and all(right - left == tf_ms for left, right in zip(times, times[1:]))
    )


def _build_fixed_grid(df, timeframe):
    """Build 200 latest slots, filling only bounded KuCoin no-tick gaps."""
    if df is None or df.empty or "time" not in df.columns:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    clean = df[OHLCV_COLUMNS].copy()
    clean["time"] = clean["time"].map(int)
    clean = clean.drop_duplicates(subset="time", keep="last").sort_values("time")
    if len(clean) < EXPECTED_BARS:
        return clean.tail(EXPECTED_BARS).reset_index(drop=True)

    tf_ms = TIMEFRAME_MS[timeframe]
    latest = int(clean.iloc[-1]["time"])
    first = latest - (EXPECTED_BARS - 1) * tf_ms
    expected_times = list(range(first, latest + tf_ms, tf_ms))
    rows_by_time = {
        int(row["time"]): [int(row["time"]), *[row[column] for column in OHLCV_COLUMNS[1:]]]
        for _, row in clean.iterrows()
    }
    missing = [timestamp for timestamp in expected_times if timestamp not in rows_by_time]

    if len(missing) > MAX_NO_TICK_FILLS:
        return clean.tail(EXPECTED_BARS).reset_index(drop=True)

    prior = clean[clean["time"] < first]
    previous_close = None if prior.empty else prior.iloc[-1]["close"]
    repaired_rows = []
    filled = []
    for timestamp in expected_times:
        row = rows_by_time.get(timestamp)
        if row is None:
            if previous_close is None:
                return clean.tail(EXPECTED_BARS).reset_index(drop=True)
            row = [timestamp, previous_close, previous_close, previous_close, previous_close, 0.0]
            filled.append(timestamp)
        repaired_rows.append(row)
        previous_close = row[4]

    repaired = pd.DataFrame(repaired_rows, columns=OHLCV_COLUMNS)
    repaired.attrs["filled_no_tick_timestamps"] = filled
    return repaired


def _get_fixed200_data(symbol, timeframe):
    """Legacy latest-window helper kept for parity tests and diagnostics."""
    df = get_data(symbol, timeframe)
    if _is_fixed_grid(df, timeframe):
        return df

    candles = _fetch_recent_ohlcv(
        _to_futures_symbol(symbol),
        timeframe,
        total_limit=FALLBACK_FETCH_BARS,
    )
    fallback = pd.DataFrame(candles, columns=OHLCV_COLUMNS)
    frames = [frame for frame in (df, fallback) if frame is not None and not frame.empty]
    combined = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=OHLCV_COLUMNS)
    )
    return _build_fixed_grid(combined, timeframe)


def _validate_frame(df, timeframe, symbol, target_timestamp=None):
    if df is None or len(df) != EXPECTED_BARS:
        raise ValueError(f"{symbol}:EXPECTED_200_BARS")
    if "time" not in df.columns:
        raise ValueError(f"{symbol}:MISSING_TIME_COLUMN")

    times = [int(value) for value in df["time"]]
    if len(set(times)) != EXPECTED_BARS:
        raise ValueError(f"{symbol}:DUPLICATE_TIMESTAMPS")
    if times != sorted(times):
        raise ValueError(f"{symbol}:UNSORTED_TIMESTAMPS")

    tf_ms = TIMEFRAME_MS[timeframe]
    if any(right - left != tf_ms for left, right in zip(times, times[1:])):
        raise ValueError(f"{symbol}:NONCONTIGUOUS_TIME_GRID")
    if target_timestamp is not None:
        if times[-2] != int(target_timestamp):
            raise ValueError(f"{symbol}:TARGET_NOT_AT_SIGNAL_CANDLE")
        if times[-1] != int(target_timestamp) + tf_ms:
            raise ValueError(f"{symbol}:PLACEHOLDER_NOT_ALIGNED")


def _latest_closed_timestamp(now_ms, timeframe):
    tf_ms = TIMEFRAME_MS[timeframe]
    return (int(now_ms) // tf_ms) * tf_ms - tf_ms


def _targets_to_process(previous_timestamp, latest_target, timeframe,
                        max_catchup=MAX_CATCHUP_CANDLES):
    tf_ms = TIMEFRAME_MS[timeframe]
    latest_target = int(latest_target)
    if latest_target < 0 or latest_target % tf_ms:
        raise ValueError("INVALID_LATEST_TARGET")
    if previous_timestamp is None:
        return [latest_target]

    previous = int(previous_timestamp)
    if previous % tf_ms:
        raise ValueError("UNALIGNED_PREVIOUS_TIMESTAMP")
    if previous > latest_target:
        raise ValueError("COVERAGE_AHEAD_OF_EXCHANGE_TIME")
    if previous == latest_target:
        return []

    delta = latest_target - previous
    if delta % tf_ms:
        raise ValueError("NONCONTIGUOUS_TARGET_RANGE")
    count = delta // tf_ms
    targets = [previous + index * tf_ms for index in range(1, count + 1)]
    return targets[:max_catchup]


def _source_limit(target_count):
    return EXPECTED_BARS + int(target_count) + MAX_NO_TICK_FILLS + 4


def _fetch_source_for_targets(symbol, timeframe, last_target, target_count):
    tf_ms = TIMEFRAME_MS[timeframe]
    return get_research_data_before(
        symbol,
        timeframe,
        before_timestamp=int(last_target) + tf_ms,
        total_limit=_source_limit(target_count),
    )


def _build_window_for_target(source, timeframe, target_timestamp):
    """Build the exact 200-row as-of window for one historical closed candle.

    The signal candle is row -2. Row -1 is a synthetic just-opened candle made
    only from the signal close, so later market data can never leak into the
    reconstructed decision.
    """
    if source is None or source.empty or "time" not in source.columns:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    tf_ms = TIMEFRAME_MS[timeframe]
    target = int(target_timestamp)
    clean = source[OHLCV_COLUMNS].copy()
    clean["time"] = clean["time"].map(int)
    clean = clean[clean["time"] <= target]
    clean = clean.drop_duplicates(subset="time", keep="last").sort_values("time")

    first = target - (CLOSED_BARS - 1) * tf_ms
    expected_times = list(range(first, target + tf_ms, tf_ms))
    rows_by_time = {
        int(row["time"]): [int(row["time"]), *[row[column] for column in OHLCV_COLUMNS[1:]]]
        for _, row in clean.iterrows()
        if first <= int(row["time"]) <= target
    }
    missing = [timestamp for timestamp in expected_times if timestamp not in rows_by_time]
    if len(missing) > MAX_NO_TICK_FILLS:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    prior = clean[clean["time"] < first]
    previous_close = None if prior.empty else prior.iloc[-1]["close"]
    repaired_rows = []
    filled = []
    for timestamp in expected_times:
        row = rows_by_time.get(timestamp)
        if row is None:
            if previous_close is None:
                return pd.DataFrame(columns=OHLCV_COLUMNS)
            row = [timestamp, previous_close, previous_close, previous_close, previous_close, 0.0]
            filled.append(timestamp)
        repaired_rows.append(row)
        previous_close = row[4]

    if previous_close is None:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    repaired_rows.append([
        target + tf_ms,
        previous_close,
        previous_close,
        previous_close,
        previous_close,
        0.0,
    ])
    frame = pd.DataFrame(repaired_rows, columns=OHLCV_COLUMNS)
    frame.attrs["filled_no_tick_timestamps"] = filled
    frame.attrs["placeholder_policy"] = PLACEHOLDER_POLICY
    return frame


def _collect_target(timeframe, target_timestamp, source_by_symbol, latest_target):
    evaluations = []
    errors = []
    no_tick_fills = []
    collection_mode = "current_asof" if target_timestamp == latest_target else "catchup_replay"

    for symbol in SYMBOLS:
        try:
            df = _build_window_for_target(
                source_by_symbol.get(symbol),
                timeframe,
                target_timestamp,
            )
            _validate_frame(df, timeframe, symbol, target_timestamp=target_timestamp)
            filled = [int(value) for value in df.attrs.get("filled_no_tick_timestamps", [])]
            if filled:
                no_tick_fills.append((symbol, filled))

            result = analyze_timeframe(df)
            if not isinstance(result, dict) or result.get("trend") == "ERROR":
                raise ValueError("PIPELINE_ERROR")

            for direction in ("LONG", "SHORT"):
                available = (
                    result.get("quality", {})
                    .get(direction, {})
                    .get("analysis_available")
                )
                if available is not True:
                    errors.append((symbol, direction, "ANALYSIS_UNAVAILABLE"))
                    continue

                candidate = evaluate_lower_tf_candidate(
                    symbol,
                    timeframe,
                    direction,
                    result,
                )
                candidate["data_quality"] = {
                    "source": "kucoin_futures",
                    "collection_mode": collection_mode,
                    "no_tick_fill_policy": NO_TICK_FILL_POLICY,
                    "filled_no_tick_timestamps": filled,
                    "placeholder_policy": PLACEHOLDER_POLICY,
                }
                if type(candidate.get("ready")) is not bool:
                    errors.append((symbol, direction, "INVALID_READY"))
                    continue
                if candidate.get("ready_timestamp") != int(target_timestamp):
                    errors.append((symbol, direction, "INVALID_TIMESTAMP"))
                    continue
                evaluations.append(candidate)
        except Exception as exc:
            errors.append((symbol, type(exc).__name__, str(exc)[:100]))

    timestamps = sorted({candidate["ready_timestamp"] for candidate in evaluations})
    return {
        "target_timestamp": int(target_timestamp),
        "evaluations": evaluations,
        "errors": errors,
        "timestamps": timestamps,
        "no_tick_fills": no_tick_fills,
        "collection_mode": collection_mode,
    }


def _validate_batch(batch):
    target = batch["target_timestamp"]
    if len(batch["evaluations"]) != EXPECTED_EVALUATIONS:
        raise SystemExit(f"FAIL {target}: expected exactly 50 evaluations")
    if batch["errors"]:
        raise SystemExit(f"FAIL {target}: some symbols failed: {batch['errors']}")
    if batch["timestamps"] != [target]:
        raise SystemExit(f"FAIL {target}: reconstructed candle timestamp mismatch")


def _write_batch(batch, timeframe, database_url):
    status_counts = {}
    failures = []
    for candidate in batch["evaluations"]:
        result = observe_lower_tf(
            candidate,
            database_url=database_url,
            db_path=None,
        )
        status = result.get("shadow_status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in SAFE_STATUSES:
            failures.append((candidate["symbol"], candidate["direction"], status))

    if failures:
        raise SystemExit(f"FAIL {batch['target_timestamp']}: storage failures: {failures}")
    if sum(status_counts.values()) != EXPECTED_EVALUATIONS:
        raise SystemExit(f"FAIL {batch['target_timestamp']}: incomplete storage statuses")

    coverage = record_complete_candle(
        database_url,
        timeframe=timeframe,
        ready_timestamp=batch["target_timestamp"],
        observed_evaluations=EXPECTED_EVALUATIONS,
        experiment_version=EXPERIMENT_VERSION,
    )
    total_gaps = int(coverage.get("total_gap_candles", 0) or 0)
    if total_gaps:
        raise SystemExit(
            f"FAIL: lower-timeframe coverage contains {total_gaps} missing candle(s)"
        )
    return status_counts, coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", required=True, choices=TIMEFRAMES)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    assert len(SYMBOLS) == 25, f"Expected 25 research symbols, got {len(SYMBOLS)}"
    started_ms = int(exchange.milliseconds())
    latest_target = _latest_closed_timestamp(started_ms, args.timeframe)

    database_url = os.environ.get("DATABASE_URL") if args.write else None
    if args.write and not database_url:
        raise SystemExit("FAIL: DATABASE_URL is not configured")

    previous_timestamp = None
    if args.write:
        previous_timestamp = latest_coverage_timestamp(
            database_url,
            timeframe=args.timeframe,
            experiment_version=EXPERIMENT_VERSION,
        )
        targets = _targets_to_process(previous_timestamp, latest_target, args.timeframe)
    else:
        targets = [latest_target]

    print("Experiment:", EXPERIMENT_VERSION)
    print("Timeframe:", args.timeframe)
    print("Latest closed target:", latest_target)
    print("Previous coverage:", previous_timestamp)
    print("Targets this run:", targets)

    if not targets:
        print("UP TO DATE: PASS")
        return

    source_by_symbol = {}
    source_errors = []
    for symbol in SYMBOLS:
        try:
            source_by_symbol[symbol] = _fetch_source_for_targets(
                symbol,
                args.timeframe,
                targets[-1],
                len(targets),
            )
        except Exception as exc:
            source_errors.append((symbol, type(exc).__name__, str(exc)[:100]))
    if source_errors:
        raise SystemExit(f"FAIL: source fetch failed before writes: {source_errors}")

    batches = []
    for target in targets:
        batch = _collect_target(args.timeframe, target, source_by_symbol, latest_target)
        ready_true = sum(candidate["ready"] is True for candidate in batch["evaluations"])
        ready_false = sum(candidate["ready"] is False for candidate in batch["evaluations"])
        print("Target:", target)
        print("Collection mode:", batch["collection_mode"])
        print("Evaluations:", len(batch["evaluations"]))
        print("READY True:", ready_true)
        print("READY False:", ready_false)
        print("No-tick fills:", batch["no_tick_fills"])
        print("Errors:", batch["errors"])
        _validate_batch(batch)
        batches.append(batch)

    if not args.write:
        print("DRY RUN: PASS")
        return

    for batch in batches:
        status_counts, coverage = _write_batch(batch, args.timeframe, database_url)
        print("Written target:", batch["target_timestamp"])
        print("Shadow statuses:", status_counts)
        print("Coverage:", coverage)

    remaining = 0
    if targets[-1] < latest_target:
        tf_ms = TIMEFRAME_MS[args.timeframe]
        remaining = (latest_target - targets[-1]) // tf_ms
    print("Catch-up remaining after this run:", remaining)
    print("NEON WRITE + SEQUENTIAL COVERAGE: PASS")


if __name__ == "__main__":
    main()
