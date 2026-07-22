"""unit tests for breakout_diagnostic_report.py"""
from breakout_diagnostic_report import build_breakout_diagnostic_report


def make_tf(direction, breakout_score=None, crossed=True, confirmed=False, decision=None, confidence=None):
    return {
        "quality": {
            direction: {
                "line": {"exists": True},
                "breakout_quality": {
                    "breakout_score": breakout_score,
                    "confirmed": confirmed,
                    "components": {
                        "cross": 1.0 if crossed else 0.0,
                        "close_distance": 15.0 if breakout_score is not None else None,
                        "candle_body": 10.0,
                        "rejection_wick": 5.0,
                    },
                },
                "structure_quality": {"structure_score": 80.0},
                "market_structure": {"alignment": "FAVORED"},
            }
        },
        "decision_details": {direction: {"decision": decision, "confidence": confidence, "decision_score": breakout_score}},
    }


def test_report_empty():
    r = build_breakout_diagnostic_report({})
    assert isinstance(r, dict)
    assert r["top10_closest_rejected"] == []


def test_not_crossed_counts():
    all_results = {"SYM": {"1H": make_tf("LONG", breakout_score=10.0, crossed=False)}}
    rep = build_breakout_diagnostic_report(all_results)
    assert rep["totals"]["breakout_not_crossed"] >= 1
    assert rep["bottleneck_counts"]["NOT_CROSSED"] >= 1


def test_weakest_component_classification():
    # make distance weak by giving small close_distance and low score
    tf = make_tf("SHORT", breakout_score=45.0, crossed=True)
    tf["quality"]["SHORT"]["breakout_quality"]["components"]["close_distance"] = 1.0
    all_results = {"T": {"4H": tf}}
    rep = build_breakout_diagnostic_report(all_results)
    assert rep["bottleneck_counts"]["WEAK_DISTANCE"] >= 1


def test_top10_sorted_and_limited():
    all_results = {}
    for i in range(20):
        all_results[f"S{i}"] = {"15m": make_tf("LONG", breakout_score=40.0 + i)}
    rep = build_breakout_diagnostic_report(all_results)
    top = rep["top10_closest_rejected"]
    assert len(top) <= 10
    # ensure sorted by gap ascending
    gaps = [c["breakout_score_gap"] for c in top]
    assert gaps == sorted(gaps)


if __name__ == "__main__":
    test_report_empty()
    test_not_crossed_counts()
    test_weakest_component_classification()
    test_top10_sorted_and_limited()
    print("breakout_diagnostic_report tests passed")
