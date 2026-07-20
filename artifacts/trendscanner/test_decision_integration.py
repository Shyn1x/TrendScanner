"""
test_decision_integration.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Интеграционные тесты Decision Engine в analysis.py и multi_tf.py.

Покрытие:
  ANALYSIS.PY:
    - pipeline LONG + decision TAKE LONG
    - pipeline LONG + decision WATCH LONG
    - pipeline WAIT + decision SKIP
    - Decision Engine exception
    - TAKE invariant нарушен
    - decision не меняет signal
    - стабильный формат
    - decision_score 0–100
    - нет NaN/inf

  MULTI_TF.PY:
    - все TF TAKE LONG
    - все TF TAKE SHORT
    - смешанные TAKE/WATCH/SKIP
    - WATCH коэффициент 0.5
    - порог TAKE ≥ 55
    - порог TAKE ≤ -55
    - ниже порога → WATCH
    - FINAL.signal WAIT запрещает TAKE
    - FINAL.signal SHORT запрещает TAKE LONG
    - FINAL.signal LONG запрещает TAKE SHORT
    - старший TAKE-конфликт
    - старший SKIP не конфликтует
    - отсутствие Decision-данных
    - исключение одного TF
    - перенормировка весов
    - contribution рассчитан правильно
    - counts рассчитаны правильно
    - стабильный формат FINAL
    - LONG/SHORT симметрия
    - нет NaN/inf
"""

from __future__ import annotations
import sys, math, traceback
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np


# ─── счётчик ──────────────────────────────────────────────────────────────────
_results: list[tuple[str, bool, str]] = []


def _check(label: str, cond: bool, detail: str = "") -> bool:
    _results.append((label, cond, detail))
    mark = "  ✓ " if cond else "  ✗ "
    print(f"{mark} {label}")
    if not cond and detail:
        print(f"     → {detail}")
    return cond


def _no_nan_inf(d, path="root") -> list[str]:
    """Рекурсивно проверяет float-значения на NaN/inf."""
    problems = []
    if isinstance(d, dict):
        for k, v in d.items():
            problems.extend(_no_nan_inf(v, f"{path}.{k}"))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            problems.extend(_no_nan_inf(v, f"{path}[{i}]"))
    elif isinstance(d, float):
        if math.isnan(d) or math.isinf(d):
            problems.append(f"{path}={d}")
    return problems


# ─── фабрики ──────────────────────────────────────────────────────────────────

def _make_df() -> pd.DataFrame:
    """Минимальный DataFrame для подстановки в моки."""
    n = 50
    return pd.DataFrame({
        "open":   np.linspace(100, 110, n),
        "close":  np.linspace(101, 111, n),
        "high":   np.linspace(102, 112, n),
        "low":    np.linspace(99,  109, n),
        "volume": np.ones(n) * 1000,
    })


def _make_quality(
    signal: str = "LONG",
    confidence: float = 75.0,
    confirmed: bool = True,
) -> dict:
    """Синтетический quality_result для мока."""
    return {
        "LONG": {
            "signal":    "LONG",
            "direction": "LONG",
            "confirmed": confirmed,
            "reason":    "test",
            "confidence": {"confidence": confidence, "label": "HIGH",
                           "available_components": 4},
            "trend_quality":    {"trend_quality_score": 65.0},
            "volume_quality":   {"volume_score": 70.0},
            "breakout_quality": {"breakout_score": 72.0, "confirmed": confirmed},
            "structure_quality": {"structure_score": 70.0, "alignment": "ALIGNED",
                                  "available": True},
            "market_structure": {"structure": "BULLISH", "bos": {
                "confirmed": True, "direction": "BULLISH", "price": 100.0, "candle_idx": 5},
                "choch": "NONE"},
        },
        "SHORT": {
            "signal":    "SHORT",
            "direction": "SHORT",
            "confirmed": False,
            "reason":    "test",
            "confidence": {"confidence": max(0.0, confidence - 20), "label": "MEDIUM",
                           "available_components": 4},
            "trend_quality":    {"trend_quality_score": 40.0},
            "volume_quality":   {"volume_score": 50.0},
            "breakout_quality": {"breakout_score": 45.0, "confirmed": False},
            "structure_quality": {"structure_score": 30.0, "alignment": "OPPOSED",
                                  "available": True},
            "market_structure": {"structure": "BULLISH", "bos": {
                "confirmed": True, "direction": "BULLISH", "price": 100.0, "candle_idx": 5},
                "choch": "NONE"},
        },
        "FINAL": {"signal": signal, "confidence": confidence, "label": "HIGH",
                  "reason": "test"},
    }


def _make_decision(
    decision: str = "TAKE",
    direction: str = "LONG",
    d_score: float = 85.0,
    breakout_confirmed: bool = True,
    dir_confidence: float = 75.0,
    long_decision: str = "TAKE",
    short_decision: str = "SKIP",
) -> dict:
    """Синтетический evaluate_both_directions result для мока."""
    def _dec_block(dec, dir_, sc, conf, bc):
        return {
            "direction": dir_,
            "decision": dec,
            "decision_score": sc,
            "confidence": conf,
            "breakout_confirmed": bc,
            "positive_factors": [],
            "warning_factors":  [],
            "blockers":         [],
            "component_scores": {},
            "market_context":   {"structure": "BULLISH", "alignment": "ALIGNED",
                                 "bos_direction": "BULLISH", "choch": "NONE"},
            "summary": "",
            "reason": "test",
        }

    return {
        "LONG": _dec_block(long_decision, "LONG", d_score,
                           dir_confidence if direction == "LONG" else 40.0,
                           breakout_confirmed if direction == "LONG" else False),
        "SHORT": _dec_block(short_decision, "SHORT", 30.0,
                            dir_confidence if direction == "SHORT" else 30.0,
                            False),
        "FINAL": {
            "decision":       decision,
            "direction":      direction,
            "decision_score": d_score,
            "confidence":     dir_confidence,
            "reason":         "test",
        },
    }


