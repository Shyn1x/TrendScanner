"""
test_explain_engine.py
~~~~~~~~~~~~~~~~~~~~~~
Unit tests for explain_engine.build_timeframe_explanation.
"""

import copy
import math

from explain_engine import build_timeframe_explanation

_passed = []
_failed = []


def ok(name: str, detail: str = ""):
    _passed.append(name)
    print(f"  ✓  {name}" + (f"  ({detail})" if detail else ""))


def fail(name: str, reason: str):
    _failed.append(name)
    print(f"  ✗  {name}  ←  {reason}")


def check(cond: bool, name: str, reason: str = "", detail: str = "") -> bool:
    if cond:
        ok(name, detail)
    else:
        fail(name, reason or "assertion failed")
    return cond


def section(title: str):
    print(f"\n{'-'*60}\n  {title}\n{'-'*60}")


def _make_tf_result(direction: str, decision: str, score: float, conf: float, confirmed: bool, breakout_confirmed: bool, tq: float = 60.0, vq: float = 60.0, bq: float = 60.0, structure: str = "TREND"):
    return {
        "trend": "BULLISH" if direction == "LONG" else "BEARISH",
        "signal": direction,
        "confidence": conf,
        "confidence_label": "HIGH",
        "quality": {
            direction: {
                "line": {"start": 0},
                "trend_quality": {"trend_quality_score": tq},
                "volume_quality": {"volume_score": vq},
                "breakout_quality": {"breakout_score": bq, "confirmed": breakout_confirmed},
                "market_structure": {"structure": structure},
                "confidence": {"confidence": conf, "label": "HIGH"},
                "confirmed": confirmed,
            }
        },
        "decision": decision,
        "decision_details": {
            direction: {
                "decision": decision,
                "decision_score": score,
                "confidence": conf,
                "confidence_label": "HIGH",
                "breakout_confirmed": breakout_confirmed,
                "component_scores": {
                    "trend_quality": tq,
                    "volume_quality": vq,
                    "breakout_quality": bq,
                    "structure_quality": 80.0,
                },
                "positive_factors": ["Positive one"],
                "warning_factors": ["Warning one"],
                "blockers": [],
            }
        }
    }


def test_take_long_explanation():
    section("E1 — TAKE LONG explanation")
    result = _make_tf_result("LONG", "TAKE", 80.0, 75.0, True, True, tq=80.0, vq=85.0, bq=90.0)
    explanation = build_timeframe_explanation(result, "LONG")

    check(explanation["direction"] == "LONG", "E1.direction")
    check(explanation["decision"] == "TAKE", "E1.decision")
    check(explanation["decision_score"] == 80.0, "E1.score")
    check(explanation["confidence"] == 75.0, "E1.confidence")
    check(explanation["breakout_confirmed"] is True, "E1.breakout_confirmed")
    check(explanation["scores"]["breakout"] == 90.0, "E1.breakout_score")
    check(explanation["distance_to_watch"] == 0.0, "E1.watch_distance")
    check(explanation["distance_to_take"] == 0.0, "E1.take_distance")
    check(explanation["pipeline_stage"] == "decision", "E1.stage")
    check(explanation["primary_blocker"] == "", "E1.primary_blocker")
    check(explanation["secondary_blocker"] == "", "E1.secondary_blocker")
    check(explanation["positive_factors"] == ["Positive one"], "E1.positive")
    check(explanation["warnings"] == ["Warning one"], "E1.warnings")
    check(explanation["blockers"] == [], "E1.blockers")


def test_watch_short_unconfirmed_breakout():
    section("E2 — WATCH SHORT unconfirmed breakout")
    result = _make_tf_result("SHORT", "WATCH", 55.0, 60.0, False, False, tq=55.0, vq=60.0, bq=30.0)
    explanation = build_timeframe_explanation(result, "SHORT")

    check(explanation["direction"] == "SHORT", "E2.direction")
    check(explanation["decision"] == "WATCH", "E2.decision")
    check(explanation["breakout_confirmed"] is False, "E2.breakout_confirmed")
    check(explanation["pipeline_stage"] == "breakout_confirmation", "E2.stage")
    check(explanation["distance_to_watch"] == 0.0, "E2.watch_distance")
    check(explanation["distance_to_take"] == 15.0, "E2.take_distance")


