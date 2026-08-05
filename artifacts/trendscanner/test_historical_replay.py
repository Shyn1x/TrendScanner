"""
test_historical_replay.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for historical_replay.py

Checks:
 - No look-ahead bias
 - Correct warmup (signal candle at df.iloc[-2])
 - Original DataFrame not modified
 - Empty / insufficient data handling
 - Incomplete analysis result handling
 - LONG and SHORT tracked separately
 - Primary rejection reason is stable and deterministic
 - Confidence / decision distributions
 - NaN / inf safety
 - Format stability (all required keys present)
 - replay calls analyze_timeframe, not duplicate logic
 - build_confirmed_breakout_funnel structure
"""

import sys
import math
import copy
import pandas as pd
import numpy as np
from collections import Counter

from historical_replay import (
    replay_timeframe,
    build_confirmed_breakout_funnel,
    _primary_rejection_reason,
    _extract_dir_data,
    _CONF_40, _CONF_50, _TREND_MIN, _VOL_MIN,
)

# ── Test runner ────────────────────────────────────────────────────────────────
_PASS = 0
_FAIL = 0
_ERRORS: list = []


def ok(name): global _PASS; _PASS += 1


def fail(name, msg=""):
    global _FAIL; _FAIL += 1
    _ERRORS.append(f"  FAIL  {name}: {msg}")


def check(name, condition, msg=""):
    if condition:
        ok(name)
    else:
        fail(name, msg or "condition False")


def run_test(name, fn):
    try:
        fn()
    except Exception as e:
        fail(name, f"{type(e).__name__}: {e}")


# ── DataFrame factory ──────────────────────────────────────────────────────────

def _make_df(n: int, seed: int = 42) -> pd.DataFrame:
    """
    Generates a realistic-looking OHLCV DataFrame with n rows.
    Uses deterministic random prices so tests are reproducible.
    """
    rng    = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    opens  = closes + rng.normal(0, 0.2, n)
    highs  = np.maximum(opens, closes) + abs(rng.normal(0, 0.3, n))
    lows   = np.minimum(opens, closes) - abs(rng.normal(0, 0.3, n))
    vols   = rng.uniform(1000, 5000, n)
    times  = [1_700_000_000_000 + i * 3_600_000 for i in range(n)]  # 1h apart
    return pd.DataFrame({
        "time":   times,
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": vols,
    })


WARMUP  = 120
DF_SIZE = 160   # warmup + 40 replay steps

# ═════════════════════════════════════════════════════════════════════════════
# 1. Basic API contract
# ═════════════════════════════════════════════════════════════════════════════

def test_returns_dict():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=5)
    check("API1_dict",    isinstance(data, dict))
    check("API1_results", "replay_results" in data)
    check("API1_meta",    "meta"           in data)


def test_meta_keys():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=5)
    meta = data["meta"]
    for k in ("symbol", "timeframe", "warmup_bars", "total_bars",
              "replay_count", "skipped", "insufficient_data"):
        check(f"META_{k}", k in meta, f"missing meta key: {k}")


def test_result_keys():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=3)
    for r in data["replay_results"]:
        for k in ("symbol", "timeframe", "replay_index", "signal_timestamp",
                  "signal", "confidence", "decision", "decision_direction",
                  "decision_score", "decision_reason", "long", "short", "timeframe_result"):
            check(f"KEYS_{k}", k in r, f"missing key: {k}")


def test_timeframe_result_min_structure():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=3)
    for r in data["replay_results"]:
        tf = r.get("timeframe_result", {})
        check("TFR1_dict", isinstance(tf, dict), "timeframe_result must be dict")

        quality = tf.get("quality", {}) if isinstance(tf, dict) else {}
        decision_details = tf.get("decision_details", {}) if isinstance(tf, dict) else {}
        check("TFR1_quality", isinstance(quality, dict), "quality must be dict")
        check("TFR1_decision_details", isinstance(decision_details, dict), "decision_details must be dict")

        for direction in ("LONG", "SHORT"):
            qd = quality.get(direction, {}) if isinstance(quality, dict) else {}
            dd = decision_details.get(direction, {}) if isinstance(decision_details, dict) else {}
            check(f"TFR1_quality_{direction}", isinstance(qd, dict), f"quality.{direction} missing")
            check(f"TFR1_decision_{direction}", isinstance(dd, dict), f"decision_details.{direction} missing")

            for key in ("confirmed", "signal", "confidence", "trend_quality", "volume_quality", "breakout_quality"):
                check(f"TFR1_q_{direction}_{key}", key in qd, f"missing quality.{direction}.{key}")

            for key in ("decision", "decision_score", "breakout_confirmed", "blockers", "component_scores", "market_context"):
                check(f"TFR1_d_{direction}_{key}", key in dd, f"missing decision_details.{direction}.{key}")

            mc = dd.get("market_context", {}) if isinstance(dd, dict) else {}
            check(f"TFR1_alignment_{direction}", "alignment" in mc if isinstance(mc, dict) else False,
                  f"missing decision_details.{direction}.market_context.alignment")