# ─── ключи стабильного формата ────────────────────────────────────────────────

_ANALYSIS_EXPECTED_KEYS = {
    "trend", "signal", "score", "confidence", "confidence_label",
    "reason", "quality",
    "decision", "decision_direction", "decision_score",
    "decision_reason", "decision_details",
}

_FINAL_DECISION_KEYS = {
    "decision", "decision_direction", "decision_score",
    "decision_directional_score", "decision_reason",
    "decision_counts", "decision_timeframe_components",
}

_COUNTS_KEYS = {"take_long", "take_short", "watch_long", "watch_short", "skip"}

_DTC_ITEM_KEYS = {
    "decision", "direction", "decision_score",
    "base_weight", "effective_weight", "contribution",
    "available", "reason",
}


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК A — analysis.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_analysis_pipeline_long_decision_take():
    """Pipeline LONG + Decision TAKE LONG → оба поля корректны."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A1 — pipeline LONG + decision TAKE LONG")
    print("────────────────────────────────────────────────────────────")
    import analysis
    qr  = _make_quality("LONG", 75.0, True)
    dec = _make_decision("TAKE", "LONG", 85.0, True, 75.0, "TAKE", "SKIP")

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    _check("A1.signal == LONG",             r["signal"] == "LONG")
    _check("A1.decision == TAKE",           r["decision"] == "TAKE")
    _check("A1.decision_direction == LONG", r["decision_direction"] == "LONG")
    _check("A1.decision_score > 0",         r["decision_score"] > 0)
    _check("A1.no_blockers",                not _no_nan_inf(r), str(_no_nan_inf(r)))
    print(f"     signal={r['signal']}, decision={r['decision']}, "
          f"d_score={r['decision_score']}")


def test_analysis_pipeline_long_decision_watch():
    """Pipeline LONG + Decision WATCH → signal unchanged."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A2 — pipeline LONG + decision WATCH")
    print("────────────────────────────────────────────────────────────")
    import analysis
    qr  = _make_quality("LONG", 75.0, True)
    dec = _make_decision("WATCH", "LONG", 60.0, 65.0, "WATCH", "SKIP")

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    _check("A2.signal == LONG",             r["signal"] == "LONG",
           f"got {r['signal']}")
    _check("A2.decision == WATCH",          r["decision"] == "WATCH",
           f"got {r['decision']}")
    _check("A2.decision_direction == LONG", r["decision_direction"] == "LONG")
    print(f"     signal={r['signal']}, decision={r['decision']}")


def test_analysis_pipeline_wait_decision_skip():
    """Pipeline WAIT + Decision SKIP."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A3 — pipeline WAIT + decision SKIP")
    print("────────────────────────────────────────────────────────────")
    import analysis
    qr  = _make_quality("WAIT", 48.0, False)
    dec = _make_decision("SKIP", "NONE", 0.0, 0.0, "SKIP", "SKIP")
    dec["FINAL"]["direction"] = "NONE"
    dec["FINAL"]["decision_score"] = 0.0

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    _check("A3.signal == WAIT",              r["signal"] == "WAIT",
           f"got {r['signal']}")
    _check("A3.decision == SKIP",            r["decision"] == "SKIP",
           f"got {r['decision']}")
    _check("A3.decision_direction == NONE",  r["decision_direction"] == "NONE")
    print(f"     signal={r['signal']}, decision={r['decision']}")


def test_analysis_decision_engine_exception():
    """Decision Engine exception → analyse не падает, decision = SKIP."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A4 — Decision Engine exception → no crash")
    print("────────────────────────────────────────────────────────────")
    import analysis
    qr = _make_quality("LONG", 75.0, True)

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions",
                      side_effect=RuntimeError("boom")):
        r = analysis.analyze_timeframe(_make_df())

    _check("A4.no_crash",        isinstance(r, dict))
    _check("A4.signal intact",   r["signal"] == "LONG",
           f"got {r['signal']}")
    _check("A4.confidence ok",   r["confidence"] > 0)
    _check("A4.decision == SKIP", r["decision"] == "SKIP",
           f"got {r['decision']}")
    _check("A4.d_score == 0",    r["decision_score"] == 0.0)
    _check("A4.reason has error", "exception" in r["decision_reason"].lower()
           or "boom" in r["decision_reason"],
           f"reason={r['decision_reason']!r}")
    print(f"     signal={r['signal']}, decision={r['decision']}, "
          f"reason={r['decision_reason']!r}")


def test_analysis_take_invariant_pipeline_signal_mismatch():
    """TAKE invariant: pipeline signal != decision_direction → WATCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A5 — TAKE invariant: signal mismatch → WATCH")
    print("────────────────────────────────────────────────────────────")
    import analysis
    # pipeline signal = WAIT, but DE says TAKE LONG
    qr  = _make_quality("WAIT", 48.0, False)
    dec = _make_decision("TAKE", "LONG", 85.0, True, 75.0, "TAKE", "SKIP")

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    _check("A5.signal == WAIT",             r["signal"] == "WAIT",
           f"got {r['signal']}")
    _check("A5.decision != TAKE",           r["decision"] != "TAKE",
           f"got {r['decision']}")
    _check("A5.decision_direction == NONE", r["decision_direction"] == "NONE",
           f"got {r['decision_direction']}")
    _check("A5.reason has invariant",
           "invariant" in r["decision_reason"].lower(),
           f"reason={r['decision_reason']!r}")
    print(f"     decision={r['decision']}, reason={r['decision_reason']!r}")


def test_analysis_take_invariant_low_confidence():
    """TAKE invariant: directional confidence < 70 → WATCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A6 — TAKE invariant: low directional confidence → WATCH")
    print("────────────────────────────────────────────────────────────")
    import analysis
    qr  = _make_quality("LONG", 75.0, True)
    # LONG directional decision TAKE but confidence = 65 < 70
    dec = _make_decision("TAKE", "LONG", 85.0, True, 65.0, "TAKE", "SKIP")

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    _check("A6.decision != TAKE",           r["decision"] != "TAKE",
           f"got {r['decision']}")
    _check("A6.decision_direction == NONE", r["decision_direction"] == "NONE")
    _check("A6.invariant in reason",
           "invariant" in r["decision_reason"].lower(),
           f"reason={r['decision_reason']!r}")
    print(f"     decision={r['decision']}, reason={r['decision_reason']!r}")


