"""Durable coverage journal for the frozen 4h prospective experiment.

A coverage row is written only after all expected READY observations for one
closed 4h candle have been accepted by the shadow storage path. This lets the
collector detect missing candles instead of silently continuing across gaps.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ready_outcome_pilot import SYMBOLS
from shadow_storage import STATE_TABLE, _connect_postgres

COVERAGE_TABLE = "market_regime_shadow_coverage"
TIMEFRAME_MS = 14_400_000
EXPECTED_DIRECTIONS = ("LONG", "SHORT")
EXPECTED_EVALUATIONS = len(SYMBOLS) * len(EXPECTED_DIRECTIONS)


class CoverageError(RuntimeError):
    pass


def _validate_timestamp(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageError("INVALID_COVERAGE_TIMESTAMP")
    if value % TIMEFRAME_MS:
        raise CoverageError("UNALIGNED_COVERAGE_TIMESTAMP")
    return value


def _ensure_table(cur) -> None:
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
        shadow_version TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        ready_timestamp BIGINT NOT NULL,
        expected_evaluations INTEGER NOT NULL,
        observed_evaluations INTEGER NOT NULL,
        previous_timestamp BIGINT,
        gap_candles INTEGER NOT NULL,
        source TEXT NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (shadow_version, timeframe, ready_timestamp)
    )""")


def _total_gaps(cur, shadow_version: str, timeframe: str) -> int:
    cur.execute(f"""SELECT COALESCE(SUM(gap_candles), 0) FROM {COVERAGE_TABLE}
        WHERE shadow_version=%s AND timeframe=%s""", (shadow_version, timeframe))
    return int(cur.fetchone()[0])


def bootstrap_from_shadow_state(database_url: str, *, shadow_version: str,
                                timeframe: str = "4h") -> dict:
    """Seed coverage from the already-established 50-row shadow baseline.

    This is only used when the coverage table is empty. It requires the exact
    frozen 25-symbol x 2-direction state population to exist and to share one
    last_timestamp; otherwise collection fails rather than guessing.
    """
    expected_ids = {(symbol, direction) for symbol in SYMBOLS for direction in EXPECTED_DIRECTIONS}

    with _connect_postgres(database_url) as connection:
        with connection.cursor() as cur:
            _ensure_table(cur)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"{shadow_version}:{timeframe}:coverage",))
            cur.execute(f"""SELECT ready_timestamp FROM {COVERAGE_TABLE}
                WHERE shadow_version=%s AND timeframe=%s
                ORDER BY ready_timestamp DESC LIMIT 1""", (shadow_version, timeframe))
            existing = cur.fetchone()
            if existing is not None:
                return {"status": "EXISTS", "ready_timestamp": int(existing[0]),
                        "total_gap_candles": _total_gaps(cur, shadow_version, timeframe)}

            cur.execute(f"""SELECT symbol, direction, last_timestamp FROM {STATE_TABLE}
                WHERE shadow_version=%s AND timeframe=%s""", (shadow_version, timeframe))
            rows = cur.fetchall()
            state = {(str(symbol), str(direction)): int(timestamp)
                     for symbol, direction, timestamp in rows
                     if (str(symbol), str(direction)) in expected_ids}

            if set(state) != expected_ids:
                missing = len(expected_ids - set(state))
                raise CoverageError(f"INCOMPLETE_BASELINE_STATE:{missing}")

            timestamps = set(state.values())
            if len(timestamps) != 1:
                raise CoverageError("BASELINE_STATE_NOT_ALIGNED")

            timestamp = _validate_timestamp(timestamps.pop())
            cur.execute(f"""INSERT INTO {COVERAGE_TABLE}
                (shadow_version, timeframe, ready_timestamp, expected_evaluations,
                 observed_evaluations, previous_timestamp, gap_candles, source, recorded_at)
                VALUES (%s, %s, %s, %s, %s, NULL, 0, 'state_bootstrap', %s)
                ON CONFLICT DO NOTHING""",
                (shadow_version, timeframe, timestamp, EXPECTED_EVALUATIONS,
                 EXPECTED_EVALUATIONS, datetime.now(timezone.utc)))
            return {"status": "BOOTSTRAPPED", "ready_timestamp": timestamp,
                    "total_gap_candles": 0}


def record_complete_candle(database_url: str, *, shadow_version: str,
                           ready_timestamp: int, observed_evaluations: int,
                           timeframe: str = "4h") -> dict:
    """Append one complete candle and report current and historical gaps."""
    timestamp = _validate_timestamp(ready_timestamp)
    if observed_evaluations != EXPECTED_EVALUATIONS:
        raise CoverageError("INCOMPLETE_CANDLE_EVALUATIONS")

    with _connect_postgres(database_url) as connection:
        with connection.cursor() as cur:
            _ensure_table(cur)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"{shadow_version}:{timeframe}:coverage",))
            cur.execute(f"""SELECT ready_timestamp, gap_candles FROM {COVERAGE_TABLE}
                WHERE shadow_version=%s AND timeframe=%s
                ORDER BY ready_timestamp DESC LIMIT 1""", (shadow_version, timeframe))
            row = cur.fetchone()
            previous = int(row[0]) if row is not None else None

            if previous is None:
                raise CoverageError("COVERAGE_NOT_BOOTSTRAPPED")
            if timestamp == previous:
                current_gap = int(row[1])
                total_gaps = _total_gaps(cur, shadow_version, timeframe)
                return {"status": "GAP" if current_gap else "DUPLICATE",
                        "ready_timestamp": timestamp,
                        "previous_timestamp": previous,
                        "gap_candles": current_gap,
                        "total_gap_candles": total_gaps}
            if timestamp < previous:
                raise CoverageError("STALE_COVERAGE_TIMESTAMP")

            delta = timestamp - previous
            if delta % TIMEFRAME_MS:
                raise CoverageError("NONCONTIGUOUS_TIME_GRID")
            gap_candles = delta // TIMEFRAME_MS - 1

            cur.execute(f"""INSERT INTO {COVERAGE_TABLE}
                (shadow_version, timeframe, ready_timestamp, expected_evaluations,
                 observed_evaluations, previous_timestamp, gap_candles, source, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'collector', %s)
                ON CONFLICT DO NOTHING""",
                (shadow_version, timeframe, timestamp, EXPECTED_EVALUATIONS,
                 observed_evaluations, previous, gap_candles,
                 datetime.now(timezone.utc)))

            total_gaps = _total_gaps(cur, shadow_version, timeframe)
            return {"status": "GAP" if gap_candles else "COMPLETE",
                    "ready_timestamp": timestamp,
                    "previous_timestamp": previous,
                    "gap_candles": int(gap_candles),
                    "total_gap_candles": total_gaps}
