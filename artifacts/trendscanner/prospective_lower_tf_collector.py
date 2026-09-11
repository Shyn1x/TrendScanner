import argparse
import os

from analysis import analyze_timeframe
from lower_tf_coverage import record_complete_candle
from lower_tf_shadow import EXPERIMENT_VERSION, TIMEFRAME_MS, observe_lower_tf
from ready_engine import evaluate_ready_candidate
from ready_outcome_pilot import SYMBOLS
from scanner import exchange, get_data

TIMEFRAMES = ("1h", "15m")
EXPECTED_BARS = 200
EXPECTED_EVALUATIONS = 50
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


def _validate_frame(df, timeframe, symbol):
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


def collect(timeframe):
    if timeframe not in TIMEFRAMES:
        raise ValueError("UNSUPPORTED_LOWER_TIMEFRAME")

    assert len(SYMBOLS) == 25, f"Expected 25 research symbols, got {len(SYMBOLS)}"
    started_ms = int(exchange.milliseconds())
    evaluations = []
    errors = []

    for symbol in SYMBOLS:
        try:
            df = get_data(symbol, timeframe)
            _validate_frame(df, timeframe, symbol)
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
                if type(candidate.get("ready")) is not bool:
                    errors.append((symbol, direction, "INVALID_READY"))
                    continue
                if type(candidate.get("ready_timestamp")) is not int:
                    errors.append((symbol, direction, "INVALID_TIMESTAMP"))
                    continue
                evaluations.append(candidate)
        except Exception as exc:
            errors.append((symbol, type(exc).__name__, str(exc)[:100]))

    timestamps = sorted({c["ready_timestamp"] for c in evaluations})
    tf_ms = TIMEFRAME_MS[timeframe]
    expected_timestamp = (started_ms // tf_ms) * tf_ms - tf_ms

    return {
        "evaluations": evaluations,
        "errors": errors,
        "timestamps": timestamps,
        "expected_timestamp": expected_timestamp,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", required=True, choices=TIMEFRAMES)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    batch = collect(args.timeframe)
    evaluations = batch["evaluations"]
    errors = batch["errors"]
    timestamps = batch["timestamps"]
    expected_timestamp = batch["expected_timestamp"]

    ready_true = sum(c["ready"] is True for c in evaluations)
    ready_false = sum(c["ready"] is False for c in evaluations)

    print("Experiment:", EXPERIMENT_VERSION)
    print("Timeframe:", args.timeframe)
    print("Symbols expected:", len(SYMBOLS))
    print("Bars per symbol:", EXPECTED_BARS)
    print("Evaluations:", len(evaluations))
    print("READY True:", ready_true)
    print("READY False:", ready_false)
    print("Unique timestamps:", timestamps)
    print("Expected timestamp:", expected_timestamp)
    print("Errors:", errors)

    if len(evaluations) != EXPECTED_EVALUATIONS:
        raise SystemExit("FAIL: expected exactly 50 evaluations")
    if errors:
        raise SystemExit("FAIL: some symbols failed")
    if timestamps != [expected_timestamp]:
        raise SystemExit("FAIL: lower-timeframe candles are stale or not aligned")

    if not args.write:
        print("DRY RUN: PASS")
        return

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("FAIL: DATABASE_URL is not configured")

    status_counts = {}
    failures = []
    for candidate in evaluations:
        result = observe_lower_tf(
            candidate,
            database_url=database_url,
            db_path=None,
        )
        status = result.get("shadow_status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in SAFE_STATUSES:
            failures.append((candidate["symbol"], candidate["direction"], status))

    print("Shadow statuses:", status_counts)
    if failures:
        print("Write failures:", failures)
        raise SystemExit("FAIL: lower-timeframe storage reported unavailable/unknown results")
    if sum(status_counts.values()) != EXPECTED_EVALUATIONS:
        raise SystemExit("FAIL: not all observations returned a status")

    coverage = record_complete_candle(
        database_url,
        timeframe=args.timeframe,
        ready_timestamp=timestamps[0],
        observed_evaluations=EXPECTED_EVALUATIONS,
        experiment_version=EXPERIMENT_VERSION,
    )
    print("Coverage:", coverage)

    total_gaps = int(coverage.get("total_gap_candles", 0) or 0)
    if total_gaps:
        raise SystemExit(
            f"FAIL: lower-timeframe coverage contains {total_gaps} missing candle(s)"
        )

    print("NEON WRITE + COVERAGE: PASS")


if __name__ == "__main__":
    main()