def test_analysis_take_invariant_no_breakout():
    """TAKE invariant: breakout_confirmed=False → WATCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A7 — TAKE invariant: breakout not confirmed → WATCH")
    print("────────────────────────────────────────────────────────────")
    import analysis
    qr  = _make_quality("LONG", 75.0, True)
    dec = _make_decision("TAKE", "LONG", 85.0, False, 75.0, "TAKE", "SKIP")

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    _check("A7.decision != TAKE", r["decision"] != "TAKE",
           f"got {r['decision']}")
    _check("A7.invariant in reason",
           "invariant" in r["decision_reason"].lower(),
           f"reason={r['decision_reason']!r}")
    print(f"     decision={r['decision']}, reason={r['decision_reason']!r}")


def test_analysis_decision_does_not_change_signal():
    """Decision Engine не изменяет pipeline signal."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A8 — Decision не меняет signal")
    print("────────────────────────────────────────────────────────────")
    import analysis

    for sig, conf, confirmed, dec_str, dec_dir in [
        ("LONG",  75.0, True,  "TAKE",  "LONG"),
        ("LONG",  75.0, True,  "WATCH", "LONG"),
        ("WAIT",  48.0, False, "SKIP",  "NONE"),
    ]:
        qr  = _make_quality(sig, conf, confirmed)
        dec = _make_decision(dec_str, dec_dir)
        if dec_dir == "NONE":
            dec["FINAL"]["direction"] = "NONE"

        with patch.object(analysis, "analyze_both_directions", return_value=qr), \
             patch.object(analysis, "evaluate_both_directions", return_value=dec):
            r = analysis.analyze_timeframe(_make_df())

        _check(f"A8.signal={sig} preserved",
               r["signal"] == sig,
               f"got {r['signal']}, decision={r['decision']}")
    print("     ✓ signal unchanged for LONG/LONG/WAIT")


def test_analysis_result_format():
    """Стабильный формат результата."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A9 — стабильный формат результата")
    print("────────────────────────────────────────────────────────────")
    import analysis
    qr  = _make_quality("LONG", 75.0, True)
    dec = _make_decision("TAKE", "LONG", 85.0, True, 75.0)

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    missing = _ANALYSIS_EXPECTED_KEYS - set(r.keys())
    _check("A9.all expected keys",  not missing, f"missing={missing}")
    _check("A9.decision in valid",  r["decision"] in ("TAKE", "WATCH", "SKIP"))
    _check("A9.d_dir in valid",     r["decision_direction"] in ("LONG", "SHORT", "NONE"))
    _check("A9.d_score is float",   isinstance(r["decision_score"], float))
    _check("A9.details has FINAL",  "FINAL" in r.get("decision_details", {}))
    print(f"     keys present: ✓")


def test_analysis_decision_score_in_range():
    """decision_score ∈ [0, 100] для всех случаев."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A10 — decision_score ∈ [0, 100]")
    print("────────────────────────────────────────────────────────────")
    import analysis

    for label, sig, conf, confirmed, dec_str, dec_dir, d_sc in [
        ("take",   "LONG", 75.0, True,  "TAKE",  "LONG", 85.0),
        ("watch",  "LONG", 75.0, True,  "WATCH", "LONG", 55.0),
        ("skip",   "WAIT", 45.0, False, "SKIP",  "NONE", 0.0),
        ("error",  "WAIT", 48.0, False, "SKIP",  "NONE", 0.0),
    ]:
        qr  = _make_quality(sig, conf, confirmed)
        dec = _make_decision(dec_str, dec_dir, d_sc)
        if dec_dir == "NONE":
            dec["FINAL"]["direction"] = "NONE"

        with patch.object(analysis, "analyze_both_directions", return_value=qr), \
             patch.object(analysis, "evaluate_both_directions", return_value=dec):
            r = analysis.analyze_timeframe(_make_df())

        sc = r["decision_score"]
        ok = isinstance(sc, float) and 0.0 <= sc <= 100.0
        _check(f"A10.{label}.score_in_range", ok, f"got {sc}")
    print("     ✓ all scores in [0, 100]")


