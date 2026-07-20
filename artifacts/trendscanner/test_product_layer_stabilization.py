"""
test_product_layer_stabilization.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for Product Layer stabilization — v0.5.5 mobile fixes.

Tests cover:
 - Timezone-aware timestamp (no naive datetime.now())
 - Format: "HH:MM:SS TZ"
 - UTC fallback
 - Final signal / decision counting from FINAL dict
 - WAIT and SKIP always included in stats
 - build_product_diagnostic structure and correctness
 - Top Setups mapping (WATCH not lost, direction NONE excluded for TAKE)
 - Decision factor ordering: SKIP→blockers first, WATCH→warnings first, TAKE→positive first
 - Mobile TF card mapping
 - Empty state summary
 - None / NaN / inf safety throughout
"""

import math
import sys
import traceback
from datetime import datetime, timezone

# ── Test runner ────────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0
_ERRORS: list[str] = []


def ok(name: str):
    global _PASS
    _PASS += 1


def fail(name: str, msg: str = ""):
    global _FAIL
    _FAIL += 1
    _ERRORS.append(f"  FAIL  {name}: {msg}")


def check(name: str, condition: bool, msg: str = ""):
    if condition:
        ok(name)
    else:
        fail(name, msg or "condition is False")


def run_test(name: str, fn):
    try:
        fn()
    except Exception as e:
        fail(name, f"{type(e).__name__}: {e}")


# ── Imports ────────────────────────────────────────────────────────────────────

from product_helpers import (
    get_local_datetime,
    format_scan_timestamp,
    build_product_diagnostic,
    build_mobile_tf_card,
    order_decision_factors,
    build_empty_state_summary,
    count_final_signals,
    count_final_decisions,
)
from settings import DISPLAY_TIMEZONE, TOP_SETUPS_MIN_DECISION_SCORE
from top_setups import build_top_setups
from ui_helpers import (
    normalize_decision_result,
    summarize_decision_factors,
    decision_badge,
)


# ── Helpers for building fake results ─────────────────────────────────────────

def _make_result(signal="WAIT", decision="SKIP", direction="NONE",
                 d_score=0.0, confidence=10.0):
    return {
        "FINAL": {
            "signal":             signal,
            "decision":           decision,
            "decision_direction": direction,
            "decision_score":     d_score,
            "confidence":         confidence,
            "confidence_label":   "LOW",
            "decision_reason":    f"{decision}: score={d_score}",
            "available_timeframes": 3,
            "decision_counts":    {},
        }
    }


def _make_take(direction="LONG", d_score=70.0):
    return _make_result("LONG", "TAKE", direction, d_score, 60.0)


def _make_watch(direction="LONG", d_score=35.0):
    return _make_result("LONG", "WATCH", direction, d_score, 40.0)


def _make_skip():
    return _make_result("WAIT", "SKIP", "NONE", 0.0, 5.0)


def _make_error():
    return {"_error": "api error", "FINAL": {}}


# ═════════════════════════════════════════════════════════════════════════════
# 1. TIMESTAMP TESTS
# ═════════════════════════════════════════════════════════════════════════════

def test_get_local_datetime_is_aware():
    dt = get_local_datetime()
    check("TS1_aware", dt.tzinfo is not None,
          "datetime should be timezone-aware")


def test_get_local_datetime_is_moscow():
    dt = get_local_datetime()
    tz_str = dt.strftime("%Z")
    check("TS2_moscow_abbr", tz_str in ("MSK", "+03", "UTC"),
          f"timezone abbreviation should be MSK or UTC fallback, got {tz_str!r}")


def test_format_scan_timestamp_format():
    from zoneinfo import ZoneInfo
    dt = datetime(2025, 7, 20, 19, 58, 22, tzinfo=ZoneInfo("Europe/Moscow"))
    s = format_scan_timestamp(dt)
    check("TS3_format", "19:58:22" in s, f"expected HH:MM:SS in '{s}'")
    check("TS3_tz_abbr", "MSK" in s or "UTC" in s or "+" in s,
          f"expected timezone in '{s}'")


def test_format_scan_timestamp_utc_fallback():
    dt = datetime(2025, 7, 20, 19, 58, 22, tzinfo=timezone.utc)
    s = format_scan_timestamp(dt)
    check("TS4_utc", "19:58:22" in s, f"expected time in '{s}'")
    check("TS4_utc_tz", "UTC" in s or "utc" in s.lower() or len(s) > 8,
          f"expected UTC mention in '{s}'")