def test_skip_multiple_blockers():
    section("E3 — SKIP with multiple blockers")
    result = _make_tf_result("LONG", "SKIP", 10.0, 35.0, False, False, tq=30.0, vq=20.0, bq=0.0)
    detail = result["decision_details"]["LONG"]
    detail["blockers"] = [
        {"code": "CONFIDENCE_TOO_LOW", "message": "Low confidence"},
        {"code": "BREAKOUT_NOT_CONFIRMED", "message": "Breakout missing"},
    ]
    detail["warning_factors"] = ["Weak volume"]
    explanation = build_timeframe_explanation(result, "LONG")

    check(explanation["primary_blocker"] == "Low confidence", "E3.primary")
    check(explanation["secondary_blocker"] == "Breakout missing", "E3.secondary")
    check(explanation["raw_blocker_codes"] == ["CONFIDENCE_TOO_LOW", "BREAKOUT_NOT_CONFIRMED"], "E3.codes")
    check(explanation["blockers"] == ["Low confidence", "Breakout missing"], "E3.blockers")


def test_warning_used_as_secondary_blocker():
    section("E4 — warning as secondary blocker")
    result = _make_tf_result("SHORT", "SKIP", 45.0, 45.0, False, False, tq=45.0, vq=50.0, bq=10.0)
    detail = result["decision_details"]["SHORT"]
    detail["blockers"] = [{"code": "BREAKOUT_NOT_CONFIRMED", "message": "Breakout missing"}]
    detail["warning_factors"] = ["Low quality"]
    explanation = build_timeframe_explanation(result, "SHORT")

    check(explanation["primary_blocker"] == "Breakout missing", "E4.primary")
    check(explanation["secondary_blocker"] == "Low quality", "E4.secondary")


def test_breakout_confirmation_diagnostics():
    section("E5 — breakout confirmation diagnostics")
    result = _make_tf_result("LONG", "WATCH", 45.0, 55.0, False, False, tq=55.0, vq=60.0, bq=20.0)
    result["quality"]["LONG"]["breakout_quality"] = {
        "breakout_score": 20.0,
        "confirmed": False,
        "line_price": 100.0,
        "signal_close": 99.5,
        "atr": 0.5,
        "distance_atr": -1.0,
        "body_ratio": 0.625,
        "rejection_wick_ratio": 0.3,
        "components": {
            "cross": 0.0,
            "close_distance": 0.0,
            "candle_body": 15.0,
            "rejection_wick": 10.0,
        },
    }
    original = copy.deepcopy(result)
    explanation = build_timeframe_explanation(result, "LONG")

    check(explanation["breakout_line_price"] == 100.0, "E5.line_price")
    check(explanation["breakout_close"] == 99.5, "E5.close")
    check(explanation["breakout_distance_direction"] == "LONG", "E5.distance_direction")
    check(explanation["breakout_distance"] == -0.5, "E5.distance")
    check(explanation["breakout_atr"] == 0.5, "E5.atr")
    check(explanation["distance_atr_ratio"] == -1.0, "E5.distance_atr")
    check(explanation["candle_body_ratio"] == 0.625, "E5.body_ratio")
    check(explanation["rejection_wick_ratio"] == 0.3, "E5.wick_ratio")
    check(explanation["breakout_cross_score"] == 0.0, "E5.cross_score")
    check(explanation["breakout_distance_score"] == 0.0, "E5.distance_score")
    check(explanation["breakout_body_score"] == 15.0, "E5.body_score")
    check(explanation["breakout_wick_score"] == 10.0, "E5.wick_score")
    check(explanation["breakout_confirmation_comparison"] == "breakout_score >= 50.0", "E5.comparison")
    check(explanation["breakout_confirmation_expression"] == "crossed and (breakout_score >= 50.0)", "E5.expression")
    check("required_breakout_distance" not in explanation, "E5.required_distance_removed")
    check(result == original, "E5.source_not_mutated")


def test_short_distance_directional_signs():
    section("E6 — SHORT directional breakout distance sign")
    result = _make_tf_result("SHORT", "WATCH", 45.0, 55.0, False, False, tq=55.0, vq=60.0, bq=20.0)
    result["quality"]["SHORT"]["breakout_quality"] = {
        "breakout_score": 20.0,
        "confirmed": False,
        "line_price": 100.0,
        "signal_close": 99.0,
        "atr": 0.5,
        "distance_atr": 2.0,
        "body_ratio": 0.6,
        "rejection_wick_ratio": 0.1,
        "components": {
            "cross": 0.0,
            "close_distance": 0.0,
            "candle_body": 15.0,
            "rejection_wick": 20.0,
        },
    }
    explanation = build_timeframe_explanation(result, "SHORT")

    check(explanation["breakout_distance_direction"] == "SHORT", "E6.distance_direction")
    check(explanation["breakout_distance"] == 1.0, "E6.positive_distance_short")
    check(explanation["distance_atr_ratio"] == 2.0, "E6.positive_distance_atr_short")


