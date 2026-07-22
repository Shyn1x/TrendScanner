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