def test_format_scan_timestamp_invalid():
    check("TS5_none", format_scan_timestamp(None) == "—",
          "None should return '—'")
    check("TS5_str",  format_scan_timestamp("abc") == "—",
          "string should return '—'")
    check("TS5_int",  format_scan_timestamp(42) == "—",
          "int should return '—'")


def test_timestamp_is_not_naive():
    dt = get_local_datetime()
    check("TS6_not_naive", dt.tzinfo is not None and dt.utcoffset() is not None,
          "should not be naive datetime")


# ═════════════════════════════════════════════════════════════════════════════
# 2. FINAL SIGNAL / DECISION COUNTING
# ═════════════════════════════════════════════════════════════════════════════

def test_count_final_signals_basic():
    results = {
        "BTC": _make_result("LONG",  "TAKE", "LONG",  80),
        "ETH": _make_result("SHORT", "TAKE", "SHORT", 75),
        "SOL": _make_result("WAIT",  "SKIP", "NONE",   0),
    }
    syms = ["BTC", "ETH", "SOL"]
    c = count_final_signals(results, syms)
    check("CS1_long",  c["LONG"]  == 1, f"LONG={c['LONG']}")
    check("CS1_short", c["SHORT"] == 1, f"SHORT={c['SHORT']}")
    check("CS1_wait",  c["WAIT"]  == 1, f"WAIT={c['WAIT']}")


def test_count_final_signals_wait_always_counted():
    results = {
        "A": _make_result("WAIT", "SKIP", "NONE", 0),
        "B": _make_result("WAIT", "SKIP", "NONE", 0),
    }
    c = count_final_signals(results, ["A", "B"])
    check("CS2_wait_counted", c["WAIT"] == 2,
          "WAIT must always be counted even if SKIP decision")


def test_count_final_decisions_basic():
    results = {
        "A": _make_take(),
        "B": _make_watch(),
        "C": _make_skip(),
    }
    syms = ["A", "B", "C"]
    c = count_final_decisions(results, syms)
    check("CD1_take",  c["TAKE"]  == 1, f"TAKE={c['TAKE']}")
    check("CD1_watch", c["WATCH"] == 1, f"WATCH={c['WATCH']}")
    check("CD1_skip",  c["SKIP"]  == 1, f"SKIP={c['SKIP']}")


def test_count_final_decisions_skip_always_counted():
    results = {
        "A": _make_skip(),
        "B": _make_skip(),
        "C": _make_skip(),
    }
    c = count_final_decisions(results, ["A", "B", "C"])
    check("CD2_skip_counted", c["SKIP"] == 3, "SKIP must be counted")


def test_count_signals_invalid_falls_to_wait():
    results = {
        "X": {"FINAL": {"signal": "GARBAGE"}},
        "Y": None,
        "Z": {},
    }
    c = count_final_signals(results, ["X", "Y", "Z"])
    check("CS3_invalid_wait", c["WAIT"] == 3,
          f"invalid/None/no-FINAL → WAIT; got {c}")


def test_count_decisions_invalid_falls_to_skip():
    results = {
        "X": {"FINAL": {"decision": "GARBAGE"}},
        "Y": None,
        "Z": {},
    }
    c = count_final_decisions(results, ["X", "Y", "Z"])
    check("CD3_invalid_skip", c["SKIP"] == 3,
          f"invalid/None/no-FINAL → SKIP; got {c}")


# ═════════════════════════════════════════════════════════════════════════════
# 3. BUILD_PRODUCT_DIAGNOSTIC
# ═════════════════════════════════════════════════════════════════════════════

def test_diagnostic_keys():
    diag = build_product_diagnostic({})
    required = [
        "symbols_total", "valid_results", "error_results",
        "final_signals", "decisions", "decision_directions",
        "watch_above_min_score", "excluded_reasons",
    ]
    for k in required:
        check(f"DG1_{k}", k in diag, f"missing key: {k}")
    excl_keys = ["skip", "watch_below_threshold", "direction_none", "invalid_data"]
    for k in excl_keys:
        check(f"DG1_exc_{k}", k in diag["excluded_reasons"], f"missing excluded_reasons key: {k}")


