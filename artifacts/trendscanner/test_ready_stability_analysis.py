from __future__ import annotations

import copy

from ready_stability_analysis import analyze_ready_stability


def _event(symbol="BTC", direction="LONG", timestamp=1_700_000_000_000, mfe=2.0, mae=1.0, close=0.5, available=True, confidence=70.0):
    return {"symbol": symbol, "direction": direction, "ready_timestamp": timestamp, "ready_confidence": confidence, "ready_decision_score": 60.0, "breakout_score": 70.0, "trend_quality": 70.0, "volume_quality": 70.0, "structure_quality": 70.0, "structure_state": "BULLISH", "horizons": {"5": {"available": available, "mfe_pct": mfe, "mae_pct": mae, "close_return_pct": close}}}


def _payload(events):
    return {"runs": [{"status": "OK", "events": events}]}


def test_time_thirds_use_time_range():
    report = analyze_ready_stability(_payload([_event(timestamp=0), _event(timestamp=10), _event(timestamp=20), _event(timestamp=30)]))
    assert [row["n"] for row in report["time_thirds"]] == [1, 1, 2]


def test_month_grouping_uses_utc():
    report = analyze_ready_stability(_payload([_event(timestamp=1_704_067_200_000), _event(timestamp=1_706_745_600_000)]))
    assert [row["month"] for row in report["monthly"]] == ["2024-01", "2024-02"]


def test_long_short_are_independent():
    report = analyze_ready_stability(_payload([_event(direction="LONG", mfe=3, mae=1), _event(direction="SHORT", mfe=1, mae=3)]))
    row = report["time_thirds"][2]
    assert row["long"]["mfe_minus_mae_pct"] == 2
    assert row["short"]["mfe_minus_mae_pct"] == -2


def test_per_symbol_aggregation_and_comparison_minimum():
    events = [_event(symbol="A", direction="LONG") for _ in range(5)] + [_event(symbol="A", direction="SHORT", mfe=1, mae=2) for _ in range(5)]
    report = analyze_ready_stability(_payload(events))
    assert report["per_symbol"]["rows"][0]["comparable"] is True
    assert report["per_symbol"]["summary"]["long_greater_than_short_symbols"] == 1


def test_month_minimum_sample_rules():
    report = analyze_ready_stability(_payload([_event() for _ in range(9)] + [_event(direction="SHORT") for _ in range(10)]))
    row = report["monthly"][0]
    assert row["long_low_sample"] is True and row["short_low_sample"] is False and row["eligible_for_ranking"] is False


def test_stable_positive_detection():
    events = []
    for timestamp in (0, 100, 200):
        events.extend(_event(timestamp=timestamp, confidence=70, mfe=3, mae=1) for _ in range(8))
    report = analyze_ready_stability(_payload(events))
    assert any(row["factor"] == "confidence_bucket" for row in report["factor_stability"]["stable_positive"])


def test_sign_change_detection():
    events = []
    for timestamp, mfe, mae in ((0, 3, 1), (100, 1, 3), (200, 3, 1)):
        events.extend(_event(timestamp=timestamp, confidence=70, mfe=mfe, mae=mae) for _ in range(8))
    report = analyze_ready_stability(_payload(events))
    assert any(row["factor"] == "confidence_bucket" for row in report["factor_stability"]["sign_changing"])


def test_same_timestamp_concentration():
    report = analyze_ready_stability(_payload([_event(symbol="A", timestamp=1), _event(symbol="B", timestamp=1), _event(symbol="C", timestamp=2)]))
    diagnostic = report["correlated_event_diagnostic"]
    assert diagnostic["unique_ready_timestamps"] == 2
    assert diagnostic["max_symbols_at_one_timestamp"] == 2
    assert abs(diagnostic["shared_timestamp_event_share"] - 200 / 3) < 1e-9


def test_unavailable_h5_is_excluded():
    report = analyze_ready_stability(_payload([_event(available=False), _event(symbol="B")]))
    assert report["summary"]["available_events"] == 1


def test_input_not_mutated():
    payload = _payload([_event()])
    original = copy.deepcopy(payload)
    analyze_ready_stability(payload)
    assert payload == original


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)} ready stability analysis tests passed.")