def test_analysis_no_nan_inf():
    """Нет NaN/inf ни при каком входе."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-A11 — нет NaN/inf")
    print("────────────────────────────────────────────────────────────")
    import analysis

    qr  = _make_quality("LONG", 75.0, True)
    dec = _make_decision("TAKE", "LONG", 85.0, True, 75.0)

    with patch.object(analysis, "analyze_both_directions", return_value=qr), \
         patch.object(analysis, "evaluate_both_directions", return_value=dec):
        r = analysis.analyze_timeframe(_make_df())

    problems = _no_nan_inf({k: v for k, v in r.items()
                            if k not in ("quality", "decision_details")})
    _check("A11.no_nan_inf", not problems, str(problems))


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК B — multi_tf.py
# ═══════════════════════════════════════════════════════════════════════════════

# ── вспомогательные: результаты analyze_timeframe ────────────────────────────

def _at_result(
    signal: str = "LONG",
    confidence: float = 75.0,
    decision: str = "TAKE",
    decision_direction: str = "LONG",
    decision_score: float = 85.0,
    decision_reason: str = "test",
    trend: str = "BULLISH",
) -> dict:
    """Синтетический результат analyze_timeframe."""
    return {
        "trend":            trend,
        "signal":           signal,
        "score":            confidence,
        "confidence":       confidence,
        "confidence_label": "HIGH" if confidence >= 70 else "MEDIUM",
        "reason":           "test",
        "quality":          {},
        "decision":           decision,
        "decision_direction": decision_direction,
        "decision_score":     decision_score,
        "decision_reason":    decision_reason,
        "decision_details":   {},
    }


def _at_error(reason: str = "error") -> dict:
    return {
        "trend": "ERROR", "signal": "WAIT",
        "score": 0.0, "confidence": 0.0, "confidence_label": "LOW",
        "reason": reason, "quality": {},
        "decision": "SKIP", "decision_direction": "NONE",
        "decision_score": 0.0, "decision_reason": reason, "decision_details": {},
    }


def _patch_multi(side_effects: dict) -> dict:
    """
    Запускает multi_analysis с замоканным analyze_timeframe.
    side_effects: dict {tf: result_dict}  (или callable для исключений)
    """
    import multi_tf

    def fake_analyze_timeframe(df):
        # df — уже замоканный DataFrame с маркером tf
        tf = getattr(df, "_tf_label", None)
        if tf and tf in side_effects:
            r = side_effects[tf]
            if callable(r):
                raise r()
            return r
        return _at_result()

    def fake_get_data(symbol, tf):
        df = pd.DataFrame({"open": [1], "close": [1], "high": [1],
                           "low": [1], "volume": [1]})
        df._tf_label = tf
        return df

    with patch.object(multi_tf, "get_data", side_effect=fake_get_data), \
         patch.object(multi_tf, "analyze_timeframe", side_effect=fake_analyze_timeframe):
        return multi_tf.multi_analysis("BTC/USDT")


TFS = ["1M", "1w", "1d", "4h", "1h"]


def test_mtf_all_take_long():
    """Все TF TAKE LONG → TAKE LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B1 — все TF TAKE LONG → TAKE LONG")
    print("────────────────────────────────────────────────────────────")
    se = {tf: _at_result("LONG", 75.0, "TAKE", "LONG", 85.0) for tf in TFS}
    r  = _patch_multi(se)
    fin = r["FINAL"]

    _check("B1.decision == TAKE",           fin["decision"] == "TAKE",
           f"got {fin['decision']}")
    _check("B1.decision_direction == LONG", fin["decision_direction"] == "LONG",
           f"got {fin['decision_direction']}")
    _check("B1.d_score > 0",               fin["decision_score"] > 0)
    _check("B1.d_dir_score > 0",           fin["decision_directional_score"] > 0)
    _check("B1.counts.take_long >= 1",
           fin["decision_counts"]["take_long"] >= 1)
    print(f"     FINAL decision={fin['decision']}/{fin['decision_direction']}, "
          f"score={fin['decision_score']}")


def test_mtf_all_take_short():
    """Все TF TAKE SHORT → TAKE SHORT."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B2 — все TF TAKE SHORT → TAKE SHORT")
    print("────────────────────────────────────────────────────────────")
    se = {tf: _at_result("SHORT", 75.0, "TAKE", "SHORT", 85.0, trend="BEARISH")
          for tf in TFS}
    r  = _patch_multi(se)
    fin = r["FINAL"]

    _check("B2.decision == TAKE",            fin["decision"] == "TAKE",
           f"got {fin['decision']}")
    _check("B2.decision_direction == SHORT", fin["decision_direction"] == "SHORT",
           f"got {fin['decision_direction']}")
    _check("B2.d_dir_score < 0",            fin["decision_directional_score"] < 0)
    _check("B2.counts.take_short >= 1",
           fin["decision_counts"]["take_short"] >= 1)
    print(f"     FINAL decision={fin['decision']}/{fin['decision_direction']}, "
          f"score={fin['decision_score']}")


def test_mtf_mixed():
    """Смешанные TAKE/WATCH/SKIP → ненулевой score."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B3 — смешанные TAKE/WATCH/SKIP")
    print("────────────────────────────────────────────────────────────")
    se = {
        "1M": _at_result("LONG", 75.0, "TAKE",  "LONG", 85.0),
        "1w": _at_result("LONG", 70.0, "WATCH", "LONG", 55.0),
        "1d": _at_result("WAIT", 48.0, "SKIP",  "NONE",  0.0),
        "4h": _at_result("LONG", 60.0, "WATCH", "LONG", 50.0),
        "1h": _at_result("WAIT", 45.0, "SKIP",  "NONE",  0.0),
    }
    r  = _patch_multi(se)
    fin = r["FINAL"]

    _check("B3.decision_directional_score != 0",
           fin["decision_directional_score"] != 0.0,
           f"got {fin['decision_directional_score']}")
    _check("B3.decision in valid",
           fin["decision"] in ("TAKE", "WATCH", "SKIP"))
    _check("B3.counts.skip >= 2",
           fin["decision_counts"]["skip"] >= 2)
    print(f"     FINAL decision={fin['decision']}/{fin['decision_direction']}, "
          f"d_score={fin['decision_directional_score']:.2f}, "
          f"counts={fin['decision_counts']}")


