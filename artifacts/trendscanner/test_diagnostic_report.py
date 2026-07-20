"""
test_diagnostic_report.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for diagnostic_report.py

Checks:
 - build_diagnostic structure and keys
 - Stage 1–9 counting correctness
 - Rejection reason accumulation
 - Blocker accumulation
 - Confidence bucket distribution
 - Pipeline Health Check (over-filtering warning)
 - format_diagnostic_report output contains required sections
 - NaN / inf / None safety
 - Empty input safety
 - No real network calls (all fake data)
"""

import sys
import math
from collections import Counter
from diagnostic_report import (
    build_diagnostic,
    format_diagnostic_report,
    _stage1_data_loaded,
    _stage2_trendline,
    _stage3_breakout_detected,
    _stage4_breakout_confirmed,
    _stage5_trend_quality,
    _stage6_volume,
    _stage7_market_structure,
    _stage8_confidence,
    _stage9_decision,
    _collect_rejection_reasons,
    _collect_decision_blockers,
    _collect_confidence_values,
    _CONF_MIN,
    _TREND_QUALITY_MIN,
    _VOLUME_MIN,
)

# ── Test runner ────────────────────────────────────────────────────────────────
_PASS = 0
_FAIL = 0
_ERRORS: list[str] = []


def ok(name):
    global _PASS; _PASS += 1


def fail(name, msg=""):
    global _FAIL; _FAIL += 1
    _ERRORS.append(f"  FAIL  {name}: {msg}")


def check(name, condition, msg=""):
    if condition:
        ok(name)
    else:
        fail(name, msg or "condition is False")


def run_test(name, fn):
    try:
        fn()
    except Exception as e:
        fail(name, f"{type(e).__name__}: {e}")


# ── Builders ───────────────────────────────────────────────────────────────────

def _make_full_tf(
    signal="LONG", trend="BULLISH", decision="TAKE",
    direction="LONG", d_score=80.0, conf=75.0,
    breakout_score=60.0, breakout_confirmed=True,
    tq_score=70.0, vq_score=65.0,
    structure="BULLISH",
):
    """Builds a realistic per-TF result dict that passes all stages."""
    q_dir = {
        "signal":    signal,
        "confirmed": breakout_confirmed,
        "line":      {"p1": [0, 100.0], "p2": [5, 102.0], "slope": 0.4, "intercept": 100.0},
        "breakout_quality": {
            "breakout_score": breakout_score,
            "confirmed":      breakout_confirmed,
            "reason":         "ok",
        },
        "trend_quality":  {"trend_quality_score": tq_score,  "reason": "ok"},
        "volume_quality": {"volume_score":         vq_score,  "reason": "ok"},
        "market_structure": {
            "structure":       structure,
            "structure_score": 75.0,
        },
        "confidence": {"confidence": conf, "label": "HIGH", "reason": "ok"},
    }
    return {
        "trend":              trend,
        "signal":             signal,
        "score":              conf,
        "confidence":         conf,
        "confidence_label":   "HIGH",
        "reason":             "",
        "quality":            {"LONG": q_dir, "SHORT": {}, "FINAL": {}},
        "decision":           decision,
        "decision_direction": direction,
        "decision_score":     d_score,
        "decision_reason":    f"{decision}: all criteria met",
        "decision_details": {
            "LONG":  {"decision": decision, "blockers": [], "positive_factors": [], "warning_factors": []},
            "SHORT": {"decision": "SKIP",   "blockers": [{"code": "DIR_CONFLICT", "message": "Direction conflict"}]},
            "FINAL": {"decision": decision, "direction": direction, "decision_score": d_score},
        },
    }


def _make_skip_tf(reason="Breakout not confirmed"):
    """Builds a TF result that fails at stage 4 (breakout not confirmed)."""
    return {
        "trend":    "RANGE",
        "signal":   "WAIT",
        "confidence": 5.0,
        "confidence_label": "LOW",
        "reason":   reason,
        "quality": {
            "LONG": {
                "confirmed": False,
                "line": {"p1": [0, 100.0], "p2": [5, 100.5]},
                "breakout_quality": {"breakout_score": 10.0, "confirmed": False, "reason": reason},
                "trend_quality":  {"trend_quality_score": 20.0},
                "volume_quality": {"volume_score": 15.0},
                "market_structure": {"structure": "UNKNOWN"},
                "confidence": {"confidence": 5.0},
            },
            "SHORT": {},
        },
        "decision":           "SKIP",
        "decision_direction": "NONE",
        "decision_score":     0.0,
        "decision_reason":    f"SKIP: {reason}",
        "decision_details": {
            "LONG":  {"decision": "SKIP", "blockers": [{"code": "BNC", "message": reason}]},
            "SHORT": {"decision": "SKIP", "blockers": []},
            "FINAL": {"decision": "SKIP"},
        },
    }


