from __future__ import annotations

import math
import sqlite3
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "strategy_analytics.db"
)

ANALYSIS_COLUMNS = (
    "analysis_id",
    "scan_id",
    "timestamp_utc",
    "symbol",
    "timeframe",
    "direction",
    "decision",
    "decision_score",
    "confidence",
    "breakout_score",
    "trend_quality_score",
    "volume_score",
    "structure_score",
    "breakout_confirmed",
    "structure_alignment",
    "primary_blocker",
    "pipeline_stage",
    "entry_price",
    "scanner_version",
    "pipeline_version",
    "decision_version",
)


def _utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _safe_number(value: Any) -> float | None:
    """Convert a finite numeric value to float; otherwise return None."""
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def _safe_bool_for_sqlite(value: Any) -> int | None:
    """Convert a boolean-like value to SQLite INTEGER."""
    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if value in (0, 1):
        return int(value)

    return None


def create_scan_id() -> str:
    """Create one ID to be shared by every row from one full scan."""
    return str(uuid.uuid4())


def initialize_database(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> Path:
    """Create the database and immutable analysis_records table."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_records (
                analysis_id TEXT PRIMARY KEY,
                scan_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL
                    CHECK(direction IN ('LONG', 'SHORT')),

                decision TEXT,
                decision_score REAL,
                confidence REAL,

                breakout_score REAL,
                trend_quality_score REAL,
                volume_score REAL,
                structure_score REAL,

                breakout_confirmed INTEGER
                    CHECK(
                        breakout_confirmed IS NULL
                        OR breakout_confirmed IN (0, 1)
                    ),

                structure_alignment TEXT,
                primary_blocker TEXT,
                pipeline_stage TEXT,
                entry_price REAL,

                scanner_version TEXT,
                pipeline_version TEXT,
                decision_version TEXT,

                UNIQUE(scan_id, symbol, timeframe, direction)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_scan_id
            ON analysis_records(scan_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_symbol_timeframe
            ON analysis_records(symbol, timeframe)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_decision
            ON analysis_records(decision)
            """
        )

        connection.commit()

    return path


def build_analysis_record(
    *,
    scan_id: str,
    symbol: str,
    timeframe: str,
    direction: str,
    decision: str | None = None,
    decision_score: Any = None,
    confidence: Any = None,
    breakout_score: Any = None,
    trend_quality_score: Any = None,
    volume_score: Any = None,
    structure_score: Any = None,
    breakout_confirmed: Any = None,
    structure_alignment: str | None = None,
    primary_blocker: str | None = None,
    pipeline_stage: str | None = None,
    entry_price: Any = None,
    scanner_version: str | None = None,
    pipeline_version: str | None = None,
    decision_version: str | None = None,
    timestamp_utc: str | None = None,
    analysis_id: str | None = None,
) -> dict[str, Any]:
    """Build one immutable directional-analysis record."""
    normalized_direction = str(direction).upper()

    if normalized_direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    if not scan_id:
        raise ValueError("scan_id is required")

    if not symbol:
        raise ValueError("symbol is required")

    if not timeframe:
        raise ValueError("timeframe is required")

    return {
        "analysis_id": analysis_id or str(uuid.uuid4()),
        "scan_id": str(scan_id),
        "timestamp_utc": timestamp_utc or _utc_timestamp(),
        "symbol": str(symbol),
        "timeframe": str(timeframe),
        "direction": normalized_direction,
        "decision": str(decision) if decision is not None else None,
        "decision_score": _safe_number(decision_score),
        "confidence": _safe_number(confidence),
        "breakout_score": _safe_number(breakout_score),
        "trend_quality_score": _safe_number(trend_quality_score),
        "volume_score": _safe_number(volume_score),
        "structure_score": _safe_number(structure_score),
        "breakout_confirmed": _safe_bool_for_sqlite(
            breakout_confirmed
        ),
        "structure_alignment": (
            str(structure_alignment)
            if structure_alignment is not None
            else None
        ),
        "primary_blocker": (
            str(primary_blocker)
            if primary_blocker is not None
            else None
        ),
        "pipeline_stage": (
            str(pipeline_stage)
            if pipeline_stage is not None
            else None
        ),
        "entry_price": _safe_number(entry_price),
        "scanner_version": (
            str(scanner_version)
            if scanner_version is not None
            else None
        ),
        "pipeline_version": (
            str(pipeline_version)
            if pipeline_version is not None
            else None
        ),
        "decision_version": (
            str(decision_version)
            if decision_version is not None
            else None
        ),
    }


def _prepare_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy a record without mutating its source."""
    copied = deepcopy(dict(record))

    missing = [
        column
        for column in ANALYSIS_COLUMNS
        if column not in copied
    ]
    if missing:
        raise ValueError(
            "Missing analysis record columns: "
            + ", ".join(missing)
        )

    return {
        column: copied.get(column)
        for column in ANALYSIS_COLUMNS
    }


def insert_analysis_record(
    db_path: str | Path,
    record: Mapping[str, Any],
) -> bool:
    """
    Insert one record.

    Returns True when inserted and False when ignored as a duplicate.
    """
    return insert_analysis_records(db_path, [record]) == 1


def insert_analysis_records(
    db_path: str | Path,
    records: Iterable[Mapping[str, Any]],
) -> int:
    """
    Insert records in one transaction.

    Duplicate scan/symbol/timeframe/direction rows are ignored.
    """
    path = initialize_database(db_path)
    prepared = [_prepare_record(record) for record in records]

    if not prepared:
        return 0

    placeholders = ", ".join(
        f":{column}" for column in ANALYSIS_COLUMNS
    )
    columns_sql = ", ".join(ANALYSIS_COLUMNS)

    query = f"""
        INSERT OR IGNORE INTO analysis_records (
            {columns_sql}
        ) VALUES (
            {placeholders}
        )
    """

    with sqlite3.connect(path) as connection:
        before = connection.total_changes
        connection.executemany(query, prepared)
        connection.commit()
        inserted = connection.total_changes - before

    return int(inserted)


def count_analysis_records(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Return the number of stored analysis records."""
    path = initialize_database(db_path)

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM analysis_records"
        ).fetchone()

    return int(row[0]) if row else 0


def fetch_analysis_records(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Read stored records for diagnostics and tests."""
    path = initialize_database(db_path)

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM analysis_records
            ORDER BY timestamp_utc, symbol, timeframe, direction
            """
        ).fetchall()

    return [dict(row) for row in rows]