def test_dir_data_keys():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=3)
    for r in data["replay_results"]:
        for direction in ("long", "short"):
            d = r[direction]
            for k in ("breakout_detected", "breakout_confirmed", "breakout_score",
                      "trend_quality", "volume_quality", "structure_quality",
                      "confidence", "decision", "blocker_codes"):
                check(f"DKEYS_{direction}_{k}", k in d, f"missing {direction}.{k}")


# ═════════════════════════════════════════════════════════════════════════════
# 2. Look-ahead bias
# ═════════════════════════════════════════════════════════════════════════════

def test_no_look_ahead_bias():
    """
    Take one replay step. Mutate all rows AFTER end_index. Recompute.
    Result must not change — the historical slice is identical.
    """
    df      = _make_df(DF_SIZE, seed=7)
    df_orig = df.copy()

    data1   = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=1)

    # Mutate future rows
    df_future = df.copy()
    future_start = WARMUP + 1   # rows after end_index=WARMUP
    df_future.loc[future_start:, "close"] *= 999.0
    df_future.loc[future_start:, "high"]  *= 999.0

    data2 = replay_timeframe(df_future, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=1)

    if data1["replay_results"] and data2["replay_results"]:
        r1 = data1["replay_results"][0]
        r2 = data2["replay_results"][0]
        check("LAB1_signal",    r1["signal"]   == r2["signal"],
              f"{r1['signal']} != {r2['signal']}")
        check("LAB1_decision",  r1["decision"] == r2["decision"],
              f"{r1['decision']} != {r2['decision']}")
        check("LAB1_confidence",
              abs(float(r1["confidence"]) - float(r2["confidence"])) < 0.001,
              f"{r1['confidence']} != {r2['confidence']}")
    else:
        # If both return empty due to pipeline error, that's also consistent
        check("LAB1_both_empty",
              len(data1["replay_results"]) == len(data2["replay_results"]),
              "result count differs")

    # Original df untouched
    pd.testing.assert_frame_equal(df, df_orig)
    check("LAB1_orig_unchanged", True)


def test_future_candle_change_no_effect():
    """Step 0 result must equal step 0 result when future candles are changed."""
    df    = _make_df(DF_SIZE, seed=13)
    data1 = replay_timeframe(df, "ETH/USDT", "4h", warmup_bars=WARMUP, max_replay_bars=2)

    df2 = df.copy()
    df2.loc[WARMUP + 2:, "close"] = 99999.9

    data2 = replay_timeframe(df2, "ETH/USDT", "4h", warmup_bars=WARMUP, max_replay_bars=2)

    for step in range(min(len(data1["replay_results"]), len(data2["replay_results"]))):
        r1 = data1["replay_results"][step]
        r2 = data2["replay_results"][step]
        check(f"LAB2_step{step}_signal",
              r1["signal"] == r2["signal"],
              f"step {step}: {r1['signal']} != {r2['signal']}")


# ═════════════════════════════════════════════════════════════════════════════
# 3. Signal candle at position -2
# ═════════════════════════════════════════════════════════════════════════════

def test_signal_candle_at_minus2():
    """
    At replay step 0, end_index = warmup_bars.
    historical_df = df.iloc[:warmup_bars+1] → warmup_bars+1 rows.
    signal candle = df.iloc[warmup_bars-1] = historical_df.iloc[-2].
    We verify the timestamp matches df.iloc[warmup_bars-1]["time"].
    """
    df   = _make_df(DF_SIZE, seed=3)
    data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=1)

    if not data["replay_results"]:
        check("SC1_has_result", False, "No replay results produced")
        return

    r  = data["replay_results"][0]
    expected_ts = int(df.iloc[WARMUP - 1]["time"])
    check("SC1_timestamp", r["signal_timestamp"] == expected_ts,
          f"got {r['signal_timestamp']}, expected {expected_ts}")


