from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from ready_factor_analysis import (
    analyze_ready_factors,
    save_report_csv,
    save_report_json,
)


def _event(
    *,
    symbol: str,
    direction: str = "LONG",
    conf: float = 70.0,
    score: float = 60.0,
    warnings: list[str] | None = None,
    mfe: float = 1.0,
    mae: float = 0.5,
    close: float = 0.1,
    breakout_score=None,
    trend_quality=None,
    volume_quality=None,
    structure_quality=None,
    structure_state=None,
    blocker_codes=None,
) -> dict:
    event = {
        "symbol": symbol,
        "timeframe": "4h",
        "direction": direction,
        "ready_confidence": conf,
        "ready_decision_score": score,
        "ready_warnings": warnings or [],
        "horizons": {
            "5": {
                "available": True,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "close_return_pct": close,
                "bars_to_mfe": 1,
                "bars_to_mae": 1,
            }
        },
    }

    if breakout_score is not None:
        event["breakout_score"] = breakout_score
    if trend_quality is not None:
        event["trend_quality"] = trend_quality
    if volume_quality is not None:
        event["volume_quality"] = volume_quality
    if structure_quality is not None:
        event["structure_quality"] = structure_quality
    if structure_state is not None:
        event["structure_state"] = structure_state
    if blocker_codes is not None:
        event["blocker_codes"] = blocker_codes

    return event


def _payload(events: list[dict], ok: bool = True) -> dict:
    return {
        "runs": [
            {
                "status": "OK" if ok else "FAILED",
                "events": events,
            }
        ]
    }


def _group(report: dict, factor_type: str, factor_value: str) -> dict | None:
    for row in report.get("groups", []):
        if row.get("factor_type") == factor_type and row.get("factor_value") == factor_value:
            return row
    return None


def test_direction_grouping() -> None:
    payload = _payload([
        _event(symbol="A", direction="LONG"),
        _event(symbol="B", direction="SHORT"),
    ])
    report = analyze_ready_factors(payload)

    assert _group(report, "direction", "LONG")["event_count"] == 1
    assert _group(report, "direction", "SHORT")["event_count"] == 1


def test_no_warnings_group() -> None:
    payload = _payload([_event(symbol="A", warnings=[])])
    report = analyze_ready_factors(payload)
    assert _group(report, "warning", "NO_WARNINGS")["event_count"] == 1


def test_multi_warning_event_in_two_groups() -> None:
    payload = _payload([_event(symbol="A", warnings=["W1", "W2"])])
    report = analyze_ready_factors(payload)

    assert _group(report, "warning", "W1")["event_count"] == 1
    assert _group(report, "warning", "W2")["event_count"] == 1


def test_confidence_bucket_boundaries() -> None:
    payload = _payload([
        _event(symbol="A", conf=59.99),
        _event(symbol="B", conf=60.0),
        _event(symbol="C", conf=70.0),
        _event(symbol="D", conf=80.0),
    ])
    report = analyze_ready_factors(payload)

    assert _group(report, "confidence_bucket", "<60")["event_count"] == 1
    assert _group(report, "confidence_bucket", "60-70")["event_count"] == 1
    assert _group(report, "confidence_bucket", "70-80")["event_count"] == 1
    assert _group(report, "confidence_bucket", "80+")["event_count"] == 1


def test_decision_bucket_boundaries() -> None:
    payload = _payload([
        _event(symbol="A", score=49.9),
        _event(symbol="B", score=50.0),
        _event(symbol="C", score=60.0),
        _event(symbol="D", score=70.0),
        _event(symbol="E", score=80.0),
    ])
    report = analyze_ready_factors(payload)

    assert _group(report, "decision_score_bucket", "<50")["event_count"] == 1
    assert _group(report, "decision_score_bucket", "50-60")["event_count"] == 1
    assert _group(report, "decision_score_bucket", "60-70")["event_count"] == 1
    assert _group(report, "decision_score_bucket", "70-80")["event_count"] == 1
    assert _group(report, "decision_score_bucket", "80+")["event_count"] == 1


def test_missing_optional_field_not_zero() -> None:
    payload = _payload([_event(symbol="A")])
    report = analyze_ready_factors(payload)
    assert _group(report, "breakout_score_bucket", "50-60") is None
    assert report["summary"]["missing_field_counts"]["breakout_score"] == 1


def test_low_sample_flag_for_small_group() -> None:
    payload = _payload([_event(symbol="A", warnings=["W"])])
    report = analyze_ready_factors(payload)
    assert _group(report, "warning", "W")["low_sample"] is True


def test_low_sample_excluded_from_rankings() -> None:
    payload = _payload([_event(symbol="A", warnings=["W1"])])
    report = analyze_ready_factors(payload)
    assert report["strongest_factors"] == []
    assert report["weakest_factors"] == []


def test_ranking_uses_mfe_minus_mae() -> None:
    events = []
    for idx in range(5):
        events.append(_event(symbol=f"A{idx}", warnings=["A"], mfe=3.0, mae=2.9, close=0.1))
        events.append(_event(symbol=f"B{idx}", warnings=["B"], mfe=2.0, mae=0.1, close=0.1))
    report = analyze_ready_factors(_payload(events))

    strongest = report["strongest_factors"]
    assert strongest[0]["factor_type"] == "warning"
    assert strongest[0]["factor_value"] == "B"