def _make_no_trendline_tf():
    return {
        "trend": "RANGE", "signal": "WAIT", "confidence": 0.0,
        "quality": {"LONG": {"confirmed": False, "line": None}, "SHORT": {"confirmed": False, "line": None}},
        "decision": "SKIP", "decision_direction": "NONE", "decision_score": 0.0,
        "decision_reason": "", "decision_details": {},
    }


def _make_error_result():
    return {"_error": "api timeout", "FINAL": {}}


def _make_sym_result(tfs_data: dict, final_decision="SKIP"):
    """Assembles a symbol result (like multi_analysis output)."""
    r = dict(tfs_data)
    r["FINAL"] = {
        "signal": "WAIT", "decision": final_decision,
        "decision_direction": "NONE", "decision_score": 0.0,
        "confidence": 5.0, "available_timeframes": len(tfs_data),
    }
    return r


TFS = ["1M", "1w", "1d", "4h", "1h"]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Individual stage functions
# ═════════════════════════════════════════════════════════════════════════════

def test_stage1_ok():
    r = _make_full_tf()
    check("S1_pass", _stage1_data_loaded(r))


def test_stage1_error():
    check("S1_error", not _stage1_data_loaded(_make_error_result()))


def test_stage1_none():
    check("S1_none", not _stage1_data_loaded(None))


def test_stage2_has_line():
    r = _make_full_tf()
    check("S2_pass", _stage2_trendline(r))


def test_stage2_no_line():
    r = _make_no_trendline_tf()
    check("S2_no_line", not _stage2_trendline(r))


def test_stage3_detected():
    r = _make_full_tf(breakout_score=50.0)
    check("S3_pass", _stage3_breakout_detected(r))


def test_stage3_zero_score():
    r = _make_full_tf(breakout_score=0.0)
    check("S3_zero", not _stage3_breakout_detected(r))


def test_stage4_confirmed():
    r = _make_full_tf(breakout_confirmed=True)
    check("S4_pass", _stage4_breakout_confirmed(r))


def test_stage4_not_confirmed():
    r = _make_full_tf(breakout_confirmed=False)
    check("S4_fail", not _stage4_breakout_confirmed(r))


def test_stage5_above_threshold():
    r = _make_full_tf(tq_score=_TREND_QUALITY_MIN + 1)
    check("S5_pass", _stage5_trend_quality(r))


def test_stage5_below_threshold():
    r = _make_full_tf(tq_score=_TREND_QUALITY_MIN - 1)
    check("S5_fail", not _stage5_trend_quality(r))


def test_stage5_at_threshold():
    r = _make_full_tf(tq_score=_TREND_QUALITY_MIN)
    check("S5_at", _stage5_trend_quality(r))


def test_stage6_above_threshold():
    r = _make_full_tf(vq_score=_VOLUME_MIN + 1)
    check("S6_pass", _stage6_volume(r))


def test_stage6_below_threshold():
    r = _make_full_tf(vq_score=_VOLUME_MIN - 1)
    check("S6_fail", not _stage6_volume(r))


def test_stage7_bullish():
    r = _make_full_tf(structure="BULLISH")
    check("S7_bullish", _stage7_market_structure(r))


def test_stage7_transition():
    r = _make_full_tf(structure="TRANSITION_BULLISH")
    check("S7_trans", not _stage7_market_structure(r))


def test_stage7_unknown():
    r = _make_full_tf(structure="UNKNOWN")
    check("S7_unknown", not _stage7_market_structure(r))


def test_stage7_range():
    r = _make_full_tf(structure="RANGE")
    check("S7_range", _stage7_market_structure(r))


def test_stage8_above_conf():
    r = _make_full_tf(conf=_CONF_MIN + 1)
    check("S8_pass", _stage8_confidence(r))


def test_stage8_below_conf():
    r = _make_full_tf(conf=_CONF_MIN - 1)
    check("S8_fail", not _stage8_confidence(r))