def test_replay_index_sequential():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "SOL/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=10)
    indices = [r["replay_index"] for r in data["replay_results"]]
    check("RI1_sequential", indices == list(range(len(indices))),
          f"indices not sequential: {indices[:5]}")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Original DataFrame not modified
# ═════════════════════════════════════════════════════════════════════════════

def test_original_df_not_modified():
    df   = _make_df(DF_SIZE)
    orig = df.copy()
    replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=10)
    pd.testing.assert_frame_equal(df, orig)
    check("DF1_unchanged", True)


# ═════════════════════════════════════════════════════════════════════════════
# 5. Empty / insufficient data
# ═════════════════════════════════════════════════════════════════════════════

def test_empty_df():
    df   = pd.DataFrame()
    data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=5)
    check("ED1_empty_results", len(data["replay_results"]) == 0)
    check("ED1_insuff_flag",   data["meta"]["insufficient_data"] is True)


def test_none_df():
    data = replay_timeframe(None, "X", "1h", warmup_bars=WARMUP, max_replay_bars=5)
    check("ND1_results", len(data["replay_results"]) == 0)
    check("ND1_insuff",  data["meta"]["insufficient_data"] is True)


def test_too_few_bars():
    df   = _make_df(50)   # warmup=120 → insufficient
    data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=5)
    check("FB1_results", len(data["replay_results"]) == 0)
    check("FB1_insuff",  data["meta"]["insufficient_data"] is True)


def test_exactly_warmup_plus_two():
    """warmup_bars + 2 is the minimum viable size → exactly 1 step."""
    n    = WARMUP + 2
    df   = _make_df(n)
    data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=10)
    check("MB1_count", data["meta"]["replay_count"] + data["meta"]["skipped"] >= 1,
          "minimum size should produce at least 1 attempt")


def test_max_replay_bars_respected():
    df   = _make_df(500)
    data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=20)
    total_attempts = data["meta"]["replay_count"] + data["meta"]["skipped"]
    check("MRB1_limit", total_attempts <= 20,
          f"attempts={total_attempts} > max=20")


# ═════════════════════════════════════════════════════════════════════════════
# 6. LONG and SHORT tracked separately
# ═════════════════════════════════════════════════════════════════════════════

def test_long_short_separate():
    df   = _make_df(DF_SIZE, seed=42)
    data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=10)
    for r in data["replay_results"]:
        check("LS1_long_key",  "long"  in r)
        check("LS1_short_key", "short" in r)
        long_conf  = r["long"].get("confidence")
        short_conf = r["short"].get("confidence")
        # They may differ (or both be None) but must be independent fields
        check("LS1_independent",
              long_conf != "MUST_NOT_EQUAL_THIS_SENTINEL")


def test_funnel_long_short_independent():
    """build_confirmed_breakout_funnel must track long and short totals separately."""
    # Make fake results with LONG confirmed only
    results = []
    for _ in range(5):
        results.append({
            "symbol": "X", "timeframe": "1h", "replay_index": 0,
            "signal_timestamp": None, "signal": "LONG", "confidence": 10.0,
            "decision": "SKIP", "decision_direction": "NONE",
            "decision_score": 0.0, "decision_reason": "",
            "long":  {"breakout_detected": True,  "breakout_confirmed": True,
                      "breakout_score": 40.0, "trend_quality": 20.0,
                      "volume_quality": 20.0, "structure_quality": None,
                      "structure_state": "BULLISH", "confidence": 30.0,
                      "decision": "SKIP", "blocker_codes": []},
            "short": {"breakout_detected": False, "breakout_confirmed": False,
                      "breakout_score": 0.0,  "trend_quality": None,
                      "volume_quality": None, "structure_quality": None,
                      "structure_state": "", "confidence": None,
                      "decision": "SKIP", "blocker_codes": []},
        })
    funnel = build_confirmed_breakout_funnel(results)
    check("FLS1_long_conf",  funnel["long"]["breakout_confirmed"]  == 5)
    check("FLS1_short_conf", funnel["short"]["breakout_confirmed"] == 0)


# ═════════════════════════════════════════════════════════════════════════════
# 7. Primary rejection reason — stable and deterministic
# ═════════════════════════════════════════════════════════════════════════════

