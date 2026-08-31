from __future__ import annotations

from ready_factor_interactions import (
    analyze_ready_factor_interactions,
    _monotonic_status,
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
    return event


def _payload(events: list[dict]) -> dict:
    return {"runs": [{"status": "OK", "events": events}]}


def _cross_tab_row(report: dict, factor_type: str, factor_value: str, direction: str) -> dict | None:
    for row in report.get("cross_tabs", []):
        if (
            row.get("factor_type") == factor_type
            and row.get("factor_value") == factor_value
            and row.get("direction") == direction
        ):
            return row
    return None


def test_direction_x_structure_state_split() -> None:
    payload = _payload(
        [
            _event(symbol="A", direction="LONG", structure_state="RANGE"),
            _event(symbol="B", direction="SHORT", structure_state="RANGE"),
        ]
    )
    report = analyze_ready_factor_interactions(payload)

    long_row = _cross_tab_row(report, "structure_state", "RANGE", "LONG")
    short_row = _cross_tab_row(report, "structure_state", "RANGE", "SHORT")

    assert long_row is not None and long_row["event_count"] == 1
    assert short_row is not None and short_row["event_count"] == 1


def test_direction_x_warning_multi_warning_counted_in_each() -> None:
    payload = _payload([_event(symbol="A", direction="LONG", warnings=["W1", "W2"])])
    report = analyze_ready_factor_interactions(payload)

    assert _cross_tab_row(report, "warning", "W1", "LONG")["event_count"] == 1
    assert _cross_tab_row(report, "warning", "W2", "LONG")["event_count"] == 1


def test_direction_x_confidence_bucket() -> None:
    payload = _payload(
        [
            _event(symbol="A", direction="LONG", conf=85.0),
            _event(symbol="B", direction="SHORT", conf=45.0),
        ]
    )
    report = analyze_ready_factor_interactions(payload)

    assert _cross_tab_row(report, "confidence_bucket", "80+", "LONG")["event_count"] == 1
    assert _cross_tab_row(report, "confidence_bucket", "<60", "SHORT")["event_count"] == 1


def test_direction_x_breakout_score_bucket_skips_missing() -> None:
    payload = _payload(
        [
            _event(symbol="A", direction="LONG", breakout_score=85.0),
            _event(symbol="B", direction="LONG", breakout_score=None),
        ]
    )
    report = analyze_ready_factor_interactions(payload)

    assert _cross_tab_row(report, "breakout_score_bucket", "80+", "LONG")["event_count"] == 1
    total_breakout_rows = [row for row in report["cross_tabs"] if row["factor_type"] == "breakout_score_bucket"]
    assert sum(row["event_count"] for row in total_breakout_rows) == 1


def test_low_sample_flagged_below_threshold() -> None:
    payload = _payload([_event(symbol="A", direction="LONG", structure_state="RANGE")])
    report = analyze_ready_factor_interactions(payload)

    row = _cross_tab_row(report, "structure_state", "RANGE", "LONG")
    assert row["low_sample"] is True
    assert row["event_count"] == 1


def test_low_sample_excluded_from_strongest_and_weakest() -> None:
    payload = _payload([_event(symbol="A", direction="LONG", structure_state="RANGE")])
    report = analyze_ready_factor_interactions(payload)

    assert report["strongest_interactions"] == []
    assert report["weakest_interactions"] == []


def test_strongest_and_weakest_respect_min_sample() -> None:
    strong_events = [
        _event(symbol=f"S{i}", direction="LONG", mfe=5.0, mae=0.1, structure_state="TREND")
        for i in range(8)
    ]
    weak_events = [
        _event(symbol=f"W{i}", direction="SHORT", mfe=0.1, mae=5.0, structure_state="RANGE")
        for i in range(8)
    ]
    payload = _payload(strong_events + weak_events)
    report = analyze_ready_factor_interactions(payload)

    strongest_values = [
        (row["factor_type"], row["factor_value"], row["direction"]) for row in report["strongest_interactions"]
    ]
    weakest_values = [
        (row["factor_type"], row["factor_value"], row["direction"]) for row in report["weakest_interactions"]
    ]

    assert ("structure_state", "TREND", "LONG") in strongest_values
    assert ("structure_state", "RANGE", "SHORT") in weakest_values


def test_monotonic_status_increasing() -> None:
    assert _monotonic_status([1.0, 2.0, 3.0]) == "increasing"


def test_monotonic_status_decreasing() -> None:
    assert _monotonic_status([3.0, 2.0, 1.0]) == "decreasing"


def test_monotonic_status_not_monotonic() -> None:
    assert _monotonic_status([1.0, 3.0, 2.0]) == "not_monotonic"


def test_monotonic_status_insufficient_data() -> None:
    assert _monotonic_status([1.0]) == "insufficient_data"


def test_monotonicity_reports_all_six_scores() -> None:
    payload = _payload(
        [
            _event(
                symbol="A",
                conf=85.0,
                score=85.0,
                breakout_score=85.0,
                trend_quality=85.0,
                volume_quality=85.0,
                structure_quality=85.0,
                mfe=3.0,
                mae=0.1,
            ),
            _event(
                symbol="B",
                conf=45.0,
                score=45.0,
                breakout_score=45.0,
                trend_quality=45.0,
                volume_quality=35.0,
                structure_quality=35.0,
                mfe=0.1,
                mae=3.0,
            ),
        ]
    )
    report = analyze_ready_factor_interactions(payload)

    score_names = {entry["score"] for entry in report["monotonicity"]}
    assert score_names == {
        "confidence",
        "decision_score",
        "breakout_score",
        "trend_quality",
        "volume_quality",
        "structure_quality",
    }


def test_unavailable_horizon_events_excluded() -> None:
    event = _event(symbol="A")
    event["horizons"]["5"]["available"] = False
    payload = _payload([event])
    report = analyze_ready_factor_interactions(payload)

    assert report["summary"]["available_events"] == 0
    assert report["cross_tabs"] == []


if __name__ == "__main__":
    tests = [
        test_direction_x_structure_state_split,
        test_direction_x_warning_multi_warning_counted_in_each,
        test_direction_x_confidence_bucket,
        test_direction_x_breakout_score_bucket_skips_missing,
        test_low_sample_flagged_below_threshold,
        test_low_sample_excluded_from_strongest_and_weakest,
        test_strongest_and_weakest_respect_min_sample,
        test_monotonic_status_increasing,
        test_monotonic_status_decreasing,
        test_monotonic_status_not_monotonic,
        test_monotonic_status_insufficient_data,
        test_monotonicity_reports_all_six_scores,
        test_unavailable_horizon_events_excluded,
    ]

    for test in tests:
        test()

    print(f"{len(tests)} ready factor interactions tests passed.")