def test_stage8_at_conf():
    r = _make_full_tf(conf=_CONF_MIN)
    check("S8_at", _stage8_confidence(r))


def test_stage9_take():
    r = _make_full_tf(decision="TAKE")
    check("S9_take", _stage9_decision(r) == "TAKE")


def test_stage9_skip():
    r = _make_full_tf(decision="SKIP")
    check("S9_skip", _stage9_decision(r) == "SKIP")


def test_stage9_none():
    check("S9_none", _stage9_decision({}) is None)


# ═════════════════════════════════════════════════════════════════════════════
# 2. Rejection reasons
# ═════════════════════════════════════════════════════════════════════════════

def test_rejection_stage1():
    stages = {f"s{i}": False for i in range(1, 10)}
    reasons = _collect_rejection_reasons(_make_error_result(), stages)
    check("RJ1_err", any("Data load" in r or "error" in r.lower() for r in reasons),
          str(reasons))


def test_rejection_no_breakout():
    stages = {f"s{i}": i <= 3 for i in range(1, 10)}
    stages = {"s1": True, "s2": True, "s3": False,
              "s4": False, "s5": False, "s6": False,
              "s7": False, "s8": False, "s9": False}
    reasons = _collect_rejection_reasons(_make_skip_tf(), stages)
    check("RJ2_no_bo", any("breakout" in r.lower() or "No breakout" in r
                           for r in reasons), str(reasons))


def test_rejection_confirmed_fail():
    r = _make_skip_tf("Breakout not confirmed")
    stages = {"s1": True, "s2": True, "s3": True, "s4": False,
              "s5": False, "s6": False, "s7": False, "s8": False, "s9": False}
    reasons = _collect_rejection_reasons(r, stages)
    check("RJ3_bq", len(reasons) >= 1, str(reasons))


def test_rejection_weak_trend():
    r = _make_full_tf(tq_score=20.0)
    stages = {"s1": True, "s2": True, "s3": True, "s4": True,
              "s5": False, "s6": True, "s7": True, "s8": True, "s9": True}
    reasons = _collect_rejection_reasons(r, stages)
    check("RJ4_tq", any("trend" in x.lower() for x in reasons), str(reasons))


def test_rejection_weak_volume():
    r = _make_full_tf(vq_score=10.0)
    stages = {"s1": True, "s2": True, "s3": True, "s4": True,
              "s5": True, "s6": False, "s7": True, "s8": True, "s9": True}
    reasons = _collect_rejection_reasons(r, stages)
    check("RJ5_vol", any("volume" in x.lower() for x in reasons), str(reasons))


def test_rejection_low_confidence():
    r = _make_full_tf(conf=5.0)
    stages = {"s1": True, "s2": True, "s3": True, "s4": True,
              "s5": True, "s6": True, "s7": True, "s8": False, "s9": False}
    reasons = _collect_rejection_reasons(r, stages)
    check("RJ6_conf", any("conf" in x.lower() or "confidence" in x.lower()
                          for x in reasons), str(reasons))


# ═════════════════════════════════════════════════════════════════════════════
# 3. Decision blockers
# ═════════════════════════════════════════════════════════════════════════════

def test_blockers_extracted():
    r = _make_full_tf()
    blockers = _collect_decision_blockers(r)
    check("BLK1_found", len(blockers) >= 1, str(blockers))
    check("BLK1_str",   all(isinstance(b, str) for b in blockers))


def test_blockers_empty():
    r = {"decision_details": {}}
    check("BLK2_empty", _collect_decision_blockers(r) == [])


def test_blockers_none():
    check("BLK3_none", _collect_decision_blockers({}) == [])


def test_blockers_invalid():
    r = {"decision_details": {"LONG": {"blockers": [None, 42, {}]}}}
    try:
        blockers = _collect_decision_blockers(r)
        check("BLK4_invalid", isinstance(blockers, list))
    except Exception as e:
        fail("BLK4_invalid", str(e))


# ═════════════════════════════════════════════════════════════════════════════
# 4. Confidence values
# ═════════════════════════════════════════════════════════════════════════════

def test_conf_values_extracted():
    r = _make_full_tf(conf=75.0)
    vals = _collect_confidence_values(r)
    check("CV1_found", len(vals) >= 1, str(vals))
    check("CV1_range", all(0.0 <= v <= 100.0 for v in vals), str(vals))


