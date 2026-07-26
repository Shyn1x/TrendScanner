from __future__ import annotations

import copy
import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from historical_shadow_validator import (
    HORIZONS,
    build_event_key,
    build_historical_shadow_report,
    calculate_forward_outcomes,
    classify_outcome_by_context,
    deduplicate_shadow_events,
    fetch_historical_candles_since,
    locate_trigger_candle,
    reconstruct_trigger_candle_timestamp,
    resolve_trigger_candle_timestamp,
    split_closed_candles,
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _candles_1h(start_iso: str, closes: list[float], highs: list[float], lows: list[float]) -> list[list[float]]:
    start = _dt(start_iso)
    out: list[list[float]] = []
    for idx, close in enumerate(closes):
        ts = int((start.timestamp() + idx * 3600) * 1000)
        out.append([ts, close, highs[idx], lows[idx], close, 100.0])
    return out


def _schema_sql() -> str:
    return """
    CREATE TABLE analysis_records (
        analysis_id TEXT PRIMARY KEY,
        scan_id TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        direction TEXT NOT NULL,
        decision TEXT,
        decision_score REAL,
        confidence REAL,
        breakout_score REAL,
        trend_quality_score REAL,
        volume_score REAL,
        structure_score REAL,
        breakout_confirmed INTEGER,
        structure_alignment TEXT,
        primary_blocker TEXT,
        pipeline_stage TEXT,
        entry_price REAL,
        trigger_candle_ts TEXT,
        cross_score REAL,
        distance_score REAL,
        body_score REAL,
        wick_score REAL,
        scanner_version TEXT,
        pipeline_version TEXT,
        decision_version TEXT
    )
    """


def _insert_row(conn: sqlite3.Connection, row: tuple) -> None:
    conn.execute(
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
        row,
    )


def _db_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        h.update(handle.read())
    return h.hexdigest()


def test_reconstruct_trigger_timestamp_1h() -> None:
    scan_ts = _dt("2026-07-24T20:46:29.894916Z")
    trigger = reconstruct_trigger_candle_timestamp(scan_ts, "1h")
    assert trigger == _dt("2026-07-24T19:00:00Z")


def test_reconstruct_trigger_timestamp_4h_boundaries() -> None:
    assert reconstruct_trigger_candle_timestamp(_dt("2026-07-24T20:46:29Z"), "4h") == _dt("2026-07-24T16:00:00Z")
    assert reconstruct_trigger_candle_timestamp(_dt("2026-07-24T03:59:59Z"), "4h") == _dt("2026-07-23T20:00:00Z")
    assert reconstruct_trigger_candle_timestamp(_dt("2026-07-24T04:00:00Z"), "4h") == _dt("2026-07-24T00:00:00Z")


def test_reconstruct_trigger_timestamp_timezone_handling() -> None:
    scan_ts = "2026-07-24T23:30:00+03:00"
    # UTC is 20:30, floored to 20:00, minus 1h => 19:00 UTC
    assert reconstruct_trigger_candle_timestamp(scan_ts, "1h") == _dt("2026-07-24T19:00:00Z")


def test_reconstruct_trigger_timestamp_rejects_unsupported_timeframe() -> None:
    with pytest.raises(ValueError):
        reconstruct_trigger_candle_timestamp(_dt("2026-07-24T20:00:00Z"), "15m")


def test_resolve_trigger_prefers_persisted_timestamp() -> None:
    record = {
        "trigger_candle_ts": "2026-07-24T18:00:00+00:00",
    }

    trigger, source, source_field = resolve_trigger_candle_timestamp(
        record,
        "1h",
        "2026-07-24T20:46:29+00:00",
    )

    assert trigger == _dt("2026-07-24T18:00:00Z")
    assert source == "PERSISTED_TRIGGER_CANDLE_TS"
    assert source_field == "analysis_records.trigger_candle_ts"


def test_resolve_trigger_fallbacks_to_reconstruction_when_missing() -> None:
    record = {}

    trigger, source, source_field = resolve_trigger_candle_timestamp(
        record,
        "1h",
        "2026-07-24T20:46:29+00:00",
    )

    assert trigger == _dt("2026-07-24T19:00:00Z")
    assert source == "RECONSTRUCTED_FROM_SCAN_TIMESTAMP"
    assert source_field == "analysis_records.timestamp_utc"


def test_build_event_key_is_deterministic() -> None:
    a = build_event_key("op/usdt", "1h", "short", _dt("2026-07-22T12:00:00Z"))
    b = build_event_key("OP/USDT", "1h", "SHORT", "2026-07-22T12:00:00+00:00")
    assert a == b


def test_deduplicate_shadow_events_collapses_duplicates_deterministically() -> None:
    events = [
        {
            "event_key": "E1",
            "scan_timestamp_utc": "2026-07-22T13:17:00Z",
            "scan_id": "B",
            "symbol": "OP/USDT",
            "timeframe": "1h",
            "direction": "SHORT",
        },
        {
            "event_key": "E1",
            "scan_timestamp_utc": "2026-07-22T13:13:00Z",
            "scan_id": "A",
            "symbol": "OP/USDT",
            "timeframe": "1h",
            "direction": "SHORT",
        },
    ]

    original = copy.deepcopy(events)
    dedup = deduplicate_shadow_events(events)

    assert dedup["raw_count"] == 2
    assert dedup["unique_count"] == 1
    assert dedup["unique_events"][0]["scan_id"] == "A"
    assert events == original


def test_fetch_historical_candles_since_uses_injected_fetcher_and_dedups() -> None:
    calls: list[tuple[str, str, int | None, int]] = []

    def fake_fetch(symbol: str, timeframe: str, since: int | None, limit: int) -> list[list[float]]:
        calls.append((symbol, timeframe, since, limit))
        if len(calls) > 1:
            return []
        return [
            [1000, 1, 2, 0.5, 1.5, 10],
            [1000, 1, 2, 0.5, 1.5, 10],
            [2000, 1.5, 2.5, 1.0, 2.0, 11],
        ]

    out = fetch_historical_candles_since(
        "OP/USDT",
        "1h",
        1000,
        fetch_ohlcv=fake_fetch,
        symbol_normalizer=lambda s: f"{s}:USDT",
        limit=400,
        max_pages=2,
    )

    assert calls
    assert out["exchange_symbol"] == "OP/USDT:USDT"
    assert [row[0] for row in out["candles"]] == [1000, 2000]


def test_split_closed_candles_excludes_incomplete_latest() -> None:
    candles = [
        [int(_dt("2026-07-24T10:00:00Z").timestamp() * 1000), 1, 1, 1, 1, 1],
        [int(_dt("2026-07-24T11:00:00Z").timestamp() * 1000), 1, 1, 1, 1, 1],
    ]
    out = split_closed_candles(candles, _dt("2026-07-24T11:30:00Z"), "1h")
    assert len(out) == 1


def test_split_closed_candles_keeps_completed_latest() -> None:
    candles = [
        [int(_dt("2026-07-24T10:00:00Z").timestamp() * 1000), 1, 1, 1, 1, 1],
        [int(_dt("2026-07-24T11:00:00Z").timestamp() * 1000), 1, 1, 1, 1, 1],
    ]
    out = split_closed_candles(candles, _dt("2026-07-24T12:01:00Z"), "1h")
    assert len(out) == 2


def test_locate_trigger_candle_exact_match_and_absent() -> None:
    candles = [[1000, 1, 2, 0.5, 1.5, 10], [2000, 1, 2, 0.5, 1.5, 10]]
    found = locate_trigger_candle(candles, _dt("1970-01-01T00:00:01Z"))
    assert found.found and found.index == 0

    missing = locate_trigger_candle(candles, _dt("1970-01-01T00:00:03Z"))
    assert not missing.found and missing.index is None


def test_calculate_forward_outcomes_horizon_independence_and_trigger_excluded() -> None:
    candles = _candles_1h(
        "2026-07-24T10:00:00Z",
        closes=[100, 110, 120, 130, 140, 150],
        highs=[101, 111, 121, 131, 141, 151],
        lows=[99, 109, 119, 129, 139, 149],
    )
    # trigger at idx 0; exactly 5 future candles => H3/H5 available, H10/H20 unavailable
    out = calculate_forward_outcomes(
        candles,
        0,
        "LONG",
        atr_value=10.0,
    )

    assert out["horizons"][3]["available"] is True
    assert out["horizons"][5]["available"] is True
    assert out["horizons"][10]["available"] is False
    assert out["horizons"][20]["available"] is False

    # Trigger candle is excluded, so H3 uses close at idx 3 (130), not idx 2.
    assert pytest.approx(out["horizons"][3]["directional_return_pct"], rel=1e-9) == 30.0


def test_calculate_forward_outcomes_long_and_short_and_nonnegative_excursions() -> None:
    candles = _candles_1h(
        "2026-07-24T10:00:00Z",
        closes=[100, 102, 98, 103, 97, 96],
        highs=[101, 103, 100, 104, 98, 97],
        lows=[99, 100, 95, 101, 94, 93],
    )

    long_out = calculate_forward_outcomes(candles, 0, "LONG", atr_value=2.0)
    short_out = calculate_forward_outcomes(candles, 0, "SHORT", atr_value=2.0)

    for horizon in (3, 5):
        if long_out["horizons"][horizon]["available"]:
            assert long_out["horizons"][horizon]["mfe_atr"] >= 0
            assert long_out["horizons"][horizon]["mae_atr"] >= 0
        if short_out["horizons"][horizon]["available"]:
            assert short_out["horizons"][horizon]["mfe_atr"] >= 0
            assert short_out["horizons"][horizon]["mae_atr"] >= 0


def test_calculate_forward_outcomes_hit_detection_and_first_hit() -> None:
    candles = _candles_1h(
        "2026-07-24T10:00:00Z",
        closes=[100, 100, 100, 100, 100, 100],
        highs=[100, 101.2, 99.8, 100.1, 100.2, 100.3],
        lows=[100, 99.9, 98.8, 99.9, 99.8, 99.7],
    )

    out = calculate_forward_outcomes(candles, 0, "LONG", atr_value=1.0)
    h3 = out["horizons"][3]
    assert h3["hit_plus_1_0_atr"] is True
    assert h3["hit_minus_1_0_atr"] is True
    assert h3["first_hit_plus1_vs_minus1"] == "+1ATR"


def test_classify_outcome_by_context_groups_correctly() -> None:
    events = [
        {"event_key": "A", "context_class": "SUPPORTS"},
        {"event_key": "B", "context_class": "MIXED"},
        {"event_key": "C", "context_class": "UNKNOWN"},
    ]
    outcomes = {"A": {"horizons": {3: {"available": True}}}, "B": {}}

    grouped = classify_outcome_by_context(events, outcomes)
    assert len(grouped["SUPPORTS"]) == 1
    assert len(grouped["MIXED"]) == 1
    assert len(grouped["UNAVAILABLE"]) == 1


def test_calculate_forward_outcomes_does_not_mutate_input() -> None:
    candles = _candles_1h(
        "2026-07-24T10:00:00Z",
        closes=[100, 101, 102, 103, 104, 105],
        highs=[100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
        lows=[99.5, 100.5, 101.5, 102.5, 103.5, 104.5],
    )
    original = copy.deepcopy(candles)

    _ = calculate_forward_outcomes(candles, 0, "LONG", atr_value=1.0)

    assert candles == original


def test_build_historical_shadow_report_is_deterministic_and_db_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "analytics.db"
    conn = sqlite3.connect(db_path)
    conn.execute(_schema_sql())

    # Same market event across two scans to verify deterministic dedup retention.
    _insert_row(
        conn,
        (
            "id-1",
            "scan-a",
            "2026-07-24T20:46:29+00:00",
            "APT/USDT",
            "1h",
            "LONG",
            "WATCH",
            61.0,
            76.0,
            95.0,
            60.0,
            55.0,
            50.0,
            1,
            "NEUTRAL",
            None,
            "ok",
            0.5987,
            None,
            None,
            None,
        ),
    )
    _insert_row(
        conn,
        (
            "id-2",
            "scan-b",
            "2026-07-24T20:47:29+00:00",
            "APT/USDT",
            "1h",
            "LONG",
            "WATCH",
            61.0,
            76.0,
            95.0,
            60.0,
            55.0,
            50.0,
            1,
            "NEUTRAL",
            None,
            "ok",
            0.5987,
            None,
            None,
            None,
        ),
    )

    # Add context rows (1w/1d) so context classification stays deterministic.
    for tf, aid in (("1w", "id-3"), ("1d", "id-4")):
        _insert_row(
            conn,
            (
                aid,
                "scan-a",
                "2026-07-24T20:46:29+00:00",
                "APT/USDT",
                tf,
                "LONG",
                "WATCH",
                55.0,
                70.0,
                70.0,
                55.0,
                45.0,
                45.0,
                1,
                "NEUTRAL",
                None,
                "ok",
                0.5987,
                None,
                None,
                None,
            ),
        )

    conn.commit()
    conn.close()

    before = _db_hash(db_path)

    def fake_fetch(symbol: str, timeframe: str, since: int | None, limit: int) -> list[list[float]]:
        # Trigger reconstructed for 1h event is 19:00 UTC.
        # Provide 6 closed candles after trigger and one incomplete last candle.
        if timeframe != "1h":
            return []
        base = int(_dt("2026-07-24T18:00:00Z").timestamp() * 1000)
        return [
            [base + 0 * 3600_000, 1, 1, 1, 1, 10],
            [base + 1 * 3600_000, 1, 1, 1, 1, 10],  # trigger 19:00
            [base + 2 * 3600_000, 1, 1.2, 0.9, 1.05, 10],
            [base + 3 * 3600_000, 1, 1.3, 0.8, 1.10, 10],
            [base + 4 * 3600_000, 1, 1.4, 0.7, 1.20, 10],
            [base + 5 * 3600_000, 1, 1.5, 0.7, 1.30, 10],
            [base + 6 * 3600_000, 1, 1.6, 0.6, 1.40, 10],
            [base + 7 * 3600_000, 1, 1.7, 0.6, 1.50, 10],  # incomplete at now=01:30
        ]

    now = _dt("2026-07-25T01:30:00Z")

    r1 = build_historical_shadow_report(
        db_path,
        now_utc=now,
        fetch_ohlcv=fake_fetch,
        symbol_normalizer=lambda s: f"{s}:USDT",
    )
    r2 = build_historical_shadow_report(
        db_path,
        now_utc=now,
        fetch_ohlcv=fake_fetch,
        symbol_normalizer=lambda s: f"{s}:USDT",
    )

    after = _db_hash(db_path)

    assert before == after
    assert r1 == r2
    assert r1["dataset"]["db_unchanged"] is True


def test_unit_tests_do_not_require_live_network_calls() -> None:
    called = {"value": False}

    def fake_fetch(symbol: str, timeframe: str, since: int | None, limit: int) -> list[list[float]]:
        called["value"] = True
        return []

    _ = fetch_historical_candles_since(
        "OP/USDT",
        "1h",
        0,
        fetch_ohlcv=fake_fetch,
        symbol_normalizer=lambda s: s,
    )

    assert called["value"] is True
