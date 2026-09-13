import argparse
import os

import pandas as pd

from analysis import analyze_timeframe
from market_regime_shadow import TIMEFRAME_MS
from market_regime_shadow_v2 import SHADOW_VERSION, context_for_target, observe_ready
from prospective_4h_v2_coverage import latest_coverage_timestamp, record_complete_candle
from ready_engine import evaluate_ready_candidate
from ready_outcome_pilot import SYMBOLS
from research_data import get_research_data_before
from scanner import exchange

EXPECTED_BARS = 200
CLOSED_BARS = EXPECTED_BARS - 1
EXPECTED_EVALUATIONS = 50
MAX_CATCHUP_CANDLES = 16
OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]
SAFE_STATUSES = frozenset({"NOT_READY", "NO_TRANSITION", "OK"})


def _latest_closed_timestamp(now_ms: int) -> int:
    return (int(now_ms) // TIMEFRAME_MS) * TIMEFRAME_MS - TIMEFRAME_MS


def _targets_to_process(previous_timestamp, latest_target, max_catchup=MAX_CATCHUP_CANDLES):
    latest_target = int(latest_target)
    if latest_target < 0 or latest_target % TIMEFRAME_MS:
        raise ValueError("INVALID_LATEST_TARGET")
    if previous_timestamp is None:
        return [latest_target]

    previous = int(previous_timestamp)
    if previous % TIMEFRAME_MS:
        raise ValueError("UNALIGNED_PREVIOUS_TIMESTAMP")
    if previous > latest_target:
        raise ValueError("COVERAGE_AHEAD_OF_EXCHANGE_TIME")
    if previous == latest_target:
        return []

    delta = latest_target - previous
    if delta % TIMEFRAME_MS:
        raise ValueError("NONCONTIGUOUS_TARGET_RANGE")
    count = delta // TIMEFRAME_MS
    targets = [previous + index * TIMEFRAME_MS for index in range(1, count + 1)]
    return targets[:max_catchup]


def _source_limit(target_count: int) -> int:
    return CLOSED_BARS + int(target_count) + 8


def _fetch_source(symbol: str, last_target: int, target_count: int):
    return get_research_data_before(
        symbol,
        "4h",
        before_timestamp=int(last_target) + TIMEFRAME_MS,
        total_limit=_source_limit(target_count),
    )


def _build_window_for_target(source, target_timestamp: int):
    if source is None or source.empty or "time" not in source.columns:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    target = int(target_timestamp)
    clean = source[OHLCV_COLUMNS].copy()
    clean["time"] = clean["time"].map(int)
    clean = clean[clean["time"] <= target]
    clean = clean.drop_duplicates(subset="time", keep="last").sort_values("time")
    closed = clean.tail(CLOSED_BARS).reset_index(drop=True)

    if len(closed) != CLOSED_BARS:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    times = [int(value) for value in closed["time"]]
    if times[-1] != target:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    if any(right - left != TIMEFRAME_MS for left, right in zip(times, times[1:])):
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    last_close = closed.iloc[-1]["close"]
    placeholder = pd.DataFrame(
        [[target + TIMEFRAME_MS, last_close, last_close, last_close, last_close, 0.0]],
        columns=OHLCV_COLUMNS,
    )
    return pd.concat([closed, placeholder], ignore_index=True)


def _collect_target(target_timestamp: int, source_by_symbol: dict):
    evaluations = []
    errors = []

    for symbol in SYMBOLS:
        try:
            frame = _build_window_for_target(source_by_symbol.get(symbol), target_timestamp)
            if len(frame) != EXPECTED_BARS:
                raise ValueError("EXPECTED_200_BARS")
            if int(frame.iloc[-2]["time"]) != int(target_timestamp):
                raise ValueError("TARGET_NOT_AT_SIGNAL_CANDLE")
            if int(frame.iloc[-1]["time"]) != int(target_timestamp) + TIMEFRAME_MS:
                raise ValueError("PLACEHOLDER_NOT_ALIGNED")

            result = analyze_timeframe(frame)
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

                candidate = evaluate_ready_candidate(symbol, "4h", direction, result)
                if type(candidate.get("ready")) is not bool:
                    errors.append((symbol, direction, "INVALID_READY"))
                    continue
                if candidate.get("ready_timestamp") != int(target_timestamp):
                    errors.append((symbol, direction, "INVALID_TIMESTAMP"))
                    continue
                evaluations.append(candidate)
        except Exception as exc:
            errors.append((symbol, type(exc).__name__, str(exc)[:120]))

    timestamps = sorted({
        candidate["ready_timestamp"]
        for candidate in evaluations
        if isinstance(candidate.get("ready_timestamp"), int)
    })
    return {
        "target_timestamp": int(target_timestamp),
        "evaluations": evaluations,
        "errors": errors,
        "timestamps": timestamps,
    }


def _validate_batch(batch):
    target = batch["target_timestamp"]
    if len(batch["evaluations"]) != EXPECTED_EVALUATIONS:
        raise SystemExit(f"FAIL {target}: expected exactly 50 evaluations")
    if batch["errors"]:
        raise SystemExit(f"FAIL {target}: some symbols failed: {batch['errors']}")
    if batch["timestamps"] != [target]:
        raise SystemExit(f"FAIL {target}: reconstructed candle timestamp mismatch")


def _write_batch(batch, database_url: str):
    target = batch["target_timestamp"]
    ready_true = sum(candidate["ready"] is True for candidate in batch["evaluations"])
    context = context_for_target(target) if ready_true else None

    if context is not None:
        snapshot = context.snapshot()
        market = snapshot.get("market", {}) if isinstance(snapshot, dict) else {}
        if snapshot.get("shadow_status") != "OK" or market.get("target_4h_timestamp") != target:
            raise SystemExit(f"FAIL {target}: market context unavailable for READY=True candle")

    status_counts = {}
    failures = []
    for candidate in batch["evaluations"]:
        result = observe_ready(
            candidate,
            context=context,
            database_url=database_url,
            db_path=None,
        )
        status = result.get("shadow_status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in SAFE_STATUSES:
            failures.append((candidate["symbol"], candidate["direction"], status))

    if failures:
        raise SystemExit(f"FAIL {target}: storage failures: {failures}")
    if sum(status_counts.values()) != EXPECTED_EVALUATIONS:
        raise SystemExit(f"FAIL {target}: incomplete storage statuses")

    coverage = record_complete_candle(
        database_url,
        ready_timestamp=target,
        observed_evaluations=EXPECTED_EVALUATIONS,
    )
    return status_counts, coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    assert len(SYMBOLS) == 25, f"Expected 25 research symbols, got {len(SYMBOLS)}"
    latest_target = _latest_closed_timestamp(exchange.milliseconds())

    database_url = os.environ.get("DATABASE_URL") if args.write else None
    if args.write and not database_url:
        raise SystemExit("FAIL: DATABASE_URL is not configured")

    previous_timestamp = latest_coverage_timestamp(database_url) if args.write else None
    targets = (
        _targets_to_process(previous_timestamp, latest_target)
        if args.write
        else [latest_target]
    )

    print("Experiment:", SHADOW_VERSION)
    print("Timeframe: 4h")
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
            source_by_symbol[symbol] = _fetch_source(symbol, targets[-1], len(targets))
        except Exception as exc:
            source_errors.append((symbol, type(exc).__name__, str(exc)[:120]))
    if source_errors:
        raise SystemExit(f"FAIL: source fetch failed before writes: {source_errors}")

    batches = []
    for target in targets:
        batch = _collect_target(target, source_by_symbol)
        ready_true = sum(candidate["ready"] is True for candidate in batch["evaluations"])
        ready_false = sum(candidate["ready"] is False for candidate in batch["evaluations"])
        print("Target:", target)
        print("Evaluations:", len(batch["evaluations"]))
        print("READY True:", ready_true)
        print("READY False:", ready_false)
        print("Errors:", batch["errors"])
        _validate_batch(batch)
        batches.append(batch)

    if not args.write:
        print("DRY RUN: PASS")
        return

    for batch in batches:
        status_counts, coverage = _write_batch(batch, database_url)
        print("Shadow statuses:", status_counts)
        print("Coverage:", coverage)

    remaining = 0
    if targets[-1] < latest_target:
        remaining = (latest_target - targets[-1]) // TIMEFRAME_MS
    print("Catch-up remaining after this run:", remaining)
    print("NEON WRITE + SEQUENTIAL COVERAGE: PASS")


if __name__ == "__main__":
    main()