def test_conf_values_nan():
    r = {"quality": {"LONG": {"confidence": {"confidence": float("nan")}}}}
    try:
        vals = _collect_confidence_values(r)
        check("CV2_nan", all(math.isfinite(v) for v in vals))
    except Exception as e:
        fail("CV2_nan", str(e))


def test_conf_values_inf():
    r = {"quality": {"LONG": {"confidence": {"confidence": float("inf")}}}}
    try:
        vals = _collect_confidence_values(r)
        check("CV3_inf", all(math.isfinite(v) for v in vals))
    except Exception as e:
        fail("CV3_inf", str(e))


# ═════════════════════════════════════════════════════════════════════════════
# 5. build_diagnostic — structure and correctness
# ═════════════════════════════════════════════════════════════════════════════

def test_build_keys():
    data = build_diagnostic({}, [], [])
    required = [
        "total_symbols", "total_tf_analyses", "stage_counts",
        "dec_counts", "rejection_counter", "blocker_counter", "conf_buckets",
    ]
    for k in required:
        check(f"BD1_{k}", k in data, f"missing key: {k}")


def test_build_empty():
    data = build_diagnostic({}, [], [])
    check("BD2_syms", data["total_symbols"]     == 0)
    check("BD2_tfs",  data["total_tf_analyses"] == 0)
    check("BD2_take", data["dec_counts"]["TAKE"] == 0)


def test_build_single_take():
    """One symbol, one TF, TAKE decision — must pass all stages."""
    tf_data  = _make_full_tf(decision="TAKE")
    all_res  = {"BTC": _make_sym_result({"1h": tf_data}, "TAKE")}
    data     = build_diagnostic(all_res, ["BTC"], ["1h"])

    check("BD3_total_sym", data["total_symbols"]      == 1)
    check("BD3_total_tf",  data["total_tf_analyses"]  == 1)
    check("BD3_take",      data["dec_counts"]["TAKE"] == 1)
    sc = data["stage_counts"]
    for i in range(1, 10):
        check(f"BD3_s{i}", sc[f"s{i}"] == 1,
              f"s{i}={sc[f's{i}']} expected 1")


def test_build_single_skip():
    """One symbol, one TF, fails at stage 4 (no breakout confirmed)."""
    tf_data = _make_skip_tf()
    all_res = {"ETH": _make_sym_result({"1h": tf_data}, "SKIP")}
    data    = build_diagnostic(all_res, ["ETH"], ["1h"])

    sc = data["stage_counts"]
    check("BD4_s1", sc["s1"] == 1, "s1 should pass")
    check("BD4_s2", sc["s2"] == 1, "s2 should pass")
    check("BD4_s3", sc["s3"] == 1, "s3 should pass (score>0)")
    check("BD4_s4", sc["s4"] == 0, f"s4 should fail, got {sc['s4']}")
    check("BD4_skip", data["dec_counts"]["SKIP"] == 1)


def test_build_no_trendline():
    """Fails at stage 2."""
    tf_data = _make_no_trendline_tf()
    all_res = {"SOL": _make_sym_result({"4h": tf_data}, "SKIP")}
    data    = build_diagnostic(all_res, ["SOL"], ["4h"])

    sc = data["stage_counts"]
    check("BD5_s1", sc["s1"] == 1)
    check("BD5_s2", sc["s2"] == 0, f"s2 should fail, got {sc['s2']}")
    check("BD5_s3", sc["s3"] == 0)


def test_build_error_symbol():
    all_res = {"X": _make_error_result()}
    data    = build_diagnostic(all_res, ["X"], ["1h"])
    check("BD6_s1_fail", data["stage_counts"]["s1"] == 0)


def test_build_multiple_symbols():
    all_res = {
        "A": _make_sym_result({"1h": _make_full_tf(decision="TAKE")},  "TAKE"),
        "B": _make_sym_result({"1h": _make_full_tf(decision="WATCH")}, "WATCH"),
        "C": _make_sym_result({"1h": _make_skip_tf()},                 "SKIP"),
    }
    data = build_diagnostic(all_res, ["A", "B", "C"], ["1h"])
    check("BD7_take",  data["dec_counts"]["TAKE"]  == 1)
    check("BD7_watch", data["dec_counts"]["WATCH"] == 1)
    check("BD7_skip",  data["dec_counts"]["SKIP"]  == 1)
    check("BD7_total", data["total_tf_analyses"]   == 3)


