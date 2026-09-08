from __future__ import annotations

import copy

from ready_regime_comparison import analyze_regime_comparison, build_console_summary


def _event(symbol="A", direction="LONG", timestamp=1, mfe=3.0, mae=1.0, available=True, state="BULLISH"):
    return {"symbol": symbol, "timeframe": "4h", "direction": direction, "ready_timestamp": timestamp, "ready_confidence": 70, "ready_decision_score": 70, "breakout_score": 70, "trend_quality": 70, "volume_quality": 70, "structure_quality": 70, "structure_state": state, "ready_warnings": [], "horizons": {"5": {"available": available, "mfe_pct": mfe, "mae_pct": mae, "close_return_pct": 0.5}}}


def _payload(events):
    return {"runs": [{"status": "OK", "events": events}]}


def _report(current, holdout):
    return analyze_regime_comparison(_payload(current), _payload(holdout))


def _events(period_timestamp, direction, mfe, mae, count=10, symbol="A", state="BULLISH"):
    return [_event(symbol=symbol, direction=direction, timestamp=period_timestamp + index, mfe=mfe, mae=mae, state=state) for index in range(count)]


def test_periods_not_mixed_and_unavailable_excluded():
    report = _report([_event(timestamp=100), _event(timestamp=101, available=False)], [_event(timestamp=1)])
    assert report["summary"]["available_events"] == {"CURRENT": 1, "HOLDOUT": 1}
    assert report["baseline"]["CURRENT"]["LONG"]["n"] == 1


def test_direction_metrics_are_independent():
    report = _report([_event(direction="LONG", mfe=3, mae=1), _event(direction="SHORT", mfe=1, mae=3)], [_event(timestamp=-10)])
    assert report["baseline"]["CURRENT"]["LONG"]["mfe_minus_mae_pct"] == 2
    assert report["baseline"]["CURRENT"]["SHORT"]["mfe_minus_mae_pct"] == -2


def test_stable_positive_negative_and_flipping():
    current = (
        _events(100, "LONG", 3, 1, state="RANGE")
        + _events(100, "SHORT", 1, 3, state="RANGE")
        + _events(200, "SHORT", 1, 3, state="BULLISH")
    )
    holdout = (
        _events(1, "LONG", 3, 1, state="RANGE")
        + _events(1, "SHORT", 1, 3, state="RANGE")
        + _events(20, "SHORT", 3, 1, state="BULLISH")
    )
    factors = _report(current, holdout)["factors"]
    assert factors["direction_stable_positive"]
    assert factors["direction_stable_negative"]
    assert factors["direction_flipping"]


def test_symbol_long_short_stable_and_flipped():
    current = _events(100, "LONG", 3, 1, 5, "LONG") + _events(100, "SHORT", 1, 3, 5, "LONG") + _events(200, "LONG", 1, 3, 5, "SHORT") + _events(200, "SHORT", 3, 1, 5, "SHORT") + _events(300, "LONG", 3, 1, 5, "FLIP") + _events(300, "SHORT", 1, 3, 5, "FLIP")
    holdout = _events(1, "LONG", 3, 1, 5, "LONG") + _events(1, "SHORT", 1, 3, 5, "LONG") + _events(10, "LONG", 1, 3, 5, "SHORT") + _events(10, "SHORT", 3, 1, 5, "SHORT") + _events(20, "LONG", 1, 3, 5, "FLIP") + _events(20, "SHORT", 3, 1, 5, "FLIP")
    counts = _report(current, holdout)["symbols"]["counts"]
    assert counts["LONG_STABLE"] == 1 and counts["SHORT_STABLE"] == 1 and counts["FLIPPED_LONG_TO_SHORT"] == 1


def test_minimum_sample_and_structure_alignment():
    report = _report(_events(100, "LONG", 3, 1, 9, state="BULLISH"), _events(1, "LONG", 3, 1, 9, state="BULLISH"))
    assert not report["factors"]["direction_stable_positive"]
    aligned = [row for row in report["structure_alignment"] if row["period"] == "CURRENT" and row["direction"] == "LONG" and row["structure_state"] == "BULLISH"][0]
    assert aligned["alignment"] == "ALIGNED" and aligned["low_sample"] is True


def test_input_not_mutated():
    current, holdout = _payload([_event(timestamp=100)]), _payload([_event(timestamp=1)])
    original = copy.deepcopy((current, holdout))
    _ = analyze_regime_comparison(current, holdout)
    assert (current, holdout) == original


def test_console_summary_handles_empty_periods():
    summary = build_console_summary(_report([], []))
    for period in ("CURRENT", "HOLDOUT"):
        assert f"{period}: LONG N=0 edge=-; SHORT N=0 edge=-; winner=INSUFFICIENT_SAMPLE" in summary


def test_console_summary_handles_missing_directions():
    report = _report([_event(timestamp=100)], [_event(timestamp=1, direction="SHORT")])
    original = copy.deepcopy(report)
    summary = build_console_summary(report)
    assert "CURRENT: LONG N=1 edge=2.00; SHORT N=0 edge=-; winner=INSUFFICIENT_SAMPLE" in summary
    assert "HOLDOUT: LONG N=0 edge=-; SHORT N=1 edge=2.00; winner=INSUFFICIENT_SAMPLE" in summary
    assert report == original


def test_console_summary_does_not_choose_a_winner_for_tied_edges():
    current = [_event(timestamp=100, direction=direction) for direction in ("LONG", "SHORT")]
    holdout = [_event(timestamp=1, direction=direction) for direction in ("LONG", "SHORT")]
    summary = build_console_summary(_report(current, holdout))
    assert summary.count("winner=NO_CLEAR_EDGE") == 2


def test_console_summary_preserves_observed_direction_winners():
    current = [_event(timestamp=100), _event(timestamp=100, direction="SHORT", mfe=1, mae=3)]
    holdout = [_event(timestamp=1, mfe=1, mae=3), _event(timestamp=1, direction="SHORT")]
    summary = build_console_summary(_report(current, holdout))
    assert "CURRENT: LONG N=1 edge=2.00; SHORT N=1 edge=-2.00; winner=LONG" in summary
    assert "HOLDOUT: LONG N=1 edge=-2.00; SHORT N=1 edge=2.00; winner=SHORT" in summary


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)} ready regime comparison tests passed.")
