from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from analytics_export import build_strategy_rows
from strategy_analytics import (
    DEFAULT_DB_PATH,
    insert_analysis_records,
)


def persist_completed_scan_analytics(
    all_results: Mapping[str, Any],
    *,
    scan_id: str,
    pipeline_version: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    """
    Persist one completed scan to Strategy Analytics.

    Never raises: scanner/UI flow must continue even if SQLite fails.
    """
    resolved_scan_id = str(scan_id)
    resolved_timestamp = (
        timestamp_utc or datetime.now(timezone.utc).isoformat()
    )

    result: dict[str, Any] = {
        "scan_id": resolved_scan_id,
        "timestamp_utc": resolved_timestamp,
        "inserted": 0,
        "rows_built": 0,
        "error": None,
    }

    try:
        rows = build_strategy_rows(
            all_results,
            scan_id=resolved_scan_id,
            pipeline_version=pipeline_version,
            timestamp_utc=resolved_timestamp,
        )
        result["rows_built"] = len(rows)
        result["inserted"] = int(
            insert_analysis_records(db_path, rows)
        )
    except Exception as exc:
        result["error"] = str(exc)

    return result
