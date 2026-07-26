from __future__ import annotations

import copy
import sqlite3
import tempfile
from pathlib import Path

from strategy_analytics import (
    build_analysis_record,
    count_analysis_records,
    create_scan_id,
    fetch_analysis_records,
    initialize_database,
    insert_analysis_record,
    insert_analysis_records,
)


def _temp_db_path() -> tuple[tempfile.TemporaryDirectory, Path]:
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "nested" / "strategy_analytics.db"
    return temp_dir, db_path


def _table_columns(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "PRAGMA table_info(analysis_records)"
        ).fetchall()
    return [str(row[1]) for row in rows]


def _create_old_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE analysis_records (
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
        connection.commit()


def test_database_creation() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        created_path = initialize_database(db_path)

        assert created_path == db_path
        assert db_path.exists()
        assert db_path.parent.exists()

        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'analysis_records'
                """
            ).fetchone()

        assert row is not None

        columns = _table_columns(db_path)
        for required in (
            "trigger_candle_ts",
            "cross_score",
            "distance_score",
            "body_score",
            "wick_score",
        ):
            assert required in columns
    finally:
        temp_dir.cleanup()


def test_migration_adds_new_columns_to_old_schema() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        _create_old_schema(db_path)

        before = _table_columns(db_path)
        assert "trigger_candle_ts" not in before

        initialize_database(db_path)

        after = _table_columns(db_path)
        assert "trigger_candle_ts" in after
        assert "cross_score" in after
        assert "distance_score" in after
        assert "body_score" in after
        assert "wick_score" in after
    finally:
        temp_dir.cleanup()


def test_migration_is_idempotent() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        _create_old_schema(db_path)

        initialize_database(db_path)
        first_cols = _table_columns(db_path)

        initialize_database(db_path)
        second_cols = _table_columns(db_path)

        assert first_cols == second_cols
    finally:
        temp_dir.cleanup()


def test_old_rows_remain_readable_and_new_columns_null_after_migration() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        _create_old_schema(db_path)

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                INSERT INTO analysis_records (
                    analysis_id, scan_id, timestamp_utc, symbol, timeframe, direction,
                    decision, decision_score, confidence, breakout_score,
                    trend_quality_score, volume_score, structure_score,
                    breakout_confirmed, structure_alignment, primary_blocker,
                    pipeline_stage, entry_price, scanner_version, pipeline_version,
                    decision_version
                ) VALUES (
                    ?,?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?
                )
                """,
                (
                    "old-id",
                    "old-scan",
                    "2026-07-24T00:00:00+00:00",
                    "BTC/USDT",
                    "1h",
                    "LONG",
                    "WATCH",
                    60.0,
                    55.0,
                    70.0,
                    65.0,
                    45.0,
                    35.0,
                    1,
                    "ALIGNED",
                    None,
                    "ok",
                    65000.0,
                    None,
                    None,
                    None,
                ),
            )
            connection.commit()

        initialize_database(db_path)
        rows = fetch_analysis_records(db_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["analysis_id"] == "old-id"
        assert row["trigger_candle_ts"] is None
        assert row["cross_score"] is None
        assert row["distance_score"] is None
        assert row["body_score"] is None
        assert row["wick_score"] is None
    finally:
        temp_dir.cleanup()


def test_scan_id_generation() -> None:
    first = create_scan_id()
    second = create_scan_id()

    assert isinstance(first, str)
    assert isinstance(second, str)
    assert first
    assert second
    assert first != second


def test_one_row_insertion_and_readback() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        scan_id = create_scan_id()
        record = build_analysis_record(
            scan_id=scan_id,
            symbol="BTC/USDT",
            timeframe="1h",
            direction="LONG",
            decision="WATCH",
            decision_score=31.25,
            confidence=56.75,
            breakout_score=73.5,
            trend_quality_score=61,
            volume_score=40,
            structure_score=30,
            breakout_confirmed=True,
            structure_alignment="NEUTRAL",
            primary_blocker=None,
            pipeline_stage="breakout_confirmation",
            entry_price=64000.0,
            trigger_candle_ts="2026-07-24T11:00:00+00:00",
            cross_score=30.0,
            distance_score=18.0,
            body_score=14.0,
            wick_score=6.0,
            scanner_version="0.6",
            pipeline_version="0.5",
            decision_version="1.0",
        )

        inserted = insert_analysis_record(db_path, record)

        assert inserted is True
        assert count_analysis_records(db_path) == 1

        rows = fetch_analysis_records(db_path)

        assert len(rows) == 1
        row = rows[0]

        assert row["scan_id"] == scan_id
        assert row["symbol"] == "BTC/USDT"
        assert row["timeframe"] == "1h"
        assert row["direction"] == "LONG"
        assert row["decision"] == "WATCH"
        assert row["breakout_confirmed"] == 1
        assert row["trigger_candle_ts"] == "2026-07-24T11:00:00+00:00"
        assert row["cross_score"] == 30.0
        assert row["distance_score"] == 18.0
        assert row["body_score"] == 14.0
        assert row["wick_score"] == 6.0
    finally:
        temp_dir.cleanup()


def test_duplicate_insertion_is_ignored() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        scan_id = create_scan_id()

        first_record = build_analysis_record(
            scan_id=scan_id,
            symbol="ETH/USDT",
            timeframe="4h",
            direction="SHORT",
            decision="SKIP",
        )

        duplicate_record = build_analysis_record(
            scan_id=scan_id,
            symbol="ETH/USDT",
            timeframe="4h",
            direction="SHORT",
            decision="TAKE",
        )

        first_inserted = insert_analysis_record(db_path, first_record)
        duplicate_inserted = insert_analysis_record(
            db_path,
            duplicate_record,
        )

        assert first_inserted is True
        assert duplicate_inserted is False
        assert count_analysis_records(db_path) == 1

        stored = fetch_analysis_records(db_path)[0]
        assert stored["decision"] == "SKIP"
    finally:
        temp_dir.cleanup()


def test_long_and_short_are_independent() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        scan_id = create_scan_id()

        records = [
            build_analysis_record(
                scan_id=scan_id,
                symbol="SOL/USDT",
                timeframe="1h",
                direction="LONG",
                decision="WATCH",
            ),
            build_analysis_record(
                scan_id=scan_id,
                symbol="SOL/USDT",
                timeframe="1h",
                direction="SHORT",
                decision="SKIP",
            ),
        ]

        inserted = insert_analysis_records(db_path, records)

        assert inserted == 2
        assert count_analysis_records(db_path) == 2

        rows = fetch_analysis_records(db_path)
        directions = {row["direction"] for row in rows}

        assert directions == {"LONG", "SHORT"}
        assert {row["scan_id"] for row in rows} == {scan_id}
    finally:
        temp_dir.cleanup()


def test_missing_optional_values_are_null() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        record = build_analysis_record(
            scan_id=create_scan_id(),
            symbol="XRP/USDT",
            timeframe="1d",
            direction="LONG",
        )

        inserted = insert_analysis_record(db_path, record)

        assert inserted is True

        stored = fetch_analysis_records(db_path)[0]

        assert stored["decision"] is None
        assert stored["decision_score"] is None
        assert stored["confidence"] is None
        assert stored["entry_price"] is None
        assert stored["primary_blocker"] is None
        assert stored["trigger_candle_ts"] is None
        assert stored["cross_score"] is None
        assert stored["distance_score"] is None
        assert stored["body_score"] is None
        assert stored["wick_score"] is None
    finally:
        temp_dir.cleanup()


def test_source_record_is_not_mutated() -> None:
    temp_dir, db_path = _temp_db_path()
    try:
        record = build_analysis_record(
            scan_id=create_scan_id(),
            symbol="AVAX/USDT",
            timeframe="1w",
            direction="SHORT",
            decision="SKIP",
            confidence=42.5,
        )
        original = copy.deepcopy(record)

        inserted = insert_analysis_record(db_path, record)

        assert inserted is True
        assert record == original
    finally:
        temp_dir.cleanup()


def run_tests() -> None:
    tests = [
        test_database_creation,
        test_migration_adds_new_columns_to_old_schema,
        test_migration_is_idempotent,
        test_old_rows_remain_readable_and_new_columns_null_after_migration,
        test_scan_id_generation,
        test_one_row_insertion_and_readback,
        test_duplicate_insertion_is_ignored,
        test_long_and_short_are_independent,
        test_missing_optional_values_are_null,
        test_source_record_is_not_mutated,
    ]

    passed = 0

    print("=" * 60)
    print("STRATEGY ANALYTICS TESTS")
    print("=" * 60)

    for test in tests:
        test()
        passed += 1
        print(f"✓ {test.__name__}")

    print("=" * 60)
    print(f"Passed {passed} / {len(tests)} tests")


if __name__ == "__main__":
    run_tests()