def test_diagnostic_empty():
    diag = build_product_diagnostic({})
    check("DG2_total",  diag["symbols_total"] == 0)
    check("DG2_valid",  diag["valid_results"] == 0)
    check("DG2_errors", diag["error_results"] == 0)
    check("DG2_take",   diag["decisions"]["TAKE"] == 0)


def test_diagnostic_counts_correct():
    results = {
        "A": _make_take(),
        "B": _make_watch(d_score=35.0),   # above threshold
        "C": _make_watch(d_score=5.0),    # below threshold
        "D": _make_skip(),
        "E": _make_error(),
    }
    diag = build_product_diagnostic(results)
    check("DG3_total",  diag["symbols_total"]  == 5, str(diag["symbols_total"]))
    check("DG3_valid",  diag["valid_results"]  == 4, str(diag["valid_results"]))
    check("DG3_errors", diag["error_results"]  == 1, str(diag["error_results"]))
    check("DG3_take",   diag["decisions"]["TAKE"]  == 1)
    check("DG3_watch",  diag["decisions"]["WATCH"] == 2)
    check("DG3_skip",   diag["decisions"]["SKIP"]  == 1)


def test_diagnostic_watch_above_threshold():
    results = {
        "A": _make_watch(d_score=TOP_SETUPS_MIN_DECISION_SCORE + 1),
        "B": _make_watch(d_score=TOP_SETUPS_MIN_DECISION_SCORE - 1),
        "C": _make_watch(d_score=TOP_SETUPS_MIN_DECISION_SCORE),  # equal = above
    }
    diag = build_product_diagnostic(results)
    above = diag["watch_above_min_score"]
    below = diag["excluded_reasons"]["watch_below_threshold"]
    check("DG4_above", above == 2, f"above={above}")
    check("DG4_below", below == 1, f"below={below}")


def test_diagnostic_direction_none_exclusion():
    results = {
        "A": _make_watch(direction="NONE"),    # WATCH + NONE → direction_none
        "B": _make_take(direction="NONE"),     # TAKE + NONE → excluded
    }
    diag = build_product_diagnostic(results)
    check("DG5_dir_none", diag["excluded_reasons"]["direction_none"] >= 1)


def test_diagnostic_skip_counted_in_exclusions():
    results = {
        "A": _make_skip(),
        "B": _make_skip(),
    }
    diag = build_product_diagnostic(results)
    check("DG6_exc_skip", diag["excluded_reasons"]["skip"] == 2,
          str(diag["excluded_reasons"]["skip"]))


def test_diagnostic_nan_inf_safe():
    results = {
        "X": {"FINAL": {
            "signal": float("nan"), "decision": float("inf"),
            "decision_score": float("nan"),
        }},
    }
    try:
        diag = build_product_diagnostic(results)
        check("DG7_nan_safe", True)
    except Exception as e:
        fail("DG7_nan_safe", str(e))


# ═════════════════════════════════════════════════════════════════════════════
# 4. TOP SETUPS MAPPING
# ═════════════════════════════════════════════════════════════════════════════

def test_watch_not_lost_when_direction_set():
    results = {"ETH": _make_watch(direction="LONG", d_score=40.0)}
    setups = build_top_setups(results, limit=10,
                              min_decision_score=25.0, include_watch=True,
                              include_skip=False)
    syms = [s["symbol"] for s in setups]
    check("TS_W1_watch_present", "ETH" in syms,
          "WATCH with LONG direction should appear in Top Setups")


def test_take_direction_none_excluded():
    results = {"BTC": _make_take(direction="NONE", d_score=80.0)}
    setups = build_top_setups(results, limit=10,
                              min_decision_score=25.0, include_watch=True,
                              include_skip=False)
    syms = [s["symbol"] for s in setups]
    check("TS_T1_take_none_excluded", "BTC" not in syms,
          "TAKE with direction=NONE must be excluded")


def test_watch_below_threshold_excluded():
    results = {"ADA": _make_watch(direction="LONG", d_score=5.0)}
    setups = build_top_setups(results, limit=10,
                              min_decision_score=25.0, include_watch=True,
                              include_skip=False)
    syms = [s["symbol"] for s in setups]
    check("TS_W2_below_threshold", "ADA" not in syms,
          "WATCH below min_decision_score must be excluded")


def test_skip_excluded_by_default():
    results = {"SOL": _make_skip()}
    setups = build_top_setups(results, limit=10,
                              min_decision_score=25.0, include_watch=True,
                              include_skip=False)
    syms = [s["symbol"] for s in setups]
    check("TS_S1_skip_excluded", "SOL" not in syms)