def test_mtf_watch_half_coefficient():
    """WATCH получает коэффициент 0.5 к contribution."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B4 — WATCH получает коэффициент 0.5")
    print("────────────────────────────────────────────────────────────")
    import multi_tf

    # Только один TF (1M) — TAKE LONG, score=80
    se_take  = {tf: _at_result("WAIT", 45.0, "SKIP", "NONE", 0.0) for tf in TFS}
    se_watch = {tf: _at_result("WAIT", 45.0, "SKIP", "NONE", 0.0) for tf in TFS}
    se_take["1M"]  = _at_result("LONG", 75.0, "TAKE",  "LONG", 80.0)
    se_watch["1M"] = _at_result("LONG", 70.0, "WATCH", "LONG", 80.0)

    r_take  = _patch_multi(se_take)
    r_watch = _patch_multi(se_watch)

    d_take  = r_take["FINAL"]["decision_directional_score"]
    d_watch = r_watch["FINAL"]["decision_directional_score"]

    _check("B4.take_score > watch_score",
           d_take > d_watch,
           f"TAKE={d_take:.4f}, WATCH={d_watch:.4f}")

    # Ratio must be ~2 (WATCH = 0.5 × TAKE)
    if d_watch != 0.0:
        ratio = d_take / d_watch
        _check("B4.ratio ~2 (TAKE = 2 × WATCH)",
               abs(ratio - 2.0) < 0.01,
               f"ratio={ratio:.4f}")
    print(f"     TAKE_score={d_take:.4f}, WATCH_score={d_watch:.4f}")


def test_mtf_take_long_threshold_55():
    """TAKE LONG требует decision_directional_score >= 55."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B5 — TAKE порог +55")
    print("────────────────────────────────────────────────────────────")
    import multi_tf
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    # Строим decision_components вручную и проверяем _aggregate_decision
    # TAKE LONG на 1M (weight=1.0 after renorm), d_score=54 → below threshold
    comps_54 = {tf: {"available": False, "effective_weight": 0.0,
                     "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}
    comps_54["1M"] = {"available": True, "effective_weight": 1.0,
                      "base_weight": 0.35}

    result_54 = {tf: _at_result() for tf in TFS}
    result_54["1M"] = _at_result("LONG", 75.0, "TAKE", "LONG", 54.0)

    dec_54 = _aggregate_decision(result_54, comps_54, "LONG")
    _check("B5.score_54_not_TAKE",
           dec_54["decision"] != "TAKE",
           f"got {dec_54['decision']}, score={dec_54['decision_directional_score']}")

    # d_score=56 → above threshold
    result_56 = dict(result_54)
    result_56["1M"] = _at_result("LONG", 75.0, "TAKE", "LONG", 56.0)
    dec_56 = _aggregate_decision(result_56, comps_54, "LONG")
    _check("B5.score_56_TAKE",
           dec_56["decision"] == "TAKE",
           f"got {dec_56['decision']}, score={dec_56['decision_directional_score']}")
    print(f"     score<55: {dec_54['decision']}, score>55: {dec_56['decision']}")


def test_mtf_take_short_threshold_neg55():
    """TAKE SHORT требует decision_directional_score <= -55."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B6 — TAKE порог -55")
    print("────────────────────────────────────────────────────────────")
    import multi_tf
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    comps = {tf: {"available": False, "effective_weight": 0.0,
                  "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}
    comps["1M"] = {"available": True, "effective_weight": 1.0, "base_weight": 0.35}

    result_54  = {tf: _at_result() for tf in TFS}
    result_54["1M"] = _at_result("SHORT", 75.0, "TAKE", "SHORT", 54.0,
                                  trend="BEARISH")
    dec_54 = _aggregate_decision(result_54, comps, "SHORT")
    _check("B6.score_neg54_not_TAKE",
           dec_54["decision"] != "TAKE",
           f"got {dec_54['decision']}, score={dec_54['decision_directional_score']}")

    result_56  = dict(result_54)
    result_56["1M"] = _at_result("SHORT", 75.0, "TAKE", "SHORT", 56.0,
                                  trend="BEARISH")
    dec_56 = _aggregate_decision(result_56, comps, "SHORT")
    _check("B6.score_neg56_TAKE",
           dec_56["decision"] == "TAKE",
           f"got {dec_56['decision']}, score={dec_56['decision_directional_score']}")
    print(f"     score>-55: {dec_54['decision']}, score<-55: {dec_56['decision']}")


def test_mtf_below_take_threshold_watch():
    """score в +25..+54 → WATCH LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B7 — score 25–54 → WATCH LONG")
    print("────────────────────────────────────────────────────────────")
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    comps = {tf: {"available": False, "effective_weight": 0.0,
                  "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}
    comps["1M"] = {"available": True, "effective_weight": 1.0, "base_weight": 0.35}

    for d_sc in (25.0, 40.0, 54.9):
        result = {tf: _at_result() for tf in TFS}
        result["1M"] = _at_result("LONG", 75.0, "TAKE", "LONG", d_sc)
        dec = _aggregate_decision(result, comps, "LONG")
        _check(f"B7.score={d_sc} → WATCH",
               dec["decision"] == "WATCH",
               f"got {dec['decision']}, d_score={dec['decision_directional_score']:.2f}")
    print("     ✓ WATCH for scores 25, 40, 54.9")


def test_mtf_final_signal_wait_prohibits_take():
    """FINAL.signal == WAIT → MTF Decision не может быть TAKE."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B8 — FINAL.signal WAIT запрещает TAKE")
    print("────────────────────────────────────────────────────────────")
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    comps = {tf: {"available": False, "effective_weight": 0.0,
                  "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}
    comps["1M"] = {"available": True, "effective_weight": 1.0, "base_weight": 0.35}

    result = {tf: _at_result() for tf in TFS}
    result["1M"] = _at_result("LONG", 75.0, "TAKE", "LONG", 90.0)

    dec = _aggregate_decision(result, comps, "WAIT")  # final_signal = WAIT
    _check("B8.decision != TAKE",   dec["decision"] != "TAKE",
           f"got {dec['decision']}")
    _check("B8.WAIT in reason",     "WAIT" in dec["decision_reason"],
           f"reason={dec['decision_reason']!r}")
    print(f"     decision={dec['decision']}, reason={dec['decision_reason']!r}")


def test_mtf_final_signal_short_prohibits_take_long():
    """FINAL.signal == SHORT запрещает TAKE LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B9 — FINAL.signal SHORT запрещает TAKE LONG")
    print("────────────────────────────────────────────────────────────")
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    comps = {tf: {"available": False, "effective_weight": 0.0,
                  "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}
    comps["1M"] = {"available": True, "effective_weight": 1.0, "base_weight": 0.35}

    result = {tf: _at_result() for tf in TFS}
    result["1M"] = _at_result("LONG", 75.0, "TAKE", "LONG", 90.0)

    dec = _aggregate_decision(result, comps, "SHORT")  # final_signal = SHORT
    _check("B9.decision != TAKE_LONG",
           not (dec["decision"] == "TAKE" and dec["decision_direction"] == "LONG"),
           f"got {dec['decision']}/{dec['decision_direction']}")
    print(f"     decision={dec['decision']}/{dec['decision_direction']}")


def test_mtf_final_signal_long_prohibits_take_short():
    """FINAL.signal == LONG запрещает TAKE SHORT."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B10 — FINAL.signal LONG запрещает TAKE SHORT")
    print("────────────────────────────────────────────────────────────")
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    comps = {tf: {"available": False, "effective_weight": 0.0,
                  "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}
    comps["1M"] = {"available": True, "effective_weight": 1.0, "base_weight": 0.35}

    result = {tf: _at_result() for tf in TFS}
    result["1M"] = _at_result("SHORT", 75.0, "TAKE", "SHORT", 90.0, trend="BEARISH")

    dec = _aggregate_decision(result, comps, "LONG")  # final_signal = LONG
    _check("B10.decision != TAKE_SHORT",
           not (dec["decision"] == "TAKE" and dec["decision_direction"] == "SHORT"),
           f"got {dec['decision']}/{dec['decision_direction']}")
    print(f"     decision={dec['decision']}/{dec['decision_direction']}")


def test_mtf_htf_take_conflict():
    """HTF (1M) TAKE SHORT → блокирует TAKE LONG кандидата."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B11 — HTF TAKE SHORT блокирует TAKE LONG")
    print("────────────────────────────────────────────────────────────")
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    # 1M = TAKE SHORT, остальные = TAKE LONG с высоким score
    comps = {tf: {"available": True,
                  "effective_weight": TIMEFRAME_WEIGHTS[tf],
                  "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}

    result = {
        "1M": _at_result("SHORT", 75.0, "TAKE", "SHORT", 90.0, trend="BEARISH"),
        "1w": _at_result("LONG",  75.0, "TAKE", "LONG",  90.0),
        "1d": _at_result("LONG",  75.0, "TAKE", "LONG",  90.0),
        "4h": _at_result("LONG",  75.0, "TAKE", "LONG",  90.0),
        "1h": _at_result("LONG",  75.0, "TAKE", "LONG",  90.0),
    }
    dec = _aggregate_decision(result, comps, "LONG")
    _check("B11.decision != TAKE_LONG",
           not (dec["decision"] == "TAKE" and dec["decision_direction"] == "LONG"),
           f"got {dec['decision']}/{dec['decision_direction']}")
    print(f"     decision={dec['decision']}/{dec['decision_direction']}, "
          f"reason={dec['decision_reason']!r}")


def test_mtf_htf_skip_no_conflict():
    """HTF (1M) SKIP → не блокирует TAKE LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B12 — HTF SKIP не блокирует TAKE LONG")
    print("────────────────────────────────────────────────────────────")
    from multi_tf import _aggregate_decision, TIMEFRAME_WEIGHTS

    comps = {tf: {"available": True,
                  "effective_weight": TIMEFRAME_WEIGHTS[tf],
                  "base_weight": TIMEFRAME_WEIGHTS[tf]} for tf in TFS}

    result = {
        "1M": _at_result("WAIT", 45.0, "SKIP", "NONE", 0.0),
        "1w": _at_result("LONG", 75.0, "TAKE", "LONG", 85.0),
        "1d": _at_result("LONG", 75.0, "TAKE", "LONG", 85.0),
        "4h": _at_result("LONG", 75.0, "TAKE", "LONG", 85.0),
        "1h": _at_result("LONG", 75.0, "TAKE", "LONG", 85.0),
    }
    dec = _aggregate_decision(result, comps, "LONG")
    # Should not be blocked by SKIP on 1M
    _check("B12.1M_SKIP_not_blocking",
           not ("HTF conflict" in dec["decision_reason"] and dec["decision"] != "TAKE"),
           f"decision={dec['decision']}, reason={dec['decision_reason']!r}")
    print(f"     decision={dec['decision']}/{dec['decision_direction']}, "
          f"score={dec['decision_directional_score']:.2f}")


def test_mtf_no_decision_data():
    """Нет Decision-данных (все ERROR) → SKIP/NONE."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B13 — нет Decision-данных → SKIP/NONE")
    print("────────────────────────────────────────────────────────────")
    se = {tf: _at_error("no data") for tf in TFS}
    r  = _patch_multi(se)
    fin = r["FINAL"]

    _check("B13.decision == SKIP",           fin["decision"] == "SKIP",
           f"got {fin['decision']}")
    _check("B13.decision_direction == NONE", fin["decision_direction"] == "NONE",
           f"got {fin['decision_direction']}")
    _check("B13.decision_score == 0",        fin["decision_score"] == 0.0)
    print(f"     FINAL decision={fin['decision']}/{fin['decision_direction']}")


def test_mtf_one_tf_exception():
    """Исключение одного TF → остальные TF всё равно агрегируются."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B14 — исключение одного TF → агрегация продолжается")
    print("────────────────────────────────────────────────────────────")
    import multi_tf

    call_count = {"n": 0}

    def fake_analyze(df):
        tf = getattr(df, "_tf_label", None)
        if tf == "1d":
            raise RuntimeError("1d failed")
        call_count["n"] += 1
        return _at_result("LONG", 75.0, "TAKE", "LONG", 85.0)

    def fake_get_data(symbol, tf):
        df = pd.DataFrame({"open": [1], "close": [1], "high": [1],
                           "low": [1], "volume": [1]})
        df._tf_label = tf
        return df

    with patch.object(multi_tf, "get_data",         side_effect=fake_get_data), \
         patch.object(multi_tf, "analyze_timeframe", side_effect=fake_analyze):
        r = multi_tf.multi_analysis("BTC/USDT")

    fin = r["FINAL"]
    _check("B14.no_crash",           isinstance(fin, dict))
    _check("B14.available < 5",      fin["available_timeframes"] < 5,
           f"got {fin['available_timeframes']}")
    _check("B14.decision in valid",  fin["decision"] in ("TAKE","WATCH","SKIP"))
    _check("B14.d_dir in valid",     fin["decision_direction"] in ("LONG","SHORT","NONE"))
    print(f"     available={fin['available_timeframes']}, "
          f"decision={fin['decision']}/{fin['decision_direction']}")


def test_mtf_contribution_formula():
    """Contribution рассчитан правильно по формуле."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B15 — contribution formula")
    print("────────────────────────────────────────────────────────────")
    from multi_tf import _build_decision_component, TIMEFRAME_WEIGHTS

    eff_w = 0.5
    d_score = 80.0
    comp_base = {"available": True, "effective_weight": eff_w,
                 "base_weight": TIMEFRAME_WEIGHTS["1M"]}

    cases = [
        ("TAKE",  "LONG",  +d_score * eff_w),
        ("TAKE",  "SHORT", -d_score * eff_w),
        ("WATCH", "LONG",  +d_score * eff_w * 0.5),
        ("WATCH", "SHORT", -d_score * eff_w * 0.5),
        ("WATCH", "NONE",  0.0),
        ("SKIP",  "NONE",  0.0),
    ]
    for dec, dir_, expected_c in cases:
        r_tf = _at_result("LONG", 75.0, dec, dir_, d_score)
        comp = _build_decision_component("1M", r_tf, comp_base)
        got = comp["contribution"]
        ok  = abs(got - expected_c) < 1e-6
        _check(f"B15.{dec}/{dir_}_contrib={expected_c:.2f}", ok,
               f"got {got:.4f}")
    print("     ✓ all contributions correct")


def test_mtf_counts():
    """Counts рассчитаны правильно."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B16 — counts")
    print("────────────────────────────────────────────────────────────")
    se = {
        "1M": _at_result("LONG",  75.0, "TAKE",  "LONG",  85.0),
        "1w": _at_result("SHORT", 75.0, "TAKE",  "SHORT", 85.0),
        "1d": _at_result("LONG",  65.0, "WATCH", "LONG",  55.0),
        "4h": _at_result("SHORT", 60.0, "WATCH", "SHORT", 50.0),
        "1h": _at_result("WAIT",  45.0, "SKIP",  "NONE",   0.0),
    }
    r   = _patch_multi(se)
    cnt = r["FINAL"]["decision_counts"]

    _check("B16.take_long == 1",   cnt["take_long"]  == 1, str(cnt))
    _check("B16.take_short == 1",  cnt["take_short"] == 1, str(cnt))
    _check("B16.watch_long == 1",  cnt["watch_long"] == 1, str(cnt))
    _check("B16.watch_short == 1", cnt["watch_short"] == 1, str(cnt))
    _check("B16.skip >= 1",        cnt["skip"] >= 1, str(cnt))
    print(f"     counts={cnt}")


def test_mtf_final_format_stable():
    """Стабильный формат FINAL с decision-полями."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B17 — стабильный формат FINAL")
    print("────────────────────────────────────────────────────────────")
    se = {tf: _at_result("LONG", 75.0, "TAKE", "LONG", 85.0) for tf in TFS}
    r  = _patch_multi(se)
    fin = r["FINAL"]

    missing_dec = _FINAL_DECISION_KEYS - set(fin.keys())
    _check("B17.all decision keys", not missing_dec, f"missing={missing_dec}")

    cnt = fin.get("decision_counts", {})
    missing_cnt = _COUNTS_KEYS - set(cnt.keys())
    _check("B17.counts keys", not missing_cnt, f"missing={missing_cnt}")

    dtc = fin.get("decision_timeframe_components", {})
    _check("B17.dtc has 5 TFs", len(dtc) == 5, f"got {len(dtc)}")
    for tf in TFS:
        if tf in dtc:
            missing_item = _DTC_ITEM_KEYS - set(dtc[tf].keys())
            _check(f"B17.dtc.{tf} keys", not missing_item,
                   f"missing={missing_item}")

    _check("B17.d_score float",
           isinstance(fin["decision_score"], float))
    _check("B17.d_dir_score float",
           isinstance(fin["decision_directional_score"], float))
    _check("B17.decision in valid",
           fin["decision"] in ("TAKE","WATCH","SKIP"))
    _check("B17.direction in valid",
           fin["decision_direction"] in ("LONG","SHORT","NONE"))
    print("     ✓ all keys present")


def test_mtf_long_short_symmetry():
    """LONG/SHORT симметрия решений."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B18 — LONG/SHORT симметрия")
    print("────────────────────────────────────────────────────────────")
    se_long  = {tf: _at_result("LONG",  75.0, "TAKE",  "LONG",  85.0) for tf in TFS}
    se_short = {tf: _at_result("SHORT", 75.0, "TAKE",  "SHORT", 85.0,
                               trend="BEARISH") for tf in TFS}

    r_long  = _patch_multi(se_long)
    r_short = _patch_multi(se_short)

    fl = r_long["FINAL"]
    fs = r_short["FINAL"]

    _check("B18.LONG decision == TAKE",  fl["decision"] == "TAKE",
           f"got {fl['decision']}")
    _check("B18.SHORT decision == TAKE", fs["decision"] == "TAKE",
           f"got {fs['decision']}")
    _check("B18.LONG direction == LONG", fl["decision_direction"] == "LONG")
    _check("B18.SHORT direction == SHORT", fs["decision_direction"] == "SHORT")
    _check("B18.scores_abs_equal",
           abs(abs(fl["decision_directional_score"]) -
               abs(fs["decision_directional_score"])) < 0.01,
           f"LONG={fl['decision_directional_score']:.4f}, "
           f"SHORT={fs['decision_directional_score']:.4f}")
    _check("B18.signs_opposite",
           fl["decision_directional_score"] > 0
           and fs["decision_directional_score"] < 0)
    print(f"     LONG d_score={fl['decision_directional_score']:.4f}, "
          f"SHORT d_score={fs['decision_directional_score']:.4f}")


def test_mtf_no_nan_inf():
    """Нет NaN/inf в FINAL decision-полях."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B19 — нет NaN/inf")
    print("────────────────────────────────────────────────────────────")
    se = {tf: _at_result("LONG", 75.0, "TAKE", "LONG", 85.0) for tf in TFS}
    r  = _patch_multi(se)
    fin = r["FINAL"]

    dec_fields = {k: fin[k] for k in _FINAL_DECISION_KEYS if k in fin
                  and k != "decision_timeframe_components"}
    problems = _no_nan_inf(dec_fields)
    _check("B19.no_nan_inf", not problems, str(problems))
    print("     ✓ no NaN/inf")


def test_mtf_renormalization():
    """Перенормировка весов при недоступных таймфреймах."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DI-B20 — перенормировка весов")
    print("────────────────────────────────────────────────────────────")
    # Только 1M и 1w доступны
    se = {
        "1M": _at_result("LONG", 75.0, "TAKE", "LONG", 85.0),
        "1w": _at_result("LONG", 75.0, "TAKE", "LONG", 85.0),
        "1d": _at_error(),
        "4h": _at_error(),
        "1h": _at_error(),
    }
    r   = _patch_multi(se)
    fin = r["FINAL"]

    _check("B20.available == 2", fin["available_timeframes"] == 2,
           f"got {fin['available_timeframes']}")
    _check("B20.decision in valid",
           fin["decision"] in ("TAKE","WATCH","SKIP"))

    # Проверяем суммарный effective_weight = 1 для доступных TF
    dtc = fin.get("decision_timeframe_components", {})
    total_ew = sum(dtc[tf]["effective_weight"] for tf in TFS
                   if dtc.get(tf, {}).get("available", False))
    _check("B20.sum_eff_weights == 1",
           abs(total_ew - 1.0) < 1e-6,
           f"got {total_ew:.6f}")
    print(f"     sum_eff_weights={total_ew:.6f}, decision={fin['decision']}")


# ═══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════════

def _run_all():
    tests = [
        # analysis.py
        test_analysis_pipeline_long_decision_take,
        test_analysis_pipeline_long_decision_watch,
        test_analysis_pipeline_wait_decision_skip,
        test_analysis_decision_engine_exception,
        test_analysis_take_invariant_pipeline_signal_mismatch,
        test_analysis_take_invariant_low_confidence,
        test_analysis_take_invariant_no_breakout,
        test_analysis_decision_does_not_change_signal,
        test_analysis_result_format,
        test_analysis_decision_score_in_range,
        test_analysis_no_nan_inf,
        # multi_tf.py
        test_mtf_all_take_long,
        test_mtf_all_take_short,
        test_mtf_mixed,
        test_mtf_watch_half_coefficient,
        test_mtf_take_long_threshold_55,
        test_mtf_take_short_threshold_neg55,
        test_mtf_below_take_threshold_watch,
        test_mtf_final_signal_wait_prohibits_take,
        test_mtf_final_signal_short_prohibits_take_long,
        test_mtf_final_signal_long_prohibits_take_short,
        test_mtf_htf_take_conflict,
        test_mtf_htf_skip_no_conflict,
        test_mtf_no_decision_data,
        test_mtf_one_tf_exception,
        test_mtf_contribution_formula,
        test_mtf_counts,
        test_mtf_final_format_stable,
        test_mtf_long_short_symmetry,
        test_mtf_no_nan_inf,
        test_mtf_renormalization,
    ]

    print("\n" + "═"*60)
    print("  DECISION INTEGRATION TESTS (v0.5)")
    print("═"*60)

    failed_tests = []
    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            failed_tests.append((test_fn.__name__, str(e)))
            print(f"\n  ✗ EXCEPTION in {test_fn.__name__}: {e}")
            traceback.print_exc()

    total  = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed

    a_labels  = [l for l, *_ in _results if l.startswith("A")]
    a_passed  = sum(1 for l, ok, _ in _results if l.startswith("A") and ok)
    b_labels  = [l for l, *_ in _results if l.startswith("B")]
    b_passed  = sum(1 for l, ok, _ in _results if l.startswith("B") and ok)

    print("\n" + "═"*60)
    print(f"  Decision integration tests: {passed}/{total}")
    print()
    print(f"  Analysis Decision tests:    {a_passed}/{len(a_labels)}")
    print(f"  MTF Decision tests:         {b_passed}/{len(b_labels)}")

    if failed_tests:
        print(f"\n  Exceptions in {len(failed_tests)} test(s):")
        for name, err in failed_tests:
            print(f"    - {name}: {err}")

    if failed > 0:
        print(f"\n  Failed checks ({failed}):")
        for label, ok, detail in _results:
            if not ok:
                print(f"    ✗ {label}" + (f" — {detail}" if detail else ""))

    print()
    if failed == 0 and not failed_tests:
        print("  DECISION INTEGRATION STATUS: PASSED")
    else:
        print("  DECISION INTEGRATION STATUS: FAILED")
    print("═"*60)

    return passed, total


if __name__ == "__main__":
    passed, total = _run_all()
    sys.exit(0 if passed == total else 1)
