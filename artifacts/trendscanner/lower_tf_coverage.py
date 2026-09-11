"""Coverage journal for the separate 1h/15m prospective experiment."""
from __future__ import annotations

from datetime import datetime, timezone

from lower_tf_shadow import EXPERIMENT_VERSION, TIMEFRAME_MS
from lower_tf_storage import STATE_TABLE, _connect_postgres
from ready_outcome_pilot import SYMBOLS

COVERAGE_TABLE = "lower_tf_prospective_coverage"
EXPECTED_DIRECTIONS = ("LONG", "SHORT")
EXPECTED_EVALUATIONS = len(SYMBOLS) * len(EXPECTED_DIRECTIONS)


class CoverageError(RuntimeError):
    pass


def _validate_timestamp(value: int, timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise CoverageError("INVALID_TIMEFRAME")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageError("INVALID_COVERAGE_TIMESTAMP")
    if value % TIMEFRAME_MS[timeframe]:
        raise CoverageError("UNALIGNED_COVERAGE_TIMESTAMP")
    return value


def _ensure_table(cur) -> None:
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
        experiment_version TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        ready_timestamp BIGINT NOT NULL,
        expected_evaluations INTEGER NOT NULL,
        observed_evaluations INTEGER NOT NULL,
        previous_timestamp BIGINT,
        gap_candles INTEGER NOT NULL,
        source TEXT NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (experiment_version, timeframe, ready_timestamp)
    )""")


def _total_gaps(cur, experiment_version: str, timeframe: str) -> int:
    cur.execute(
        f"SELECT COALESCE(SUM(gap_candles), 0) FROM {COVERAGE_TABLE} "
        "WHERE experiment_version=%s AND timeframe=%s",
        (experiment_version, timeframe),
    )
    return int(cur.fetchone()[0])


def _bootstrap_from_state(cur, *, experiment_version: str, timeframe: str,
                          ready_timestamp: int) -> dict:
    expected_ids = {
        (symbol, direction)
        for symbol in SYMBOLS
        for direction in EXPECTED_DIRECTIONS
    }
    cur.execute(
        f"SELECT symbol, direction, last_timestamp FROM {STATE_TABLE} "
        "WHERE experiment_version=%s AND timeframe=%s",
        (experiment_version, timeframe),
    )
    rows = cur.fetchall()
    actual_ids = {(str(symbol), str(direction)) for symbol, direction, _ in rows}
    if actual_ids != expected_ids:
        missing = len(expected_ids - actual_ids)
        extra = len(actual_ids - expected_ids)
        raise CoverageError(f"INCOMPLETE_BASELINE_STATE:missing={missing},extra={extra}")

    timestamps = {int(timestamp) for _, _, timestamp in rows}
    if timestamps != {ready_timestamp}:
        raise CoverageError("BASELINE_STATE_NOT_ALIGNED")

    cur.execute(
        f"""INSERT INTO {COVERAGE_TABLE}
        (experiment_version, timeframe, ready_timestamp, expected_evaluations,
         observed_evaluations, previous_timestamp, gap_candles, source, recorded_at)
        VALUES (%s, %s, %s, %s, %s, NULL, 0, 'state_bootstrap', %s)
        ON CONFLICT DO NOTHING""",
        (
            experiment_version,
            timeframe,
            ready_timestamp,
            EXPECTED_EVALUATIONS,
            EXPECTED_EVALUATIONS,
            datetime.now(timezone.utc),
        ),
    )
    return {
        "status": "BOOTSTRAPPED",
        "ready_timestamp": ready_timestamp,
        "gap_candles": 0,
        "total_gap_candles": 0,
    }


def record_complete_candle(database_url: str, *, timeframe: str,
                           ready_timestamp: int, observed_evaluations: int,
                           experiment_version: str = EXPERIMENT_VERSION) -> dict:
    timestamp = _validate_timestamp(ready_timestamp, timeframe)
    if observed_evaluations != EXPECTED_EVALUATIONS:
        raise CoverageError("INCOMPLETE_CANDLE_EVALUATIONS")

    with _connect_postgres(database_url) as connection:
        with connection.cursor() as cur:
            _ensure_table(cur)
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"{experiment_version}:{timeframe}:lower_tf_coverage",),
            )
            cur.execute(
                f"SELECT ready_timestamp, gap_candles FROM {COVERAGE_TABLE} "
                "WHERE experiment_version=%s AND timeframe=%s "
                "ORDER BY ready_timestamp DESC LIMIT 1",
                (experiment_version, timeframe),
            )
            row = cur.fetchone()

            if row is None:
                return _bootstrap_from_state(
                    cur,
                    experiment_version=experiment_version,
                    timeframe=timeframe,
                    ready_timestamp=timestamp,
                )

            previous = int(row[0])
            if timestamp == previous:
                current_gap = int(row[1])
                return {
                    "status": "GAP" if current_gap else "DUPLICATE",
                    "ready_timestamp": timestamp,
                    "previous_timestamp": previous,
                    "gap_candles": current_gap,
                    "total_gap_candles": _total_gaps(cur, experiment_version, timeframe),
                }
            if timestamp < previous:
                raise CoverageError("STALE_COVERAGE_TIMESTAMP")

            tf_ms = TIMEFRAME_MS[timeframe]
            delta = timestamp - previous
            if delta % tf_ms:
                raise CoverageError("NONCONTIGUOUS_TIME_GRID")
            gap_candles = delta // tf_ms - 1

            cur.execute(
                f"""INSERT INTO {COVERAGE_TABLE}
                (experiment_version, timeframe, ready_timestamp, expected_evaluations,
                 observed_evaluations, previous_timestamp, gap_candles, source, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'collector', %s)
                ON CONFLICT DO NOTHING""",
                (
                    experiment_version,
                    timeframe,
                    timestamp,
                    EXPECTED_EVALUATIONS,
                    observed_evaluations,
                    previous,
                    int(gap_candles),
                    datetime.now(timezone.utc),
                ),
            )
            total_gaps = _total_gaps(cur, experiment_version, timeframe)
            return {
                "status": "GAP" if gap_candles else "COMPLETE",
                "ready_timestamp": timestamp,
                "previous_timestamp": previous,
                "gap_candles": int(gap_candles),
                "total_gap_candles": total_gaps,
            }
