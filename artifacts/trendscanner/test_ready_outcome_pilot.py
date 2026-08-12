from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ready_outcome_pilot import (
    build_aggregate,
    collect_examples,
    run_ready_outcome_pilot,
)
def _event(
    *,
    symbol: str,
    timeframe: str = "4h",
    direction: str = "LONG",
    timestamp: int = 1000,
    replay_index: int = 0,
    entry: float = 100.0,
    h3: dict[str, Any] | None = None,
    h5: dict[str, Any] | None = None,
    h10: dict[str, Any] | None = None,
    confidence: float | None = 70.0,
    score: float | None = 55.0,
    warnings: list[str] | None = None,
    breakout_score: float | None = None,
    breakout_confirmed: bool | None = None,
    trend_quality: float | None = None,
    volume_quality: float | None = None,
    structure_quality: float | None = None,
    structure_state: str | None = None,
    blocker_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "ready_replay_index": replay_index,
        "ready_timestamp": timestamp,
        "ready_candle_index": replay_index,
        "entry_reference_price": entry,
        "ready_confidence": confidence,
        "ready_decision_score": score,
        "ready_warnings": warnings or [],
        "breakout_score": breakout_score,
        "breakout_confirmed": breakout_confirmed,
        "trend_quality": trend_quality,
        "volume_quality": volume_quality,
        "structure_quality": structure_quality,
        "structure_state": structure_state,
        "blocker_codes": blocker_codes,
        "horizons": {
            "3": h3 or {
                "available": True,
                "mfe_pct": 1.0,
                "mae_pct": 0.5,
                "close_return_pct": 0.1,
                "bars_to_mfe": 1,
                "bars_to_mae": 1,
            },
            "5": h5 or {
                "available": True,
                "mfe_pct": 1.0,
                "mae_pct": 0.5,
                "close_return_pct": 0.1,
                "bars_to_mfe": 1,
                "bars_to_mae": 1,
            },
            "10": h10 or {
                "available": False,
                "mfe_pct": None,
                "mae_pct": None,
                "close_return_pct": None,
                "bars_to_mfe": None,
                "bars_to_mae": None,
            },
        },
    }


def _summary(ready_events: int, skipped_events: int = 0) -> dict[str, Any]:
    return {
        "ready_events": ready_events,
        "skipped_events": skipped_events,
        "by_direction": {"LONG": ready_events, "SHORT": 0},
        "by_horizon": {
            "3": {
                "available_events": ready_events,
                "average_mfe_pct": 1.0,
                "median_mfe_pct": 1.0,
                "average_mae_pct": 0.5,
                "median_mae_pct": 0.5,
                "average_close_return_pct": 0.1,
                "positive_close_rate": 100.0,
                "mfe_ge_1_pct_rate": 100.0,
                "mfe_ge_2_pct_rate": 0.0,
                "mfe_ge_3_pct_rate": 0.0,
            },
            "5": {
                "available_events": ready_events,
                "average_mfe_pct": 1.0,
                "median_mfe_pct": 1.0,
                "average_mae_pct": 0.5,
                "median_mae_pct": 0.5,
                "average_close_return_pct": 0.1,
                "positive_close_rate": 100.0,
                "mfe_ge_1_pct_rate": 100.0,
                "mfe_ge_2_pct_rate": 0.0,
                "mfe_ge_3_pct_rate": 0.0,
            },
            "10": {
                "available_events": 0,
                "average_mfe_pct": None,
                "median_mfe_pct": None,
                "average_mae_pct": None,
                "median_mae_pct": None,
                "average_close_return_pct": None,
                "positive_close_rate": None,
                "mfe_ge_1_pct_rate": None,
                "mfe_ge_2_pct_rate": None,
                "mfe_ge_3_pct_rate": None,
            },
        },
        "diagnostics": {
            "timestamp_not_found": 0,
            "invalid_price": 0,
            "malformed_replay_entry": 0,
        },
    }