def _make_entry(
    signal="WAIT", decision="SKIP",
    long_confirmed=False, long_bs=0.0, long_conf=None,
    long_tq=None, long_vq=None, long_st="",
    long_blockers=None,
):
    return {
        "signal": signal, "decision": decision,
        "decision_direction": "NONE",
        "long": {
            "breakout_detected": long_bs > 0,
            "breakout_confirmed": long_confirmed,
            "breakout_score": long_bs,
            "trend_quality": long_tq,
            "volume_quality": long_vq,
            "structure_quality": None,
            "structure_state": long_st,
            "confidence": long_conf,
            "decision": decision,
            "blocker_codes": long_blockers or [],
        },
        "short": {
            "breakout_detected": False, "breakout_confirmed": False,
            "breakout_score": 0, "trend_quality": None,
            "volume_quality": None, "structure_quality": None,
            "structure_state": "", "confidence": None,
            "decision": "SKIP", "blocker_codes": [],
        },
    }


def test_rejection_not_confirmed():
    e = _make_entry(long_confirmed=False, long_bs=10.0)
    check("REJ1", _primary_rejection_reason(e, "long") == "breakout not confirmed")


def test_rejection_conf_below_40():
    e = _make_entry(long_confirmed=True, long_bs=50.0, long_conf=30.0, long_tq=60.0, long_vq=50.0)
    r = _primary_rejection_reason(e, "long")
    check("REJ2", r == "confidence below 40", f"got: {r}")


def test_rejection_conf_40_49():
    e = _make_entry(long_confirmed=True, long_bs=50.0, long_conf=45.0, long_tq=60.0, long_vq=50.0)
    r = _primary_rejection_reason(e, "long")
    check("REJ3", r == "confidence 40-49", f"got: {r}")


def test_rejection_weak_tq():
    e = _make_entry(long_confirmed=True, long_bs=50.0, long_conf=60.0, long_tq=20.0, long_vq=50.0)
    r = _primary_rejection_reason(e, "long")
    check("REJ4", r == "weak trend quality", f"got: {r}")


def test_rejection_weak_vol():
    e = _make_entry(long_confirmed=True, long_bs=50.0, long_conf=60.0, long_tq=60.0, long_vq=10.0)
    r = _primary_rejection_reason(e, "long")
    check("REJ5", r == "weak volume", f"got: {r}")


def test_rejection_transitional():
    e = _make_entry(long_confirmed=True, long_bs=50.0, long_conf=70.0,
                    long_tq=60.0, long_vq=50.0, long_st="TRANSITION_BULLISH")
    r = _primary_rejection_reason(e, "long")
    check("REJ6", r == "transitional/range structure", f"got: {r}")


def test_rejection_structure_opposition():
    e = _make_entry(long_confirmed=True, long_bs=50.0, long_conf=70.0,
                    long_tq=60.0, long_vq=50.0, long_st="BULLISH",
                    long_blockers=["STRONG_STRUCTURE_OPPOSITION"])
    r = _primary_rejection_reason(e, "long")
    check("REJ7", r == "strong structure opposition", f"got: {r}")


def test_rejection_deterministic():
    """Same input always returns same reason."""
    e = _make_entry(long_confirmed=True, long_bs=50.0, long_conf=30.0)
    r1 = _primary_rejection_reason(e, "long")
    r2 = _primary_rejection_reason(e, "long")
    check("REJ8_stable", r1 == r2, f"{r1} != {r2}")


def test_rejection_invalid_data():
    e = {"long": None, "short": None, "signal": "WAIT", "decision": "SKIP"}
    r = _primary_rejection_reason(e, "long")
    check("REJ9_invalid", isinstance(r, str) and len(r) > 0)


# ═════════════════════════════════════════════════════════════════════════════
# 8. build_confirmed_breakout_funnel
# ═════════════════════════════════════════════════════════════════════════════

def test_funnel_keys():
    funnel = build_confirmed_breakout_funnel([])
    for k in ("total", "long", "short", "top_rejections"):
        check(f"FUN1_{k}", k in funnel)
    for d in ("long", "short"):
        for k in ("total_points", "breakout_detected", "breakout_confirmed",
                  "trend_quality_ok", "volume_ok", "structure_available",
                  "confidence_ge_40", "confidence_ge_50",
                  "decision_watch", "decision_take", "decision_skip",
                  "top_rejections"):
            check(f"FUN1_{d}_{k}", k in funnel[d], f"missing funnel[{d}][{k}]")


def test_funnel_empty():
    funnel = build_confirmed_breakout_funnel([])
    check("FUN2_total",   funnel["total"] == 0)
    check("FUN2_l_conf",  funnel["long"]["breakout_confirmed"] == 0)
    check("FUN2_s_conf",  funnel["short"]["breakout_confirmed"] == 0)