def test_rates_are_percentages() -> None:
    payload = _payload([
        _event(symbol="A", close=1.0, mfe=2.0),
        _event(symbol="B", close=-1.0, mfe=0.5),
    ])
    report = analyze_ready_factors(payload)
    row = _group(report, "direction", "LONG")
    assert row["positive_close_rate"] == 50.0
    assert row["mfe_ge_2_pct_rate"] == 50.0


def test_median_correct() -> None:
    payload = _payload([
        _event(symbol="A", mfe=1.0),
        _event(symbol="B", mfe=3.0),
        _event(symbol="C", mfe=2.0),
    ])
    report = analyze_ready_factors(payload)
    row = _group(report, "direction", "LONG")
    assert row["median_mfe_pct"] == 2.0


def test_nan_inf_not_in_json() -> None:
    events = [_event(symbol="A", mfe=1.0, mae=0.5, close=0.1)]
    report = analyze_ready_factors(_payload(events))
    report["groups"][0]["average_mfe_pct"] = math.inf

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.json"
        save_report_json(path, report)
        parsed = json.loads(path.read_text(encoding="utf-8"))
        assert parsed["groups"][0]["average_mfe_pct"] is None


def test_csv_one_row_per_group() -> None:
    payload = _payload([
        _event(symbol="A", direction="LONG", warnings=["W1"]),
        _event(symbol="B", direction="SHORT", warnings=["W2"]),
    ])
    report = analyze_ready_factors(payload)

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.csv"
        save_report_csv(path, report)
        with path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    assert len(rows) == len(report["groups"])


def test_payload_not_mutated() -> None:
    payload = _payload([_event(symbol="A")])
    original = copy.deepcopy(payload)
    _ = analyze_ready_factors(payload)
    assert payload == original


def test_empty_payload() -> None:
    report = analyze_ready_factors({})
    assert report["summary"]["successful_runs"] == 0
    assert report["summary"]["available_events"] == 0


def test_malformed_events_safe_and_limited() -> None:
    payload = {
        "runs": [
            {"status": "OK", "events": ["bad", {"symbol": "X", "horizons": {"5": {"available": True}}}]}
        ]
    }
    report = analyze_ready_factors(payload)
    assert report["summary"]["available_events"] == 0
    assert any("Skipped malformed events" in item for item in report["limitations"])


def test_structure_state_grouping() -> None:
    payload = _payload([
        _event(symbol="A", structure_state="ALIGNED"),
        _event(symbol="B", structure_state="OPPOSED"),
    ])
    report = analyze_ready_factors(payload)
    assert _group(report, "structure_state", "ALIGNED")["event_count"] == 1
    assert _group(report, "structure_state", "OPPOSED")["event_count"] == 1


def test_missing_structure_state_not_unknown() -> None:
    payload = _payload([
        _event(symbol="A"),
    ])
    report = analyze_ready_factors(payload)
    assert _group(report, "structure_state", "UNKNOWN") is None
    assert report["summary"]["missing_field_counts"]["structure_state"] == 1


def test_blocker_codes_multiple_groups() -> None:
    payload = _payload([
        _event(symbol="A", blocker_codes=["B1", "B2"]),
    ])
    report = analyze_ready_factors(payload)
    assert _group(report, "blocker_code", "B1")["event_count"] == 1
    assert _group(report, "blocker_code", "B2")["event_count"] == 1


def test_optional_score_buckets_use_real_values() -> None:
    payload = _payload([
        _event(
            symbol="A",
            breakout_score=85.0,
            trend_quality=75.0,
            volume_quality=65.0,
            structure_quality=45.0,
            blocker_codes=[],
            structure_state="TRANSITION",
        )
    ])
    report = analyze_ready_factors(payload)
    assert _group(report, "breakout_score_bucket", "80+")["event_count"] == 1
    assert _group(report, "trend_quality_bucket", "70-80")["event_count"] == 1
    assert _group(report, "volume_quality_bucket", "60-80")["event_count"] == 1
    assert _group(report, "structure_quality_bucket", "40-60")["event_count"] == 1


def test_old_16_behavior_still_works() -> None:
    payload = _payload([
        _event(symbol="A", direction="LONG", warnings=["W1"], mfe=2.0, mae=1.0, close=0.5),
        _event(symbol="B", direction="SHORT", warnings=[], mfe=0.5, mae=2.0, close=-0.2),
    ])
    report = analyze_ready_factors(payload)
    assert _group(report, "direction", "LONG")["event_count"] == 1
    assert _group(report, "warning", "NO_WARNINGS")["event_count"] == 1


if __name__ == "__main__":
    tests = [
        test_direction_grouping,
        test_no_warnings_group,
        test_multi_warning_event_in_two_groups,
        test_confidence_bucket_boundaries,
        test_decision_bucket_boundaries,
        test_missing_optional_field_not_zero,
        test_low_sample_flag_for_small_group,
        test_low_sample_excluded_from_rankings,
        test_ranking_uses_mfe_minus_mae,
        test_rates_are_percentages,
        test_median_correct,
        test_nan_inf_not_in_json,
        test_csv_one_row_per_group,
        test_payload_not_mutated,
        test_empty_payload,
        test_malformed_events_safe_and_limited,
        test_structure_state_grouping,
        test_missing_structure_state_not_unknown,
        test_blocker_codes_multiple_groups,
        test_optional_score_buckets_use_real_values,
        test_old_16_behavior_still_works,
    ]

    for test in tests:
        test()

    print(f"{len(tests)} ready factor analysis tests passed.")