def test_success_run_is_saved() -> None:
    with TemporaryDirectory() as tmp:
        json_path = str(Path(tmp) / "r" / "out.json")
        csv_path = str(Path(tmp) / "r" / "out.csv")

        def fake_get_data(symbol: str, timeframe: str) -> dict:
            return {"symbol": symbol, "timeframe": timeframe}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [1], "meta": {"ok": True}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            assert replay_results == [1]
            return {
                "summary": _summary(1),
                "events": [_event(symbol=df["symbol"])],
            }

        payload = run_ready_outcome_pilot(
            symbols=["BTC/USDT"],
            timeframes=["4h"],
            horizons=(3, 5, 10),
            output_json=json_path,
            output_csv=csv_path,
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["status"] == "OK"


def test_failed_combo_does_not_stop_others() -> None:
    with TemporaryDirectory() as tmp:
        def fake_get_data(symbol: str, timeframe: str) -> dict:
            if symbol == "BAD":
                raise RuntimeError("network")
            return {"symbol": symbol, "timeframe": timeframe}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [1], "meta": {}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            return {"summary": _summary(0), "events": []}

        payload = run_ready_outcome_pilot(
            symbols=["BAD", "GOOD"],
            timeframes=["4h"],
            output_json=str(Path(tmp) / "o.json"),
            output_csv=str(Path(tmp) / "o.csv"),
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        assert len(payload["runs"]) == 2
        statuses = [row["status"] for row in payload["runs"]]
        assert statuses.count("FAILED") == 1
        assert statuses.count("OK") == 1


def test_aggregate_weighted_by_events() -> None:
    runs = [
        {
            "status": "OK",
            "summary": {"ready_events": 1, "skipped_events": 0},
            "events": [
                _event(symbol="A", h5={"available": True, "mfe_pct": 10.0, "mae_pct": 1.0, "close_return_pct": 1.0, "bars_to_mfe": 1, "bars_to_mae": 1}),
            ],
        },
        {
            "status": "OK",
            "summary": {"ready_events": 3, "skipped_events": 0},
            "events": [
                _event(symbol="B", replay_index=1, h5={"available": True, "mfe_pct": 0.0, "mae_pct": 1.0, "close_return_pct": -1.0, "bars_to_mfe": 1, "bars_to_mae": 1}),
                _event(symbol="B", replay_index=2, h5={"available": True, "mfe_pct": 0.0, "mae_pct": 1.0, "close_return_pct": -1.0, "bars_to_mfe": 1, "bars_to_mae": 1}),
                _event(symbol="B", replay_index=3, h5={"available": True, "mfe_pct": 0.0, "mae_pct": 1.0, "close_return_pct": -1.0, "bars_to_mfe": 1, "bars_to_mae": 1}),
            ],
        },
    ]

    agg = build_aggregate(runs, (5,))
    h5 = agg["by_horizon"]["5"]

    assert h5["available_events"] == 4
    assert h5["average_mfe_pct"] == 2.5


def test_csv_horizon_flattening() -> None:
    with TemporaryDirectory() as tmp:
        def fake_get_data(symbol: str, timeframe: str) -> dict:
            return {"symbol": symbol}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [1], "meta": {}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            event = _event(symbol="BTC/USDT")
            return {"summary": _summary(1), "events": [event]}

        csv_path = Path(tmp) / "d" / "events.csv"
        payload = run_ready_outcome_pilot(
            symbols=["BTC/USDT"],
            timeframes=["4h"],
            output_json=str(Path(tmp) / "d" / "out.json"),
            output_csv=str(csv_path),
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        assert payload["runs"][0]["status"] == "OK"
        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            assert "h3_mfe_pct" in headers
            assert "h5_close_return_pct" in headers
            assert "h10_bars_to_mae" in headers
            assert "breakout_score" in headers
            assert "breakout_confirmed" in headers
            assert "trend_quality" in headers
            assert "volume_quality" in headers
            assert "structure_quality" in headers
            assert "structure_state" in headers
            assert "blocker_codes" in headers


def test_csv_pipeline_fields_flatten_values() -> None:
    with TemporaryDirectory() as tmp:
        def fake_get_data(symbol: str, timeframe: str) -> dict:
            return {"symbol": symbol}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [1], "meta": {}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            event = _event(
                symbol="BTC/USDT",
                breakout_score=81.5,
                breakout_confirmed=True,
                trend_quality=66.0,
                volume_quality=72.0,
                structure_quality=58.0,
                structure_state="OPPOSED",
                blocker_codes=["B1", "B2"],
            )
            return {"summary": _summary(1), "events": [event]}

        csv_path = Path(tmp) / "f" / "events.csv"
        run_ready_outcome_pilot(
            symbols=["BTC/USDT"],
            timeframes=["4h"],
            output_json=str(Path(tmp) / "f" / "out.json"),
            output_csv=str(csv_path),
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        row = rows[0]
        assert row["breakout_score"] == "81.5"
        assert row["breakout_confirmed"] == "True"
        assert row["trend_quality"] == "66.0"
        assert row["volume_quality"] == "72.0"
        assert row["structure_quality"] == "58.0"
        assert row["structure_state"] == "OPPOSED"
        assert row["blocker_codes"] == "B1 | B2"


def test_json_nan_inf_sanitized() -> None:
    with TemporaryDirectory() as tmp:
        def fake_get_data(symbol: str, timeframe: str) -> dict:
            return {"symbol": symbol}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [1], "meta": {}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            event = _event(
                symbol="BTC/USDT",
                h5={
                    "available": True,
                    "mfe_pct": math.inf,
                    "mae_pct": 1.0,
                    "close_return_pct": math.nan,
                    "bars_to_mfe": 1,
                    "bars_to_mae": 1,
                },
            )
            summary = _summary(1)
            summary["by_horizon"]["5"]["average_mfe_pct"] = math.inf
            return {"summary": summary, "events": [event]}

        json_path = Path(tmp) / "e" / "out.json"
        run_ready_outcome_pilot(
            symbols=["BTC/USDT"],
            timeframes=["4h"],
            output_json=str(json_path),
            output_csv=str(Path(tmp) / "e" / "out.csv"),
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        h5_avg = parsed["runs"][0]["summary"]["by_horizon"]["5"]["average_mfe_pct"]
        h5_evt = parsed["runs"][0]["events"][0]["horizons"]["5"]["mfe_pct"]
        h5_close = parsed["runs"][0]["events"][0]["horizons"]["5"]["close_return_pct"]
        assert h5_avg is None
        assert h5_evt is None
        assert h5_close is None


def test_examples_sorted_deterministically() -> None:
    runs = [
        {
            "status": "OK",
            "summary": {"ready_events": 3, "skipped_events": 0},
            "events": [
                _event(symbol="B", timestamp=2, replay_index=2, h5={"available": True, "mfe_pct": 5.0, "mae_pct": 1.0, "close_return_pct": -1.0, "bars_to_mfe": 1, "bars_to_mae": 1}),
                _event(symbol="A", timestamp=1, replay_index=1, h5={"available": True, "mfe_pct": 5.0, "mae_pct": 1.0, "close_return_pct": -1.0, "bars_to_mfe": 1, "bars_to_mae": 1}),
                _event(symbol="C", timestamp=3, replay_index=3, h5={"available": True, "mfe_pct": 1.0, "mae_pct": 1.0, "close_return_pct": -5.0, "bars_to_mfe": 1, "bars_to_mae": 1}),
            ],
        }
    ]

    examples = collect_examples(runs, horizon=5, limit=5)
    top = examples["top_mfe_h5"]
    worst = examples["worst_close_h5"]

    assert top[0]["symbol"] == "A"
    assert top[1]["symbol"] == "B"
    assert worst[0]["symbol"] == "C"


def test_analyzer_results_not_mutated() -> None:
    with TemporaryDirectory() as tmp:
        result_obj = {
            "summary": _summary(1),
            "events": [
                _event(symbol="BTC/USDT", warnings=["W1"]),
            ],
        }
        original = copy.deepcopy(result_obj)

        def fake_get_data(symbol: str, timeframe: str) -> dict:
            return {"symbol": symbol}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [1], "meta": {}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            return result_obj

        run_ready_outcome_pilot(
            symbols=["BTC/USDT"],
            timeframes=["4h"],
            output_json=str(Path(tmp) / "z" / "out.json"),
            output_csv=str(Path(tmp) / "z" / "out.csv"),
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        assert result_obj == original


def test_empty_results_saved() -> None:
    with TemporaryDirectory() as tmp:
        def fake_get_data(symbol: str, timeframe: str) -> dict:
            return {"symbol": symbol}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [], "meta": {}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            return {"summary": _summary(0), "events": []}

        payload = run_ready_outcome_pilot(
            symbols=["BTC/USDT"],
            timeframes=["4h"],
            output_json=str(Path(tmp) / "q" / "out.json"),
            output_csv=str(Path(tmp) / "q" / "out.csv"),
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        assert payload["aggregate"]["total_ready_events"] == 0
        assert payload["runs"][0]["events"] == []


def test_output_dirs_are_created() -> None:
    with TemporaryDirectory() as tmp:
        base = Path(tmp) / "deep" / "nested" / "path"

        def fake_get_data(symbol: str, timeframe: str) -> dict:
            return {"symbol": symbol}

        def fake_replay(df: dict, **kwargs) -> dict:
            return {"replay_results": [], "meta": {}}

        def fake_analyze(df: dict, replay_results: list, horizons: tuple[int, ...]) -> dict:
            return {"summary": _summary(0), "events": []}

        json_path = base / "a" / "out.json"
        csv_path = base / "b" / "out.csv"

        run_ready_outcome_pilot(
            symbols=["BTC/USDT"],
            timeframes=["4h"],
            output_json=str(json_path),
            output_csv=str(csv_path),
            get_data_fn=fake_get_data,
            replay_timeframe_fn=fake_replay,
            analyze_ready_outcomes_fn=fake_analyze,
        )

        assert json_path.exists()
        assert csv_path.exists()


if __name__ == "__main__":
    tests = [
        test_success_run_is_saved,
        test_failed_combo_does_not_stop_others,
        test_aggregate_weighted_by_events,
        test_csv_horizon_flattening,
        test_csv_pipeline_fields_flatten_values,
        test_json_nan_inf_sanitized,
        test_examples_sorted_deterministically,
        test_analyzer_results_not_mutated,
        test_empty_results_saved,
        test_output_dirs_are_created,
    ]

    for test in tests:
        test()

    print(f"{len(tests)} ready outcome pilot tests passed.")
