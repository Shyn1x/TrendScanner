import argparse
import os

from ready_outcome_pilot import SYMBOLS
from scanner import exchange, get_data
from analysis import analyze_timeframe
from ready_engine import evaluate_ready_candidate
from market_regime_shadow import (
    SHADOW_VERSION,
    TIMEFRAME_MS,
    _PROCESS_CONTEXT,
    observe_ready,
)
from prospective_coverage import bootstrap_from_shadow_state, record_complete_candle

EXPECTED_EVALUATIONS = 50
SAFE_SHADOW_STATUSES = frozenset({"NOT_READY", "NO_TRANSITION", "OK"})


def _expected_closed_timestamp(now_ms: int) -> int:
    return (int(now_ms) // TIMEFRAME_MS) * TIMEFRAME_MS - TIMEFRAME_MS


def _bad_shadow_statuses(status_counts: dict) -> dict:
    return {
        status: count
        for status, count in status_counts.items()
        if status not in SAFE_SHADOW_STATUSES
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    assert len(SYMBOLS) == 25, f"Expected 25 research symbols, got {len(SYMBOLS)}"

    evaluations = []
    errors = []

    for symbol in SYMBOLS:
        try:
            df = get_data(symbol, "4h")
            result = analyze_timeframe(df)

            for direction in ("LONG", "SHORT"):
                available = (
                    result.get("quality", {})
                    .get(direction, {})
                    .get("analysis_available")
                )

                if available is not True:
                    errors.append((symbol, direction, "ANALYSIS_UNAVAILABLE"))
                    continue

                candidate = evaluate_ready_candidate(
                    symbol,
                    "4h",
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
            errors.append((symbol, type(exc).__name__))

    timestamps = sorted({
        c["ready_timestamp"]
        for c in evaluations
        if isinstance(c.get("ready_timestamp"), int)
    })

    ready_true = sum(c.get("ready") is True for c in evaluations)
    ready_false = sum(c.get("ready") is False for c in evaluations)

    print("Symbols expected:", len(SYMBOLS))
    print("Evaluations:", len(evaluations))
    print("READY True:", ready_true)
    print("READY False:", ready_false)
    print("Unique timestamps:", timestamps)
    print("Errors:", errors)

    if len(evaluations) != EXPECTED_EVALUATIONS:
        raise SystemExit("FAIL: expected exactly 50 evaluations")
    if errors:
        raise SystemExit("FAIL: some symbols failed")
    if len(timestamps) != 1:
        raise SystemExit("FAIL: symbols are not aligned")

    expected_timestamp = _expected_closed_timestamp(exchange.milliseconds())
    if timestamps[0] != expected_timestamp:
        raise SystemExit(
            f"FAIL: stale or premature 4h candle: got {timestamps[0]}, expected {expected_timestamp}"
        )

    if not args.write:
        print("DRY RUN: PASS")
        return

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("FAIL: DATABASE_URL is not configured")

    baseline = bootstrap_from_shadow_state(
        database_url,
        shadow_version=SHADOW_VERSION,
        timeframe="4h",
    )
    print("Coverage bootstrap:", baseline)

    # A READY=True event needs the exact 4h market snapshot. Validate it before
    # any shadow write so a transient context failure cannot become an
    # immutable UNAVAILABLE event.
    if ready_true:
        snapshot = _PROCESS_CONTEXT.snapshot()
        market = snapshot.get("market", {}) if isinstance(snapshot, dict) else {}
        if (
            snapshot.get("shadow_status") != "OK"
            or market.get("target_4h_timestamp") != timestamps[0]
        ):
            raise SystemExit("FAIL: market context unavailable for READY=True candle")

    status_counts = {}

    for candidate in evaluations:
        result = observe_ready(
            candidate,
            context=_PROCESS_CONTEXT,
            database_url=database_url,
            db_path=None,
        )
        status = result.get("shadow_status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    print("Shadow statuses:", status_counts)

    bad_statuses = _bad_shadow_statuses(status_counts)
    if bad_statuses:
        raise SystemExit(f"FAIL: shadow persistence unavailable: {bad_statuses}")

    if sum(status_counts.values()) != EXPECTED_EVALUATIONS:
        raise SystemExit("FAIL: not all shadow observations returned a status")

    coverage = record_complete_candle(
        database_url,
        shadow_version=SHADOW_VERSION,
        ready_timestamp=timestamps[0],
        observed_evaluations=EXPECTED_EVALUATIONS,
        timeframe="4h",
    )
    print("Coverage:", coverage)

    if coverage.get("status") == "GAP":
        raise SystemExit(
            f"FAIL: prospective coverage gap: {coverage.get('gap_candles')} missing 4h candle(s)"
        )

    print("NEON WRITE + COVERAGE: PASS")


if __name__ == "__main__":
    main()