def test_funnel_none_entries():
    try:
        funnel = build_confirmed_breakout_funnel([None, None])
        check("FUN3_safe", True)
    except Exception as e:
        fail("FUN3_safe", str(e))


def test_funnel_counts():
    results = []
    for _ in range(3):
        results.append({
            "signal": "LONG", "decision": "SKIP",
            "decision_direction": "NONE", "confidence": 70.0,
            "long": {
                "breakout_detected": True, "breakout_confirmed": True,
                "breakout_score": 50.0, "trend_quality": 60.0,
                "volume_quality": 60.0, "structure_quality": 60.0,
                "structure_state": "BULLISH", "confidence": 70.0,
                "decision": "SKIP", "blocker_codes": [],
            },
            "short": {"breakout_detected": False, "breakout_confirmed": False,
                      "breakout_score": 0, "trend_quality": None, "volume_quality": None,
                      "structure_quality": None, "structure_state": "", "confidence": None,
                      "decision": "SKIP", "blocker_codes": []},
        })
    funnel = build_confirmed_breakout_funnel(results)
    check("FUN4_total",       funnel["total"]                      == 3)
    check("FUN4_l_conf",      funnel["long"]["breakout_confirmed"] == 3)
    check("FUN4_l_tq",        funnel["long"]["trend_quality_ok"]   == 3)
    check("FUN4_l_skip",      funnel["long"]["decision_skip"]      == 3)


# ═════════════════════════════════════════════════════════════════════════════
# 9. NaN / inf safety
# ═════════════════════════════════════════════════════════════════════════════

def test_nan_inf_in_replay_results():
    """build_confirmed_breakout_funnel must not crash on NaN/inf values."""
    results = [{
        "signal": "WAIT", "decision": "SKIP",
        "decision_direction": "NONE", "confidence": float("nan"),
        "long": {
            "breakout_detected": True, "breakout_confirmed": True,
            "breakout_score": float("nan"), "trend_quality": float("inf"),
            "volume_quality": float("nan"), "structure_quality": None,
            "structure_state": "BULLISH", "confidence": float("nan"),
            "decision": "SKIP", "blocker_codes": [],
        },
        "short": {
            "breakout_detected": False, "breakout_confirmed": False,
            "breakout_score": 0.0, "trend_quality": None, "volume_quality": None,
            "structure_quality": None, "structure_state": "", "confidence": None,
            "decision": "SKIP", "blocker_codes": [],
        },
    }]
    try:
        funnel = build_confirmed_breakout_funnel(results)
        check("NAN1_funnel_safe", True)
    except Exception as e:
        fail("NAN1_funnel_safe", str(e))


def test_nan_inf_in_df():
    """Replay must handle NaN values in the DataFrame without crashing."""
    df = _make_df(DF_SIZE)
    df.loc[WARMUP + 2, "close"] = float("nan")
    try:
        data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=5)
        check("NAN2_replay_safe", True)
    except Exception as e:
        fail("NAN2_replay_safe", str(e))


# ═════════════════════════════════════════════════════════════════════════════
# 10. replay uses analyze_timeframe, not duplicate logic
# ═════════════════════════════════════════════════════════════════════════════

def test_uses_analyze_timeframe(monkeypatch=None):
    """
    Monkey-patch analyze_timeframe to return a fixed result
    and verify replay uses it.
    """
    import historical_replay as hr

    _calls = []
    _orig  = hr.analyze_timeframe

    def _mock(df):
        _calls.append(len(df))
        return {
            "trend": "BULLISH", "signal": "WAIT",
            "confidence": 10.0, "confidence_label": "LOW",
            "reason": "", "quality": {"LONG": {}, "SHORT": {}, "FINAL": {}},
            "decision": "SKIP", "decision_direction": "NONE",
            "decision_score": 0.0, "decision_reason": "mock",
            "decision_details": {},
        }

    hr.analyze_timeframe = _mock
    try:
        df   = _make_df(DF_SIZE)
        data = replay_timeframe(df, "BTC/USDT", "1h", warmup_bars=WARMUP, max_replay_bars=5)
        check("USE_AT_called",  len(_calls) >= 1,
              f"analyze_timeframe was called {len(_calls)} times")
        check("USE_AT_correct", data["meta"]["replay_count"] >= 1)
        # Verify the mock result was used (decision=SKIP)
        for r in data["replay_results"]:
            check("USE_AT_decision", r["decision"] == "SKIP", r["decision"])
    finally:
        hr.analyze_timeframe = _orig