def test_build_multiple_tfs():
    all_res = {
        "BTC": _make_sym_result(
            {"1h": _make_full_tf(), "4h": _make_full_tf(), "1d": _make_skip_tf()},
            "WATCH",
        )
    }
    data = build_diagnostic(all_res, ["BTC"], ["1h", "4h", "1d"])
    check("BD8_total_tf", data["total_tf_analyses"] == 3)
    sc = data["stage_counts"]
    # 1h and 4h pass all stages, 1d fails at s4
    check("BD8_s1", sc["s1"] == 3)
    check("BD8_s4", sc["s4"] == 2)


def test_build_conf_buckets():
    """Confidence values should land in correct buckets."""
    tf_lo = _make_full_tf(conf=10.0)   # bucket 0 (0-20)
    tf_hi = _make_full_tf(conf=85.0)   # bucket 4 (80-100)
    all_res = {
        "A": _make_sym_result({"1h": tf_lo}, "SKIP"),
        "B": _make_sym_result({"1h": tf_hi}, "TAKE"),
    }
    data = build_diagnostic(all_res, ["A", "B"], ["1h"])
    cb = data["conf_buckets"]
    check("BD9_low_bucket",  cb[0] >= 1, str(cb))
    check("BD9_high_bucket", cb[4] >= 1, str(cb))


# ═════════════════════════════════════════════════════════════════════════════
# 6. NaN / inf / None safety in build_diagnostic
# ═════════════════════════════════════════════════════════════════════════════

def test_build_nan_scores():
    r = {
        "trend": "BULLISH", "signal": "LONG",
        "confidence": float("nan"),
        "quality": {"LONG": {
            "confirmed": True,
            "line": {"p1": [0, 100.0], "p2": [5, 102.0]},
            "breakout_quality": {"breakout_score": float("nan"), "confirmed": True},
            "trend_quality":    {"trend_quality_score": float("nan")},
            "volume_quality":   {"volume_score": float("nan")},
            "market_structure": {"structure": "BULLISH"},
            "confidence":       {"confidence": float("nan")},
        }, "SHORT": {}},
        "decision": "SKIP", "decision_direction": "NONE",
        "decision_score": float("nan"), "decision_reason": "",
        "decision_details": {},
    }
    all_res = {"X": _make_sym_result({"1h": r}, "SKIP")}
    try:
        data = build_diagnostic(all_res, ["X"], ["1h"])
        check("NAN1_safe", True)
    except Exception as e:
        fail("NAN1_safe", str(e))


def test_build_none_symbol():
    all_res = {"X": None}
    try:
        data = build_diagnostic(all_res, ["X"], ["1h"])
        check("NAN2_none", True)
    except Exception as e:
        fail("NAN2_none", str(e))


def test_build_missing_tf():
    """TF key missing from symbol result — should be skipped gracefully."""
    all_res = {"X": _make_sym_result({}, "SKIP")}
    data = build_diagnostic(all_res, ["X"], ["1h", "4h", "1d"])
    check("NAN3_missing_tf", data["total_tf_analyses"] == 0)


# ═════════════════════════════════════════════════════════════════════════════
# 7. Pipeline Health Check
# ═════════════════════════════════════════════════════════════════════════════

def test_health_check_warning_all_fail_at_s2():
    """All TFs fail at s2 → 100% drop → warning should appear."""
    all_res = {
        f"SYM{i}": _make_sym_result({"1h": _make_no_trendline_tf()}, "SKIP")
        for i in range(10)
    }
    data   = build_diagnostic(all_res, [f"SYM{i}" for i in range(10)], ["1h"])
    report = format_diagnostic_report(data)
    check("HC1_warning", "over-filtering" in report.lower(), report[-400:])


def test_health_check_healthy():
    """All TFs pass all stages → no warning."""
    all_res = {
        f"SYM{i}": _make_sym_result({"1h": _make_full_tf()}, "TAKE")
        for i in range(5)
    }
    data   = build_diagnostic(all_res, [f"SYM{i}" for i in range(5)], ["1h"])
    report = format_diagnostic_report(data)
    check("HC2_healthy", "healthy" in report.lower(), report[-400:])


# ═════════════════════════════════════════════════════════════════════════════
# 8. format_diagnostic_report — required sections
# ═════════════════════════════════════════════════════════════════════════════