def test_short_distance_directional_signs_above_line():
    section("E7 — SHORT directional breakout distance negative when above line")
    result = _make_tf_result("SHORT", "WATCH", 45.0, 55.0, False, False, tq=55.0, vq=60.0, bq=20.0)
    result["quality"]["SHORT"]["breakout_quality"] = {
        "breakout_score": 20.0,
        "confirmed": False,
        "line_price": 100.0,
        "signal_close": 101.0,
        "atr": 0.5,
        "distance_atr": -2.0,
        "body_ratio": 0.6,
        "rejection_wick_ratio": 0.1,
        "components": {
            "cross": 0.0,
            "close_distance": 0.0,
            "candle_body": 15.0,
            "rejection_wick": 20.0,
        },
    }
    explanation = build_timeframe_explanation(result, "SHORT")

    check(explanation["breakout_distance_direction"] == "SHORT", "E7.distance_direction")
    check(explanation["breakout_distance"] == -1.0, "E7.negative_distance_short")
    check(explanation["distance_atr_ratio"] == -2.0, "E7.negative_distance_atr_short")


def test_missing_quality_components():
    section("E5 — missing quality components")
    result = {
        "trend": "LONG",
        "signal": "LONG",
        "quality": {},
        "decision": "SKIP",
        "decision_details": {
            "LONG": {
                "decision": "SKIP",
                "decision_score": 0.0,
                "confidence": None,
                "confidence_label": "LOW",
                "breakout_confirmed": False,
                "component_scores": {},
                "positive_factors": [],
                "warning_factors": [],
                "blockers": [],
            }
        }
    }
    explanation = build_timeframe_explanation(result, "LONG")

    check(explanation["pipeline_stage"] == "trendline", "E5.stage")
    check(explanation["scores"]["trend_quality"] is None, "E5.tq_none")
    check(explanation["confidence"] is None, "E5.conf_none")


def test_malformed_input_and_nan_inf():
    section("E6 — malformed input, NaN and inf")
    result = {
        "trend": "LONG",
        "signal": "LONG",
        "quality": {
            "LONG": {
                "line": {"start": 0},
                "trend_quality": {"trend_quality_score": float("nan")},
                "volume_quality": {"volume_score": float("inf")},
                "breakout_quality": {"breakout_score": 5.0, "confirmed": False},
                "market_structure": {"structure": "UNKNOWN"},
                "confidence": {"confidence": float("inf"), "label": "HIGH"},
                "confirmed": False,
            }
        },
        "decision": "WATCH",
        "decision_details": {
            "LONG": {
                "decision": "WATCH",
                "decision_score": float("nan"),
                "confidence": float("inf"),
                "confidence_label": "HIGH",
                "breakout_confirmed": False,
                "component_scores": {"trend_quality": float("nan"), "volume_quality": float("inf"), "breakout_quality": None, "structure_quality": None},
                "positive_factors": [],
                "warning_factors": ["Unreliable data"],
                "blockers": [],
            }
        }
    }
    explanation = build_timeframe_explanation(result, "LONG")

    check(explanation["decision_score"] is None, "E6.score_none")
    check(explanation["confidence"] is None, "E6.conf_none")
    check(explanation["scores"]["trend_quality"] is None, "E6.tq_none")
    check(explanation["scores"]["volume"] is None, "E6.vq_none")
    check(explanation["pipeline_stage"] == "breakout_confirmation", "E6.stage")


def test_distance_already_above_threshold_returns_zero():
    section("E7 — distance above threshold returns 0")
    result = _make_tf_result("SHORT", "WATCH", 90.0, 90.0, True, True, tq=90.0, vq=90.0, bq=90.0)
    explanation = build_timeframe_explanation(result, "SHORT")

    check(explanation["distance_to_watch"] == 0.0, "E7.watch_zero")
    check(explanation["distance_to_take"] == 0.0, "E7.take_zero")


def test_source_dictionary_not_mutated():
    section("E8 — source dictionary not mutated")
    result = _make_tf_result("LONG", "WATCH", 55.0, 55.0, True, True)
    original = copy.deepcopy(result)
    _ = build_timeframe_explanation(result, "LONG")
    check(result == original, "E8.no_mutation")


def main():
    print("\n" + "="*60)
    print("  EXPLAIN ENGINE TESTS")
    print("="*60)

    tests = [
        test_take_long_explanation,
        test_watch_short_unconfirmed_breakout,
        test_skip_multiple_blockers,
        test_warning_used_as_secondary_blocker,
        test_missing_quality_components,
        test_malformed_input_and_nan_inf,
        test_distance_already_above_threshold_returns_zero,
        test_source_dictionary_not_mutated,
    ]

    for test in tests:
        try:
            test()
        except Exception as exc:
            fail(test.__name__, f"exception {exc}")

    print("\n" + "="*60)
    print(f"Passed {_passed.__len__()} / {len(tests)} tests")
    if _failed:
        print(f"Failed {_failed.__len__()} tests")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
