import argparse
import os

from ready_outcome_pilot import SYMBOLS
from scanner import get_data
from analysis import analyze_timeframe
from ready_engine import evaluate_ready_candidate
from market_regime_shadow import observe_ready


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
                candidate = evaluate_ready_candidate(
                    symbol,
                    "4h",
                    direction,
                    result,
                )
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

    if len(evaluations) != 50:
        raise SystemExit("FAIL: expected exactly 50 evaluations")
    if errors:
        raise SystemExit("FAIL: some symbols failed")
    if len(timestamps) != 1:
        raise SystemExit("FAIL: symbols are not aligned")

    if not args.write:
        print("DRY RUN: PASS")
        return

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("FAIL: DATABASE_URL is not configured")

    status_counts = {}

    for candidate in evaluations:
        result = observe_ready(
            candidate,
            database_url=database_url,
            db_path=None,
        )
        status = result.get("shadow_status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    print("Shadow statuses:", status_counts)
    print("NEON WRITE: PASS")


if __name__ == "__main__":
    main()