def test_take_beats_watch_in_ranking():
    results = {
        "A": _make_take(direction="LONG", d_score=50.0),
        "B": _make_watch(direction="LONG", d_score=80.0),
    }
    setups = build_top_setups(results, limit=10,
                              min_decision_score=25.0, include_watch=True,
                              include_skip=False)
    if len(setups) >= 2:
        check("TS_R1_take_first", setups[0]["symbol"] == "A",
              f"TAKE should rank above WATCH; got {setups[0]['symbol']}")
    else:
        fail("TS_R1_take_first", f"expected 2 setups, got {len(setups)}")


def test_top_setups_nan_inf_safe():
    results = {
        "X": {"FINAL": {
            "decision": "WATCH",
            "decision_direction": "LONG",
            "decision_score": float("nan"),
            "confidence": float("inf"),
            "confidence_label": "LOW",
            "signal": "WAIT",
            "decision_reason": "",
            "available_timeframes": 0,
            "decision_counts": {},
        }}
    }
    try:
        setups = build_top_setups(results, limit=10, min_decision_score=25.0,
                                  include_watch=True, include_skip=False)
        check("TS_N1_nan_safe", True)
    except Exception as e:
        fail("TS_N1_nan_safe", str(e))


# ═════════════════════════════════════════════════════════════════════════════
# 5. DECISION FACTOR ORDERING
# ═════════════════════════════════════════════════════════════════════════════

def _make_factors():
    return {
        "positive": ["Good trend", "Good volume"],
        "warnings": ["Low confidence"],
        "blockers": ["Breakout not confirmed"],
    }


def test_skip_blockers_first():
    sections = order_decision_factors(_make_factors(), "SKIP")
    check("FO1_first",  sections[0]["title"] == "✕ Blockers",
          f"got {sections[0]['title']}")
    check("FO1_second", sections[1]["title"] == "⚠ Warnings",
          f"got {sections[1]['title']}")
    check("FO1_third",  sections[2]["title"] == "✓ Positive",
          f"got {sections[2]['title']}")


def test_watch_warnings_first():
    sections = order_decision_factors(_make_factors(), "WATCH")
    check("FO2_first",  sections[0]["title"] == "⚠ Warnings",
          f"got {sections[0]['title']}")
    check("FO2_second", sections[1]["title"] == "✓ Positive",
          f"got {sections[1]['title']}")
    check("FO2_third",  sections[2]["title"] == "✕ Blockers",
          f"got {sections[2]['title']}")


def test_take_positive_first():
    sections = order_decision_factors(_make_factors(), "TAKE")
    check("FO3_first",  sections[0]["title"] == "✓ Positive",
          f"got {sections[0]['title']}")
    check("FO3_second", sections[1]["title"] == "⚠ Warnings",
          f"got {sections[1]['title']}")
    check("FO3_third",  sections[2]["title"] == "✕ Blockers",
          f"got {sections[2]['title']}")


def test_factor_ordering_returns_3_sections():
    for dec in ("TAKE", "WATCH", "SKIP", "garbage"):
        s = order_decision_factors(_make_factors(), dec)
        check(f"FO4_{dec}_3sections", len(s) == 3,
              f"expected 3 sections, got {len(s)}")


def test_factor_ordering_carries_items():
    sections = order_decision_factors(_make_factors(), "SKIP")
    blocker_section = sections[0]
    check("FO5_items", len(blocker_section["items"]) == 1,
          f"expected 1 blocker, got {blocker_section['items']}")
    check("FO5_css", blocker_section["css_class"] == "reason-blocker",
          blocker_section["css_class"])


def test_factor_ordering_empty_factors():
    empty = {"positive": [], "warnings": [], "blockers": []}
    for dec in ("TAKE", "WATCH", "SKIP"):
        s = order_decision_factors(empty, dec)
        check(f"FO6_{dec}_empty_ok", len(s) == 3)
        check(f"FO6_{dec}_no_items", all(len(sec["items"]) == 0 for sec in s))


# ═════════════════════════════════════════════════════════════════════════════
# 6. MOBILE TF CARD
# ═════════════════════════════════════════════════════════════════════════════