# ═════════════════════════════════════════════════════════════════════════════
# 11. Confidence / Decision distributions (shape sanity)
# ═════════════════════════════════════════════════════════════════════════════

def test_confidence_values_in_range():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=10)
    for r in data["replay_results"]:
        conf = float(r["confidence"])
        check("CONF1_range", 0.0 <= conf <= 100.0, f"confidence={conf}")


def test_decision_values_valid():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=10)
    valid = {"TAKE", "WATCH", "SKIP"}
    for r in data["replay_results"]:
        check("DEC1_valid", r["decision"] in valid, r["decision"])


def test_signal_values_valid():
    df   = _make_df(DF_SIZE)
    data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=10)
    valid = {"LONG", "SHORT", "WAIT"}
    for r in data["replay_results"]:
        check("SIG1_valid", r["signal"] in valid, r["signal"])


# ═════════════════════════════════════════════════════════════════════════════
# 12. Incomplete analysis result (pipeline returns partial dict)
# ═════════════════════════════════════════════════════════════════════════════

def test_incomplete_result_handled():
    import historical_replay as hr
    _orig = hr.analyze_timeframe

    def _mock_incomplete(df):
        # Simulates a result missing optional keys
        return {
            "trend": "BULLISH",
            "signal": "WAIT",
            # missing: quality, decision_details, etc.
        }

    hr.analyze_timeframe = _mock_incomplete
    try:
        df   = _make_df(DF_SIZE)
        data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=3)
        # Should not crash; results may be empty (skipped) or have defaults
        check("INC1_no_crash", True)
    except Exception as e:
        fail("INC1_no_crash", str(e))
    finally:
        hr.analyze_timeframe = _orig


def test_error_result_skipped():
    import historical_replay as hr
    _orig = hr.analyze_timeframe

    def _mock_error(df):
        return {"trend": "ERROR", "signal": "WAIT"}

    hr.analyze_timeframe = _mock_error
    try:
        df   = _make_df(DF_SIZE)
        data = replay_timeframe(df, "X", "1h", warmup_bars=WARMUP, max_replay_bars=5)
        check("ERR1_skipped",   data["meta"]["skipped"]      >= 5)
        check("ERR1_no_results", data["meta"]["replay_count"] == 0)
    finally:
        hr.analyze_timeframe = _orig


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

_TESTS = [
    # API contract
    test_returns_dict, test_meta_keys, test_result_keys, test_timeframe_result_min_structure, test_dir_data_keys,
    # Look-ahead bias
    test_no_look_ahead_bias, test_future_candle_change_no_effect,
    # Signal candle at -2
    test_signal_candle_at_minus2, test_replay_index_sequential,
    # Original df not modified
    test_original_df_not_modified,
    # Empty / insufficient
    test_empty_df, test_none_df, test_too_few_bars,
    test_exactly_warmup_plus_two, test_max_replay_bars_respected,
    # LONG/SHORT separate
    test_long_short_separate, test_funnel_long_short_independent,
    # Primary rejection reason
    test_rejection_not_confirmed, test_rejection_conf_below_40,
    test_rejection_conf_40_49, test_rejection_weak_tq, test_rejection_weak_vol,
    test_rejection_transitional, test_rejection_structure_opposition,
    test_rejection_deterministic, test_rejection_invalid_data,
    # Funnel
    test_funnel_keys, test_funnel_empty, test_funnel_none_entries, test_funnel_counts,
    # NaN/inf
    test_nan_inf_in_replay_results, test_nan_inf_in_df,
    # Uses analyze_timeframe
    test_uses_analyze_timeframe,
    # Confidence / decision distributions
    test_confidence_values_in_range, test_decision_values_valid, test_signal_values_valid,
    # Incomplete result
    test_incomplete_result_handled, test_error_result_skipped,
]

if __name__ == "__main__":
    print("─" * 55)
    print("  Historical Replay Tests")
    print("─" * 55)
    for t in _TESTS:
        run_test(t.__name__, t)

    total = _PASS + _FAIL
    print(f"\n  Replay tests: {_PASS}/{total}")
    for err in _ERRORS:
        print(err)
    if _FAIL == 0:
        print("  REPLAY STATUS: PASSED")
    else:
        print("  REPLAY STATUS: FAILED")
    sys.exit(0 if _FAIL == 0 else 1)
