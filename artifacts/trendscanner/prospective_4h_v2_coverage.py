"""Strict sequential coverage journal for the repaired 4h prospective series."""
from datetime import datetime, timezone

from market_regime_shadow_v2 import SHADOW_VERSION
from prospective_coverage import COVERAGE_TABLE, EXPECTED_EVALUATIONS, TIMEFRAME_MS, CoverageError, _ensure_table
from shadow_storage import _connect_postgres


def latest_coverage_timestamp(database_url: str) -> int | None:
    with _connect_postgres(database_url) as connection:
        with connection.cursor() as cur:
            _ensure_table(cur)
            cur.execute(
                f"""SELECT ready_timestamp FROM {COVERAGE_TABLE}
                    WHERE shadow_version=%s AND timeframe='4h'
                    ORDER BY ready_timestamp DESC LIMIT 1""",
                (SHADOW_VERSION,),
            )
            row = cur.fetchone()
            return None if row is None else int(row[0])


def record_complete_candle(database_url: str, *, ready_timestamp: int,
                           observed_evaluations: int) -> dict:
    if isinstance(ready_timestamp, bool) or not isinstance(ready_timestamp, int):
        raise CoverageError("INVALID_COVERAGE_TIMESTAMP")
    if ready_timestamp < 0 or ready_timestamp % TIMEFRAME_MS:
        raise CoverageError("UNALIGNED_COVERAGE_TIMESTAMP")
    if observed_evaluations != EXPECTED_EVALUATIONS:
        raise CoverageError("INCOMPLETE_CANDLE_EVALUATIONS")

    with _connect_postgres(database_url) as connection:
        with connection.cursor() as cur:
            _ensure_table(cur)
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"{SHADOW_VERSION}:4h:coverage",),
            )
            cur.execute(
                f"""SELECT ready_timestamp FROM {COVERAGE_TABLE}
                    WHERE shadow_version=%s AND timeframe='4h'
                    ORDER BY ready_timestamp DESC LIMIT 1""",
                (SHADOW_VERSION,),
            )
            row = cur.fetchone()
            previous = None if row is None else int(row[0])

            if previous is not None:
                if ready_timestamp == previous:
                    return {
                        "status": "DUPLICATE",
                        "ready_timestamp": ready_timestamp,
                        "previous_timestamp": previous,
                        "gap_candles": 0,
                        "total_gap_candles": 0,
                    }
                if ready_timestamp < previous:
                    raise CoverageError("STALE_COVERAGE_TIMESTAMP")
                if ready_timestamp - previous != TIMEFRAME_MS:
                    raise CoverageError("NONSEQUENTIAL_COVERAGE_TIMESTAMP")

            cur.execute(
                f"""INSERT INTO {COVERAGE_TABLE}
                    (shadow_version, timeframe, ready_timestamp,
                     expected_evaluations, observed_evaluations,
                     previous_timestamp, gap_candles, source, recorded_at)
                    VALUES (%s, '4h', %s, %s, %s, %s, 0, %s, %s)
                    ON CONFLICT DO NOTHING""",
                (
                    SHADOW_VERSION,
                    ready_timestamp,
                    EXPECTED_EVALUATIONS,
                    observed_evaluations,
                    previous,
                    "sequential_v2_baseline" if previous is None else "sequential_v2_collector",
                    datetime.now(timezone.utc),
                ),
            )
            return {
                "status": "BASELINE" if previous is None else "COMPLETE",
                "ready_timestamp": ready_timestamp,
                "previous_timestamp": previous,
                "gap_candles": 0,
                "total_gap_candles": 0,
            }