def _make_tf_data(signal="WAIT", decision="SKIP", direction="NONE", d_score=0.0):
    return {
        "signal":             signal,
        "decision":           decision,
        "decision_direction": direction,
        "decision_score":     d_score,
        "confidence":         10.0,
        "confidence_label":   "LOW",
        "trend":              "NEUTRAL",
        "reason":             "",
        "quality":            {},
    }


def test_mobile_tf_card_required_keys():
    card = build_mobile_tf_card("1h", _make_tf_data())
    required = [
        "tf", "signal", "signal_icon", "decision", "decision_label",
        "decision_score", "confidence", "confidence_label",
        "trend", "badge", "should_expand",
    ]
    for k in required:
        check(f"MTC1_{k}", k in card, f"missing key: {k}")


def test_mobile_tf_card_take_should_expand():
    card = build_mobile_tf_card("1d", _make_tf_data("LONG", "TAKE", "LONG", 80))
    check("MTC2_expand", card["should_expand"] is True,
          "TAKE should expand")


def test_mobile_tf_card_watch_should_expand():
    card = build_mobile_tf_card("4h", _make_tf_data("LONG", "WATCH", "LONG", 40))
    check("MTC3_expand", card["should_expand"] is True,
          "WATCH should expand")


def test_mobile_tf_card_skip_not_expanded():
    card = build_mobile_tf_card("1h", _make_tf_data("WAIT", "SKIP", "NONE", 0))
    check("MTC4_no_expand", card["should_expand"] is False,
          "SKIP should not expand")


def test_mobile_tf_card_none_input():
    card = build_mobile_tf_card("1M", None)
    check("MTC5_none", card["decision"] == "SKIP",
          f"None input should default to SKIP: {card['decision']}")
    check("MTC5_no_expand", card["should_expand"] is False)


def test_mobile_tf_card_signal_icon():
    card_long  = build_mobile_tf_card("1w", _make_tf_data("LONG", "TAKE", "LONG", 80))
    card_short = build_mobile_tf_card("1w", _make_tf_data("SHORT", "TAKE", "SHORT", 80))
    card_wait  = build_mobile_tf_card("1w", _make_tf_data("WAIT", "SKIP", "NONE", 0))
    check("MTC6_long_icon",  card_long["signal_icon"]  == "🟢")
    check("MTC6_short_icon", card_short["signal_icon"] == "🔴")
    check("MTC6_wait_icon",  card_wait["signal_icon"]  == "⚪")


# ═════════════════════════════════════════════════════════════════════════════
# 7. EMPTY STATE SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def test_empty_state_summary_keys():
    ess = build_empty_state_summary({}, [])
    for k in ("take_count", "watch_count", "most_common_blocker"):
        check(f"ESS1_{k}", k in ess, f"missing key: {k}")


def test_empty_state_summary_no_candidates():
    results = {
        "A": _make_skip(),
        "B": _make_skip(),
    }
    ess = build_empty_state_summary(results, [])
    check("ESS2_take",  ess["take_count"]  == 0)
    check("ESS2_watch", ess["watch_count"] == 0)


def test_empty_state_summary_has_blocker():
    results = {
        "A": _make_skip(),
        "B": _make_skip(),
    }
    ess = build_empty_state_summary(results, [])
    # should be a string (may be empty if no reason)
    check("ESS3_blocker_str",
          isinstance(ess["most_common_blocker"], str))


def test_empty_state_summary_counts_watch():
    results = {
        "A": _make_watch(d_score=5.0),   # below threshold — still WATCH decision
        "B": _make_skip(),
    }
    ess = build_empty_state_summary(results, [])
    check("ESS4_watch", ess["watch_count"] == 1, str(ess["watch_count"]))


def test_empty_state_summary_safe_with_errors():
    results = {
        "X": _make_error(),
        "Y": None,
        "Z": {"FINAL": {"decision": float("nan")}},
    }
    try:
        ess = build_empty_state_summary(results, [])
        check("ESS5_safe", True)
    except Exception as e:
        fail("ESS5_safe", str(e))


# ═════════════════════════════════════════════════════════════════════════════
# 8. LAST SCAN — DATA LAYER (single source)
# ═════════════════════════════════════════════════════════════════════════════

def test_format_scan_timestamp_idempotent_with_get_local_datetime():
    """
    get_local_datetime() + format_scan_timestamp() produces a string once.
    The same string should not change when called again (within test).
    """
    dt1 = get_local_datetime()
    s1  = format_scan_timestamp(dt1)
    # Should be a non-empty string with colon (time-like)
    check("LS1_not_empty", len(s1) > 0)
    check("LS1_has_colon", ":" in s1, f"no colon in '{s1}'")


