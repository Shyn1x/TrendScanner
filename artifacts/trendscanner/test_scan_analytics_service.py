from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from unittest import mock

from scan_analytics_service import persist_completed_scan_analytics
from strategy_analytics import count_analysis_records, fetch_analysis_records


def _temp_db_path() -> tuple[tempfile.TemporaryDirectory, Path]:
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "nested" / "strategy_analytics.db"
    return temp_dir, db_path


def _sample_all_results() -> dict:
    return {
        "BTC/USDT": {
            "1h": {
                "quality": {
                    "LONG": {
                        "confidence": {"confidence": 70.0},
                        "trend_quality": {"score": 72.0},
                        "volume_quality": {"score": 45.0},
                        "breakout_quality": {
                            "score": 88.0,
                            "confirmed": True,
                            "signal_close": 101.0,
                        },
                        "structure_quality": {
                            "score": 35.0,
                            "alignment": "ALIGNED",
                        },
                    },
                    "SHORT": {
                        "confidence": {"confidence": 30.0},
                        "trend_quality": {"score": 40.0},
                        "volume_quality": {"score": 20.0},
                        "breakout_quality": {
                            "score": 10.0,
                            "confirmed": False,
                            "signal_close": 101.0,
                        },
                        "structure_quality": {
                            "score": 10.0,
                            "alignment": "OPPOSED",
                        },
                    },
                    "FINAL": {},
                },
                "decision_details": {
                    "LONG": {
                        "decision": "TAKE",
                        "decision_score": 78.0,
                        "confidence": 70.0,
                        "breakout_confirmed": True,
                        "blockers": [],
                    },
                    "SHORT": {
                        "decision": "SKIP",
                        "decision_score": 8.0,
                        "confidence": 30.0,
                        "breakout_confirmed": False,
                        "blockers": [
                            {
                                "code": "BREAKOUT_NOT_CONFIRMED",
                                "message": "Breakout is not confirmed",
                            }
                        ],
                    },
                    "FINAL": {},
                },
            },
            "FINAL": {},
        }
    }


def test_persist_completed_scan_analytics_is_idempotent_per_scan_id() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        all_results = _sample_all_results()

        first = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-fixed",
            timestamp_utc="2026-07-24T00:00:00+00:00",
        )
        second = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-fixed",
            timestamp_utc="2026-07-24T00:00:00+00:00",
        )

        assert first["error"] is None
        assert second["error"] is None
        assert first["inserted"] == 2
        assert second["inserted"] == 0

        assert count_analysis_records(db_path) == 2
        rows = fetch_analysis_records(db_path)
        assert {row["direction"] for row in rows} == {"LONG", "SHORT"}
        assert {row["scan_id"] for row in rows} == {"scan-fixed"}
    finally:
        temp_dir.cleanup()


def test_widget_rerun_with_same_scan_id_adds_zero_rows() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        all_results = _sample_all_results()

        first = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-rerun",
        )
        rerun = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-rerun",
        )

        assert first["inserted"] == 2
        assert rerun["inserted"] == 0
        assert count_analysis_records(db_path) == 2
    finally:
        temp_dir.cleanup()


def test_two_different_scan_ids_insert_two_independent_scans() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        all_results = _sample_all_results()

        first = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-a",
        )
        second = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-b",
        )

        assert first["inserted"] == 2
        assert second["inserted"] == 2
        assert count_analysis_records(db_path) == 4

        rows = fetch_analysis_records(db_path)
        assert {row["scan_id"] for row in rows} == {"scan-a", "scan-b"}
    finally:
        temp_dir.cleanup()


def test_manual_refresh_new_scan_id_adds_one_complete_batch() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        all_results = _sample_all_results()

        initial = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-initial",
        )
        refreshed = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-refresh",
        )

        assert initial["inserted"] == 2
        assert refreshed["inserted"] == 2
        assert count_analysis_records(db_path) == 4
    finally:
        temp_dir.cleanup()


def test_all_rows_from_one_batch_share_one_scan_id() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        all_results = _sample_all_results()

        result = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=db_path,
            scan_id="scan-single-batch",
        )

        assert result["inserted"] == 2
        rows = fetch_analysis_records(db_path)
        assert len(rows) == 2
        assert {row["scan_id"] for row in rows} == {"scan-single-batch"}
    finally:
        temp_dir.cleanup()


def test_persist_completed_scan_analytics_never_raises_on_sqlite_failure() -> None:
    all_results = _sample_all_results()
    original = copy.deepcopy(all_results)

    with mock.patch(
        "scan_analytics_service.insert_analysis_records",
        side_effect=RuntimeError("sqlite write failed"),
    ):
        result = persist_completed_scan_analytics(
            all_results,
            pipeline_version="0.5",
            db_path=":memory:",
            scan_id="scan-fail",
            timestamp_utc="2026-07-24T00:00:00+00:00",
        )

    assert result["scan_id"] == "scan-fail"
    assert result["inserted"] == 0
    assert result["error"] is not None
    assert "sqlite write failed" in result["error"]
    assert all_results == original