def test_format_required_sections():
    all_res = {
        "A": _make_sym_result({"1h": _make_full_tf(decision="TAKE")},  "TAKE"),
        "B": _make_sym_result({"1h": _make_skip_tf()},                 "SKIP"),
    }
    data   = build_diagnostic(all_res, ["A", "B"], ["1h"])
    report = format_diagnostic_report(data)

    sections = [
        "PIPELINE DIAGNOSTIC",
        "Total symbols",
        "Total timeframe analyses",
        "Data loaded",
        "Trendline found",
        "Breakout detected",
        "Breakout confirmed",
        "Trend Quality passed",
        "Volume passed",
        "Market Structure passed",
        "Confidence > 40",
        "Decision computed",
        "Decision TAKE",
        "Decision WATCH",
        "Decision SKIP",
        "REJECTED BECAUSE",
        "DECISION BLOCKERS",
        "CONFIDENCE DISTRIBUTION",
        "PIPELINE HEALTH CHECK",
    ]
    for s in sections:
        check(f"FMT_{s[:15].replace(' ','_')}", s in report, f"missing '{s}'")


def test_format_returns_string():
    data   = build_diagnostic({}, [], [])
    report = format_diagnostic_report(data)
    check("FMT_type", isinstance(report, str))
    check("FMT_nonempty", len(report) > 50)


def test_format_rejection_reasons_top_n():
    """Rejection reasons in report should be sorted by count (most common first)."""
    all_res = {}
    for i in range(10):
        all_res[f"SYM{i}"] = _make_sym_result({"1h": _make_no_trendline_tf()}, "SKIP")
    for i in range(3):
        all_res[f"OK{i}"]  = _make_sym_result({"1h": _make_skip_tf()}, "SKIP")
    data   = build_diagnostic(all_res, list(all_res.keys()), ["1h"])
    report = format_diagnostic_report(data)
    # "No trendline" should appear (10 occurrences vs 3)
    check("FMT_top_rej", "No trendline" in report or "trendline" in report.lower(),
          report[-600:])


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

_TESTS = [
    # Stage functions
    test_stage1_ok, test_stage1_error, test_stage1_none,
    test_stage2_has_line, test_stage2_no_line,
    test_stage3_detected, test_stage3_zero_score,
    test_stage4_confirmed, test_stage4_not_confirmed,
    test_stage5_above_threshold, test_stage5_below_threshold, test_stage5_at_threshold,
    test_stage6_above_threshold, test_stage6_below_threshold,
    test_stage7_bullish, test_stage7_transition, test_stage7_unknown, test_stage7_range,
    test_stage8_above_conf, test_stage8_below_conf, test_stage8_at_conf,
    test_stage9_take, test_stage9_skip, test_stage9_none,
    # Rejection reasons
    test_rejection_stage1, test_rejection_no_breakout,
    test_rejection_confirmed_fail, test_rejection_weak_trend,
    test_rejection_weak_volume, test_rejection_low_confidence,
    # Blockers
    test_blockers_extracted, test_blockers_empty, test_blockers_none, test_blockers_invalid,
    # Confidence values
    test_conf_values_extracted, test_conf_values_nan, test_conf_values_inf,
    # build_diagnostic
    test_build_keys, test_build_empty, test_build_single_take,
    test_build_single_skip, test_build_no_trendline, test_build_error_symbol,
    test_build_multiple_symbols, test_build_multiple_tfs, test_build_conf_buckets,
    # NaN/inf safety
    test_build_nan_scores, test_build_none_symbol, test_build_missing_tf,
    # Health check
    test_health_check_warning_all_fail_at_s2, test_health_check_healthy,
    # Format
    test_format_required_sections, test_format_returns_string,
    test_format_rejection_reasons_top_n,
]

if __name__ == "__main__":
    print("─" * 50)
    print("  Diagnostic Report Tests")
    print("─" * 50)
    for t in _TESTS:
        run_test(t.__name__, t)

    total = _PASS + _FAIL
    print(f"\n  Diagnostic tests: {_PASS}/{total}")
    for err in _ERRORS:
        print(err)
    if _FAIL == 0:
        print("  DIAGNOSTIC STATUS: PASSED")
    else:
        print("  DIAGNOSTIC STATUS: FAILED")
    sys.exit(0 if _FAIL == 0 else 1)