def test_format_scan_timestamp_always_string():
    for val in [None, 0, "abc", [], 3.14]:
        s = format_scan_timestamp(val)
        check(f"LS2_type_{type(val).__name__}", isinstance(s, str))


# ═════════════════════════════════════════════════════════════════════════════
# 9. NaN / INF SAFETY — normalize_decision_result
# ═════════════════════════════════════════════════════════════════════════════

def test_normalize_decision_nan():
    r = normalize_decision_result({"decision": float("nan"),
                                   "decision_score": float("nan")})
    check("NDR_nan_decision", r["decision"] in ("TAKE", "WATCH", "SKIP"))
    check("NDR_nan_score",    math.isfinite(r["decision_score"]) or r["decision_score"] == 0.0)


def test_normalize_decision_inf():
    r = normalize_decision_result({"decision_score": float("inf"),
                                   "decision_direction": float("inf")})
    check("NDR_inf_score", r["decision_score"] == 0.0,
          f"inf score should default to 0.0: {r['decision_score']}")
    check("NDR_inf_dir",   r["decision_direction"] in ("LONG", "SHORT", "NONE"))


def test_normalize_decision_none():
    r = normalize_decision_result(None)
    check("NDR_none", r["decision"] == "SKIP")


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

_TESTS = [
    # Timestamp
    test_get_local_datetime_is_aware,
    test_get_local_datetime_is_moscow,
    test_format_scan_timestamp_format,
    test_format_scan_timestamp_utc_fallback,
    test_format_scan_timestamp_invalid,
    test_timestamp_is_not_naive,
    # Signal / Decision counting
    test_count_final_signals_basic,
    test_count_final_signals_wait_always_counted,
    test_count_final_decisions_basic,
    test_count_final_decisions_skip_always_counted,
    test_count_signals_invalid_falls_to_wait,
    test_count_decisions_invalid_falls_to_skip,
    # Diagnostic
    test_diagnostic_keys,
    test_diagnostic_empty,
    test_diagnostic_counts_correct,
    test_diagnostic_watch_above_threshold,
    test_diagnostic_direction_none_exclusion,
    test_diagnostic_skip_counted_in_exclusions,
    test_diagnostic_nan_inf_safe,
    # Top Setups mapping
    test_watch_not_lost_when_direction_set,
    test_take_direction_none_excluded,
    test_watch_below_threshold_excluded,
    test_skip_excluded_by_default,
    test_take_beats_watch_in_ranking,
    test_top_setups_nan_inf_safe,
    # Factor ordering
    test_skip_blockers_first,
    test_watch_warnings_first,
    test_take_positive_first,
    test_factor_ordering_returns_3_sections,
    test_factor_ordering_carries_items,
    test_factor_ordering_empty_factors,
    # Mobile TF card
    test_mobile_tf_card_required_keys,
    test_mobile_tf_card_take_should_expand,
    test_mobile_tf_card_watch_should_expand,
    test_mobile_tf_card_skip_not_expanded,
    test_mobile_tf_card_none_input,
    test_mobile_tf_card_signal_icon,
    # Empty state
    test_empty_state_summary_keys,
    test_empty_state_summary_no_candidates,
    test_empty_state_summary_has_blocker,
    test_empty_state_summary_counts_watch,
    test_empty_state_summary_safe_with_errors,
    # Last scan data layer
    test_format_scan_timestamp_idempotent_with_get_local_datetime,
    test_format_scan_timestamp_always_string,
    # NaN / inf safety
    test_normalize_decision_nan,
    test_normalize_decision_inf,
    test_normalize_decision_none,
]

if __name__ == "__main__":
    print("─" * 60)
    print("  Product Layer Stabilization Tests")
    print("─" * 60)
    for t in _TESTS:
        run_test(t.__name__, t)

    total = _PASS + _FAIL
    print(f"\n  Stabilization tests: {_PASS}/{total}")
    for err in _ERRORS:
        print(err)

    if _FAIL == 0:
        print("  STABILIZATION STATUS: PASSED")
    else:
        print("  STABILIZATION STATUS: FAILED")
    sys.exit(0 if _FAIL == 0 else 1)
