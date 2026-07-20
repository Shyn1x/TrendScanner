"""
test_analysis_integration.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Интеграционные тесты для analysis.py и multi_tf.py.

Использует mock/patch — НЕ обращается к бирже.
Не ослабляет test_v04.py и test_quality_pipeline.py.

Запуск: python test_analysis_integration.py
"""

import sys
import math
import traceback
import unittest.mock as mock

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
#  Инструменты (копия из test_quality_pipeline.py)
# ─────────────────────────────────────────────────────────────────────────────

_passed: list[str] = []
_failed: list[str] = []


def ok(name: str, detail: str = "") -> None:
    _passed.append(name)
    print(f"  ✓  {name}" + (f"  ({detail})" if detail else ""))


def fail(name: str, reason: str) -> None:
    _failed.append(name)
    print(f"  ✗  {name}  ←  {reason}")


def check(cond: bool, name: str, reason: str = "", detail: str = "") -> bool:
    if cond:
        ok(name, detail)
    else:
        fail(name, reason or "assertion failed")
    return cond


def assert_in_range(val, lo, hi, name: str) -> bool:
    return check(lo <= val <= hi, name,
                 f"got {val!r}, expected [{lo}, {hi}]", f"{val}")


def assert_approx(val: float, expected: float, name: str, tol: float = 0.01) -> bool:
    ok_flag = math.isfinite(val) and abs(val - expected) <= tol
    return check(ok_flag, name,
                 f"got {val!r}, expected ≈{expected} (±{tol})", f"{val:.4f}")


def assert_no_nan_inf(val, name: str) -> bool:
    if isinstance(val, (int, float)):
        return check(math.isfinite(float(val)), name, f"got {val!r} (NaN or Inf)")
    return check(True, name)


def assert_type(val, typ, name: str) -> bool:
    return check(isinstance(val, typ), name,
                 f"expected {typ.__name__}, got {type(val).__name__}")


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─────────────────────────────────────────────────────────────────────────────
#  Фабрики mock-данных
# ─────────────────────────────────────────────────────────────────────────────

VALID_SIGNALS = {"LONG", "SHORT", "WAIT"}
VALID_LABELS  = {"LOW", "MEDIUM", "HIGH", "VERY HIGH"}

EXPECTED_ANALYZE_KEYS = {
    "trend", "signal", "score", "confidence",
    "confidence_label", "reason", "quality",
}

EXPECTED_FINAL_KEYS = {
    "signal", "score", "confidence", "directional_score",
    "confidence_label", "long_timeframes", "short_timeframes",
    "wait_timeframes", "available_timeframes", "reason",
    "timeframe_components",
}

EXPECTED_COMPONENT_KEYS = {
    "signal", "confidence", "base_weight", "effective_weight",
    "contribution", "available", "reason",
}

TIMEFRAMES = ["1M", "1w", "1d", "4h", "1h"]

TIMEFRAME_WEIGHTS = {
    "1M": 0.35, "1w": 0.25, "1d": 0.20, "4h": 0.12, "1h": 0.08,
}


def make_analyze_result(signal: str, confidence: float,
                        trend: str | None = None,
                        reason: str = "") -> dict:
    """Создаёт mock-результат analyze_timeframe."""
    if trend is None:
        trend = {"LONG": "BULLISH", "SHORT": "BEARISH",
                 "WAIT": "NEUTRAL", "ERROR": "ERROR"}.get(signal, "NEUTRAL")
    label = "LOW"
    if confidence >= 85: label = "VERY HIGH"
    elif confidence >= 70: label = "HIGH"
    elif confidence >= 40: label = "MEDIUM"
    return {
        "trend":            trend,
        "signal":           signal if signal != "ERROR" else "WAIT",
        "score":            confidence,
        "confidence":       confidence,
        "confidence_label": label,
        "reason":           reason or signal,
        "quality":          {},
    }


def make_quality_result(final_signal: str, confidence: float,
                        reason: str = "") -> dict:
    """Создаёт mock-результат analyze_both_directions для analyze_timeframe."""
    label = "LOW"
    if confidence >= 85: label = "VERY HIGH"
    elif confidence >= 70: label = "HIGH"
    elif confidence >= 40: label = "MEDIUM"
    return {
        "LONG":  {"direction": "LONG",  "signal": final_signal if final_signal == "LONG"  else "WAIT",
                  "confirmed": final_signal == "LONG",  "line": None,
                  "trend_quality": {}, "volume_quality": {}, "breakout_quality": {"breakout_score": 0},
                  "confidence": {"confidence": confidence if final_signal == "LONG"  else 0.0}, "reason": ""},
        "SHORT": {"direction": "SHORT", "signal": final_signal if final_signal == "SHORT" else "WAIT",
                  "confirmed": final_signal == "SHORT", "line": None,
                  "trend_quality": {}, "volume_quality": {}, "breakout_quality": {"breakout_score": 0},
                  "confidence": {"confidence": confidence if final_signal == "SHORT" else 0.0}, "reason": ""},
        "FINAL": {"signal": final_signal, "confidence": confidence,
                  "label": label, "reason": reason or final_signal},
    }


def make_small_df() -> pd.DataFrame:
    return pd.DataFrame({
        "open": [100.0]*5, "high": [101.0]*5,
        "low":  [99.0]*5,  "close":[100.5]*5, "volume":[1000.0]*5,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Формат-валидаторы
# ─────────────────────────────────────────────────────────────────────────────

def assert_analyze_tf_format(r: dict, tag: str) -> None:
    actual = set(r.keys())
    check(EXPECTED_ANALYZE_KEYS == actual,
          f"{tag}.keys == expected",
          f"missing={EXPECTED_ANALYZE_KEYS - actual}, extra={actual - EXPECTED_ANALYZE_KEYS}")
    check(r["signal"] in VALID_SIGNALS,     f"{tag}.signal valid",   f"got {r['signal']!r}")
    check(r["trend"]  in ("BULLISH","BEARISH","NEUTRAL","ERROR"),
          f"{tag}.trend valid", f"got {r['trend']!r}")
    check(r["confidence_label"] in VALID_LABELS, f"{tag}.label valid", f"got {r['confidence_label']!r}")
    assert_type(r["reason"], str, f"{tag}.reason is str")
    assert_in_range(float(r["confidence"]), 0, 100, f"{tag}.confidence in [0,100]")
    assert_in_range(float(r["score"]),      0, 100, f"{tag}.score in [0,100]")
    assert_no_nan_inf(float(r["confidence"]), f"{tag}.confidence not NaN/Inf")
    assert_no_nan_inf(float(r["score"]),      f"{tag}.score not NaN/Inf")


def assert_final_format(final: dict, tag: str) -> None:
    actual = set(final.keys())
    missing = EXPECTED_FINAL_KEYS - actual
    check(not missing, f"{tag}.FINAL.keys complete", f"missing={missing}")
    check(final.get("signal") in VALID_SIGNALS,  f"{tag}.FINAL.signal valid",
          f"got {final.get('signal')!r}")
    check(final.get("confidence_label") in VALID_LABELS,
          f"{tag}.FINAL.label valid", f"got {final.get('confidence_label')!r}")
    assert_in_range(float(final.get("confidence", -1)), 0, 100, f"{tag}.FINAL.confidence in [0,100]")
    assert_in_range(float(final.get("score",      -1)), 0, 100, f"{tag}.FINAL.score in [0,100]")
    assert_in_range(float(final.get("directional_score", -999)), -100, 100,
                    f"{tag}.FINAL.directional_score in [-100,100]")
    for key in ("long_timeframes","short_timeframes","wait_timeframes","available_timeframes"):
        assert_type(final.get(key), int, f"{tag}.FINAL.{key} is int")
    assert_type(final.get("reason"), str, f"{tag}.FINAL.reason is str")
    assert_type(final.get("timeframe_components"), dict, f"{tag}.FINAL.timeframe_components is dict")


def assert_component_format(comp: dict, tf: str, tag: str) -> None:
    actual = set(comp.keys())
    missing = EXPECTED_COMPONENT_KEYS - actual
    check(not missing, f"{tag}.comp[{tf}].keys complete", f"missing={missing}")
    assert_type(comp.get("available"), bool,  f"{tag}.comp[{tf}].available is bool")
    assert_in_range(float(comp.get("base_weight",    -1)), 0, 1,
                    f"{tag}.comp[{tf}].base_weight in [0,1]")
    assert_in_range(float(comp.get("effective_weight",-1)), 0, 1,
                    f"{tag}.comp[{tf}].effective_weight in [0,1]")
    assert_no_nan_inf(float(comp.get("contribution", math.nan)),
                      f"{tag}.comp[{tf}].contribution not NaN/Inf")


# ═════════════════════════════════════════════════════════════════════════════
#  ЧАСТЬ 1: analysis.py
# ═════════════════════════════════════════════════════════════════════════════

def test_analyze_long():
    section("AF1 — analyze_timeframe: LONG сигнал")

    qr = make_quality_result("LONG", 72.5, "LONG confirmed")

    with mock.patch("analysis.analyze_both_directions", return_value=qr):
        from analysis import analyze_timeframe
        r = analyze_timeframe(make_small_df())

    assert_analyze_tf_format(r, "AF1")
    check(r["signal"] == "LONG",     "AF1.signal == LONG",    f"got {r['signal']!r}")
    check(r["trend"]  == "BULLISH",  "AF1.trend == BULLISH",  f"got {r['trend']!r}")
    assert_approx(r["confidence"], 72.5, "AF1.confidence == 72.5", tol=0.1)
    assert_approx(r["score"],      72.5, "AF1.score == confidence")
    check(r["confidence_label"] == "HIGH", "AF1.label == HIGH",
          f"got {r['confidence_label']!r}")
    check(isinstance(r["quality"], dict), "AF1.quality is dict")
    print(f"     signal={r['signal']}, trend={r['trend']}, conf={r['confidence']}")


def test_analyze_short():
    section("AF2 — analyze_timeframe: SHORT сигнал")

    qr = make_quality_result("SHORT", 58.0, "SHORT confirmed")

    with mock.patch("analysis.analyze_both_directions", return_value=qr):
        from analysis import analyze_timeframe
        r = analyze_timeframe(make_small_df())

    assert_analyze_tf_format(r, "AF2")
    check(r["signal"] == "SHORT",    "AF2.signal == SHORT",   f"got {r['signal']!r}")
    check(r["trend"]  == "BEARISH",  "AF2.trend == BEARISH",  f"got {r['trend']!r}")
    assert_approx(r["confidence"], 58.0, "AF2.confidence == 58.0", tol=0.1)
    assert_approx(r["score"], r["confidence"], "AF2.score == confidence")
    check(r["confidence_label"] == "MEDIUM", "AF2.label == MEDIUM",
          f"got {r['confidence_label']!r}")
    print(f"     signal={r['signal']}, trend={r['trend']}, conf={r['confidence']}")


def test_analyze_wait():
    section("AF3 — analyze_timeframe: WAIT сигнал")

    qr = make_quality_result("WAIT", 22.0)

    with mock.patch("analysis.analyze_both_directions", return_value=qr):
        from analysis import analyze_timeframe
        r = analyze_timeframe(make_small_df())

    assert_analyze_tf_format(r, "AF3")
    check(r["signal"] == "WAIT",    "AF3.signal == WAIT")
    check(r["trend"]  == "NEUTRAL", "AF3.trend == NEUTRAL", f"got {r['trend']!r}")
    check(r["confidence_label"] == "LOW", "AF3.label == LOW",
          f"got {r['confidence_label']!r}")
    print(f"     signal={r['signal']}, trend={r['trend']}, conf={r['confidence']}")


def test_analyze_pipeline_exception():
    section("AF4 — analyze_timeframe: исключение pipeline → не падает")

    with mock.patch("analysis.analyze_both_directions",
                    side_effect=RuntimeError("mock pipeline crash")):
        from analysis import analyze_timeframe
        r = analyze_timeframe(make_small_df())

    assert_analyze_tf_format(r, "AF4")
    check(r["signal"] == "WAIT",  "AF4.signal == WAIT (after exception)")
    check(r["trend"]  == "ERROR", "AF4.trend == ERROR", f"got {r['trend']!r}")
    check("mock pipeline crash" in r["reason"] or "Pipeline exception" in r["reason"],
          "AF4.reason mentions exception", f"got {r['reason']!r}")
    print(f"     trend={r['trend']}, reason={r['reason']!r}")


def test_analyze_empty_df():
    section("AF5 — analyze_timeframe: пустой DataFrame")

    from analysis import analyze_timeframe
    empty = pd.DataFrame(columns=["open","high","low","close","volume"])
    r = analyze_timeframe(empty)

    assert_analyze_tf_format(r, "AF5")
    check(r["signal"] == "WAIT", "AF5.signal == WAIT")
    print(f"     reason={r['reason']!r}")


def test_analyze_none_df():
    section("AF6 — analyze_timeframe: df=None")

    from analysis import analyze_timeframe
    r = analyze_timeframe(None)

    assert_analyze_tf_format(r, "AF6")
    check(r["signal"] == "WAIT", "AF6.signal == WAIT")
    check("None" in r["reason"], "AF6.reason mentions None", f"got {r['reason']!r}")
    print(f"     reason={r['reason']!r}")


def test_analyze_incomplete_final():
    section("AF7 — analyze_timeframe: неполный FINAL от pipeline")

    # FINAL без 'label'
    qr_no_label = make_quality_result("LONG", 75.0)
    del qr_no_label["FINAL"]["label"]

    with mock.patch("analysis.analyze_both_directions", return_value=qr_no_label):
        from analysis import analyze_timeframe
        r = analyze_timeframe(make_small_df())

    assert_analyze_tf_format(r, "AF7.no_label")
    check(r["confidence_label"] in VALID_LABELS,
          "AF7.fallback_label computed", f"got {r['confidence_label']!r}")
    print(f"     fallback_label={r['confidence_label']!r} for conf=75.0")

    # FINAL полностью пустой
    qr_empty_final = {"LONG": {}, "SHORT": {}, "FINAL": {}}
    with mock.patch("analysis.analyze_both_directions", return_value=qr_empty_final):
        r2 = analyze_timeframe(make_small_df())

    assert_analyze_tf_format(r2, "AF7.empty_final")
    check(r2["signal"] == "WAIT", "AF7.empty_final.signal == WAIT")
    print(f"     empty_final handled, signal={r2['signal']}")


def test_analyze_score_equals_confidence():
    section("AF8 — analyze_timeframe: score == confidence (0–100)")

    for sig, conf in [("LONG", 85.0), ("SHORT", 50.0), ("WAIT", 0.0)]:
        qr = make_quality_result(sig, conf)
        with mock.patch("analysis.analyze_both_directions", return_value=qr):
            from analysis import analyze_timeframe
            r = analyze_timeframe(make_small_df())

        assert_approx(r["score"], r["confidence"],
                      f"AF8.{sig}.score == confidence ({conf})", tol=0.01)
        assert_in_range(r["score"], 0, 100, f"AF8.{sig}.score in [0,100]")
        assert_in_range(r["confidence"], 0, 100, f"AF8.{sig}.confidence in [0,100]")


def test_analyze_stable_format():
    section("AF9 — analyze_timeframe: формат стабилен при разных входах")

    scenarios = [
        ("LONG",  80.0), ("SHORT", 60.0), ("WAIT", 25.0),
    ]
    for sig, conf in scenarios:
        qr = make_quality_result(sig, conf)
        with mock.patch("analysis.analyze_both_directions", return_value=qr):
            from analysis import analyze_timeframe
            r = analyze_timeframe(make_small_df())
        assert_analyze_tf_format(r, f"AF9.{sig}_{conf}")


# ═════════════════════════════════════════════════════════════════════════════
#  ЧАСТЬ 2: multi_tf.py
# ═════════════════════════════════════════════════════════════════════════════

def _run_multi(tf_map: dict[str, dict],
               error_tfs: set[str] | None = None) -> dict:
    """
    Запускает multi_analysis("BTC/USDT") с подменёнными get_data и analyze_timeframe.

    tf_map : { tf -> analyze_result_dict }
    error_tfs : таймфреймы, для которых get_data поднимает исключение
    """
    from multi_tf import multi_analysis as _multi

    error_tfs = error_tfs or set()

    def fake_get_data(symbol, tf):
        if tf in error_tfs:
            raise ConnectionError(f"mock error: {tf}")
        return make_small_df()

    def fake_analyze(df):
        # Определяем tf по строгому совпадению контекста невозможно через df,
        # поэтому используем side_effect с iterable (по порядку TIMEFRAMES).
        pass  # заменяется ниже

    # Построим ordered список результатов по порядку TIMEFRAMES
    side_effects = []
    for tf in TIMEFRAMES:
        if tf in error_tfs:
            # get_data выбросит исключение — analyze_timeframe не будет вызван
            pass
        else:
            side_effects.append(tf_map.get(tf, make_analyze_result("WAIT", 0.0)))

    with mock.patch("multi_tf.get_data", side_effect=fake_get_data), \
         mock.patch("multi_tf.analyze_timeframe", side_effect=side_effects):
        return _multi("BTC/USDT")


def _directional_score_expected(tf_map: dict[str, dict],
                                 error_tfs: set[str] | None = None) -> float:
    """Вычисляет ожидаемый directional_score по спецификации."""
    error_tfs = error_tfs or set()
    available = {tf: tf_map[tf] for tf in TIMEFRAMES
                 if tf not in error_tfs
                 and tf_map.get(tf, {}).get("trend", "ERROR") != "ERROR"}
    total_base = sum(TIMEFRAME_WEIGHTS[tf] for tf in available)
    if total_base == 0:
        return 0.0
    ds = 0.0
    for tf, r in available.items():
        eff  = TIMEFRAME_WEIGHTS[tf] / total_base
        sig  = r.get("signal", "WAIT")
        conf = float(r.get("confidence", 0.0))
        if sig == "LONG":
            ds += conf * eff
        elif sig == "SHORT":
            ds -= conf * eff
    return ds


# ── MF1-MF3: базовые сценарии ─────────────────────────────────────────────

def test_multi_all_long():
    section("MF1 — multi_analysis: все LONG (conf=80)")

    tf_map = {tf: make_analyze_result("LONG", 80.0) for tf in TIMEFRAMES}
    r = _run_multi(tf_map)

    final = r["FINAL"]
    assert_final_format(final, "MF1")

    check(final["signal"] == "LONG", "MF1.FINAL.signal == LONG",
          f"got {final['signal']!r}")
    check(final["long_timeframes"]  == 5, "MF1.long_tfs == 5")
    check(final["short_timeframes"] == 0, "MF1.short_tfs == 0")

    expected_ds = _directional_score_expected(tf_map)
    assert_approx(final["directional_score"], expected_ds,
                  f"MF1.directional_score ≈ {expected_ds:.2f}", tol=0.01)

    check(final["available_timeframes"] == 5, "MF1.available_tfs == 5")
    print(f"     signal={final['signal']}, ds={final['directional_score']:.2f}")


def test_multi_all_short():
    section("MF2 — multi_analysis: все SHORT (conf=80)")

    tf_map = {tf: make_analyze_result("SHORT", 80.0) for tf in TIMEFRAMES}
    r = _run_multi(tf_map)

    final = r["FINAL"]
    assert_final_format(final, "MF2")

    check(final["signal"] == "SHORT", "MF2.FINAL.signal == SHORT",
          f"got {final['signal']!r}")
    check(final["short_timeframes"] == 5, "MF2.short_tfs == 5")

    expected_ds = _directional_score_expected(tf_map)
    assert_approx(final["directional_score"], expected_ds,
                  f"MF2.directional_score ≈ {expected_ds:.2f}", tol=0.01)
    print(f"     signal={final['signal']}, ds={final['directional_score']:.2f}")


def test_multi_mixed():
    section("MF3 — multi_analysis: смешанные LONG/SHORT/WAIT")

    # 1M LONG, 1w LONG, 1d SHORT, 4h WAIT, 1h WAIT
    tf_map = {
        "1M": make_analyze_result("LONG",  75.0),
        "1w": make_analyze_result("LONG",  65.0),
        "1d": make_analyze_result("SHORT", 50.0),
        "4h": make_analyze_result("WAIT",  0.0),
        "1h": make_analyze_result("WAIT",  0.0),
    }
    r = _run_multi(tf_map)

    final = r["FINAL"]
    assert_final_format(final, "MF3")
    check(final["signal"] in VALID_SIGNALS, "MF3.signal valid")
    check(final["long_timeframes"]  == 2, "MF3.long_tfs == 2")
    check(final["short_timeframes"] == 1, "MF3.short_tfs == 1")
    check(final["wait_timeframes"]  == 2, "MF3.wait_tfs == 2")

    expected_ds = _directional_score_expected(tf_map)
    assert_approx(final["directional_score"], expected_ds,
                  f"MF3.directional_score ≈ {expected_ds:.2f}", tol=0.05)
    print(f"     signal={final['signal']}, ds={final['directional_score']:.2f}, "
          f"L={final['long_timeframes']}, S={final['short_timeframes']}, W={final['wait_timeframes']}")


# ── MF4-MF6: пороги ───────────────────────────────────────────────────────

def test_multi_threshold_long():
    section("MF4 — directional_score >= 40 → LONG (без конфликта HTF)")

    # Все LONG с conf=50 (минимально допустимый активный сигнал).
    # Инвариант: conf < 50 → treated as WAIT; conf=50 проходит.
    # ds = 50 * 1.0 = 50 ≥ 40 → LONG ✓
    tf_map = {tf: make_analyze_result("LONG", 50.0) for tf in TIMEFRAMES}
    r = _run_multi(tf_map)

    final = r["FINAL"]
    ds = final["directional_score"]
    assert_approx(ds, 50.0, "MF4.directional_score == 50.0", tol=0.05)
    check(final["signal"] == "LONG", "MF4.signal == LONG",
          f"ds={ds:.3f}, signal={final['signal']!r}")
    print(f"     ds={ds:.4f} → signal={final['signal']}")


def test_multi_threshold_short():
    section("MF5 — directional_score <= -40 → SHORT (без конфликта HTF)")

    # Аналогично MF4: все SHORT с conf=50 → ds=-50 ≤ -40 → SHORT
    tf_map = {tf: make_analyze_result("SHORT", 50.0) for tf in TIMEFRAMES}
    r = _run_multi(tf_map)

    final = r["FINAL"]
    ds = final["directional_score"]
    assert_approx(ds, -50.0, "MF5.directional_score == -50.0", tol=0.05)
    check(final["signal"] == "SHORT", "MF5.signal == SHORT",
          f"ds={ds:.3f}, signal={final['signal']!r}")
    print(f"     ds={ds:.4f} → signal={final['signal']}")


def test_multi_below_threshold():
    section("MF6 — directional_score < 40 → WAIT")

    # Sub-case A: conf=49 < 50 → инвариант превращает в WAIT → ds=0 → WAIT
    tf_map_inv = {tf: make_analyze_result("LONG", 49.0) for tf in TIMEFRAMES}
    r_inv = _run_multi(tf_map_inv)
    ds_inv = r_inv["FINAL"]["directional_score"]
    assert_approx(ds_inv, 0.0, "MF6.inv.directional_score == 0 (conf<50→WAIT)", tol=0.05)
    check(r_inv["FINAL"]["signal"] == "WAIT",
          "MF6.inv.signal == WAIT (conf<50 → invariant)",
          f"ds={ds_inv:.3f}")

    # Sub-case B: только 1h+4h LONG(conf=50), остальные WAIT
    # ds = 50*(0.08+0.12) = 10.0 < 40 → WAIT  (тест порога агрегации)
    tf_map_partial = {
        "1M": make_analyze_result("WAIT",  0.0),
        "1w": make_analyze_result("WAIT",  0.0),
        "1d": make_analyze_result("WAIT",  0.0),
        "4h": make_analyze_result("LONG", 50.0),
        "1h": make_analyze_result("LONG", 50.0),
    }
    r_p = _run_multi(tf_map_partial)
    ds_p = r_p["FINAL"]["directional_score"]
    # Ожидаем ds = 50*(0.12+0.08)/1.0 = 10.0 (веса нормированы на total_base=1.0)
    assert_in_range(ds_p, 0.0, 39.9, "MF6.partial.directional_score < 40")
    check(r_p["FINAL"]["signal"] == "WAIT",
          "MF6.partial.signal == WAIT (ds < 40)",
          f"ds={ds_p:.3f}")

    # Sub-case C: SHORT зеркально
    tf_map_inv_s = {tf: make_analyze_result("SHORT", 49.0) for tf in TIMEFRAMES}
    r_inv_s = _run_multi(tf_map_inv_s)
    ds_inv_s = r_inv_s["FINAL"]["directional_score"]
    assert_approx(ds_inv_s, 0.0, "MF6.SHORT.inv.directional_score == 0", tol=0.05)
    check(r_inv_s["FINAL"]["signal"] == "WAIT",
          "MF6.SHORT.inv.signal == WAIT (conf<50 → invariant)",
          f"ds={ds_inv_s:.3f}")

    print(f"     inv ds={ds_inv:.3f}, partial ds={ds_p:.3f}, short inv ds={ds_inv_s:.3f} → all WAIT")


# ── MF7-MF9: отсутствующие таймфреймы ────────────────────────────────────

def test_multi_one_missing():
    section("MF7 — один таймфрейм недоступен → перенормировка")

    # 1M выбросит исключение → error
    tf_map = {
        "1M": make_analyze_result("LONG", 80.0),  # не дойдём до него
        "1w": make_analyze_result("LONG", 80.0),
        "1d": make_analyze_result("LONG", 80.0),
        "4h": make_analyze_result("LONG", 80.0),
        "1h": make_analyze_result("LONG", 80.0),
    }
    r = _run_multi(tf_map, error_tfs={"1M"})

    final = r["FINAL"]
    assert_final_format(final, "MF7")
    check(final["available_timeframes"] == 4, "MF7.available_tfs == 4",
          f"got {final['available_timeframes']}")
    check(r["1M"]["trend"] == "ERROR", "MF7.1M.trend == ERROR")

    # Веса оставшихся: 1w=0.25, 1d=0.20, 4h=0.12, 1h=0.08 → sum=0.65
    total_base = 0.25 + 0.20 + 0.12 + 0.08  # 0.65
    comps = final["timeframe_components"]
    eff_sum = sum(comps[tf]["effective_weight"] for tf in TIMEFRAMES
                  if comps[tf]["available"])
    assert_approx(eff_sum, 1.0, "MF7.sum(effective_weight) == 1.0", tol=0.001)

    # 1M effective_weight должен быть 0
    check(comps["1M"]["effective_weight"] == 0.0,
          "MF7.1M.effective_weight == 0.0",
          f"got {comps['1M']['effective_weight']}")
    print(f"     available={final['available_timeframes']}, eff_sum={eff_sum:.4f}")


def test_multi_several_missing():
    section("MF8 — несколько таймфреймов недоступны → перенормировка")

    tf_map = {
        "1M": make_analyze_result("LONG", 70.0),
        "1w": make_analyze_result("LONG", 70.0),
        "1d": make_analyze_result("WAIT", 0.0),
        "4h": make_analyze_result("WAIT", 0.0),
        "1h": make_analyze_result("WAIT", 0.0),
    }
    r = _run_multi(tf_map, error_tfs={"1M", "1w"})

    final = r["FINAL"]
    assert_final_format(final, "MF8")
    check(final["available_timeframes"] == 3, "MF8.available_tfs == 3",
          f"got {final['available_timeframes']}")

    comps   = final["timeframe_components"]
    eff_sum = sum(comps[tf]["effective_weight"] for tf in TIMEFRAMES
                  if comps[tf]["available"])
    assert_approx(eff_sum, 1.0, "MF8.sum(effective_weight) == 1.0", tol=0.001)
    print(f"     available={final['available_timeframes']}, eff_sum={eff_sum:.4f}")


def test_multi_one_exception():
    section("MF9 — исключение одного TF не останавливает остальные")

    # 4h бросает исключение при get_data
    tf_map = {
        "1M": make_analyze_result("LONG", 80.0),
        "1w": make_analyze_result("LONG", 80.0),
        "1d": make_analyze_result("LONG", 80.0),
        "4h": make_analyze_result("LONG", 80.0),  # не достигнем
        "1h": make_analyze_result("LONG", 80.0),
    }
    r = _run_multi(tf_map, error_tfs={"4h"})

    final = r["FINAL"]
    assert_final_format(final, "MF9")
    check(final["available_timeframes"] == 4, "MF9.available_tfs == 4",
          f"got {final['available_timeframes']}")
    check(r["4h"]["trend"] == "ERROR", "MF9.4h.trend == ERROR")
    check(r["1M"]["signal"] == "LONG",  "MF9.1M.signal == LONG  (другие TF работают)")
    print(f"     available={final['available_timeframes']}, signal={final['signal']}")


def test_multi_no_available():
    section("MF10 — нет доступных таймфреймов → WAIT, score=0")

    r = _run_multi({}, error_tfs=set(TIMEFRAMES))

    final = r["FINAL"]
    assert_final_format(final, "MF10")
    check(final["signal"]               == "WAIT", "MF10.signal == WAIT")
    check(final["available_timeframes"] == 0,       "MF10.available_tfs == 0")
    assert_approx(final["confidence"],        0.0, "MF10.confidence == 0",  tol=0.01)
    assert_approx(final["directional_score"], 0.0, "MF10.ds == 0",          tol=0.01)
    check("No available" in final["reason"],
          "MF10.reason mentions 'No available'", f"got {final['reason']!r}")
    print(f"     signal={final['signal']}, conf={final['confidence']}, "
          f"reason={final['reason']!r}")


# ── MF11-MF13: конфликт HTF ───────────────────────────────────────────────

def test_htf_conflict_long_short():
    section("MF11 — HTF конфликт: 1M=LONG, 1w=SHORT → WAIT")

    tf_map = {
        "1M": make_analyze_result("LONG",  80.0),
        "1w": make_analyze_result("SHORT", 80.0),
        "1d": make_analyze_result("LONG",  80.0),
        "4h": make_analyze_result("LONG",  80.0),
        "1h": make_analyze_result("LONG",  80.0),
    }
    r = _run_multi(tf_map)

    final = r["FINAL"]
    assert_final_format(final, "MF11")
    check(final["signal"] == "WAIT",
          "MF11.signal == WAIT (1M/1w conflict)", f"got {final['signal']!r}")
    check("конфликт" in final["reason"].lower() or "conflict" in final["reason"].lower()
          or "1M" in final["reason"],
          "MF11.reason mentions conflict", f"got {final['reason']!r}")
    print(f"     signal={final['signal']}, reason={final['reason']!r}")


def test_htf_conflict_short_long():
    section("MF12 — HTF конфликт: 1M=SHORT, 1w=LONG → WAIT")

    tf_map = {
        "1M": make_analyze_result("SHORT", 75.0),
        "1w": make_analyze_result("LONG",  75.0),
        "1d": make_analyze_result("SHORT", 60.0),
        "4h": make_analyze_result("SHORT", 60.0),
        "1h": make_analyze_result("SHORT", 60.0),
    }
    r = _run_multi(tf_map)

    final = r["FINAL"]
    assert_final_format(final, "MF12")
    check(final["signal"] == "WAIT",
          "MF12.signal == WAIT (1M/1w conflict)", f"got {final['signal']!r}")
    print(f"     signal={final['signal']}, reason={final['reason']!r}")


def test_htf_no_conflict_wait():
    section("MF13 — WAIT на одном HTF не вызывает конфликт")

    # 1M=LONG, 1w=WAIT → не конфликт
    tf_map_a = {
        "1M": make_analyze_result("LONG", 80.0),
        "1w": make_analyze_result("WAIT", 0.0),
        "1d": make_analyze_result("LONG", 80.0),
        "4h": make_analyze_result("LONG", 80.0),
        "1h": make_analyze_result("LONG", 80.0),
    }
    r_a = _run_multi(tf_map_a)
    # ds ожидается высоким — LONG
    check(r_a["FINAL"]["signal"] != "WAIT" or r_a["FINAL"]["directional_score"] < 40,
          "MF13a.1M_LONG_1w_WAIT.no_conflict",
          f"signal={r_a['FINAL']['signal']!r}, ds={r_a['FINAL']['directional_score']:.2f}")
    # Чище: просто убеждаемся, что reason НЕ говорит о конфликте
    check("конфликт" not in r_a["FINAL"]["reason"].lower()
          and "conflict" not in r_a["FINAL"]["reason"].lower(),
          "MF13a.reason does NOT mention conflict",
          f"got {r_a['FINAL']['reason']!r}")

    # 1M=WAIT, 1w=SHORT → не конфликт
    tf_map_b = {
        "1M": make_analyze_result("WAIT",  0.0),
        "1w": make_analyze_result("SHORT", 80.0),
        "1d": make_analyze_result("SHORT", 80.0),
        "4h": make_analyze_result("SHORT", 80.0),
        "1h": make_analyze_result("SHORT", 80.0),
    }
    r_b = _run_multi(tf_map_b)
    check("конфликт" not in r_b["FINAL"]["reason"].lower()
          and "conflict" not in r_b["FINAL"]["reason"].lower(),
          "MF13b.1M_WAIT_1w_SHORT.no_conflict",
          f"got {r_b['FINAL']['reason']!r}")
    print(f"     a.signal={r_a['FINAL']['signal']}, b.signal={r_b['FINAL']['signal']}")


# ── MF14-MF18: числовые инварианты ───────────────────────────────────────

def test_effective_weight_sum():
    section("MF14 — sum(effective_weight) доступных TF == 1.0")

    scenarios = [
        ("all_long",      {tf: make_analyze_result("LONG", 70.0) for tf in TIMEFRAMES}, set()),
        ("one_missing",   {tf: make_analyze_result("LONG", 70.0) for tf in TIMEFRAMES}, {"1M"}),
        ("two_missing",   {tf: make_analyze_result("LONG", 70.0) for tf in TIMEFRAMES}, {"1M","1w"}),
        ("three_missing", {tf: make_analyze_result("LONG", 70.0) for tf in TIMEFRAMES}, {"1M","1w","1d"}),
    ]

    for tag, tf_map, err_tfs in scenarios:
        r = _run_multi(tf_map, error_tfs=err_tfs)
        comps = r["FINAL"]["timeframe_components"]
        eff_sum = sum(comps[tf]["effective_weight"] for tf in TIMEFRAMES
                      if comps[tf]["available"])
        n_avail = sum(1 for tf in TIMEFRAMES if comps[tf]["available"])
        if n_avail > 0:
            assert_approx(eff_sum, 1.0,
                          f"MF14.{tag}.eff_sum == 1.0 (avail={n_avail})", tol=0.001)
        else:
            check(True, f"MF14.{tag}.no_available (skip)")


def test_contribution_formula():
    section("MF15 — contribution = signal × confidence × effective_weight")

    tf_map = {
        "1M": make_analyze_result("LONG",  80.0),
        "1w": make_analyze_result("SHORT", 60.0),
        "1d": make_analyze_result("WAIT",  30.0),
        "4h": make_analyze_result("LONG",  50.0),
        "1h": make_analyze_result("WAIT",  0.0),
    }
    r     = _run_multi(tf_map)
    comps = r["FINAL"]["timeframe_components"]

    # Рассчитываем ожидаемые effective_weight (total_base = 1.0, все доступны)
    total_base = sum(TIMEFRAME_WEIGHTS.values())  # 1.0
    for tf, res in tf_map.items():
        sig  = res["signal"]
        conf = float(res["confidence"])
        eff  = TIMEFRAME_WEIGHTS[tf] / total_base
        if sig == "LONG":
            expected_contrib = +conf * eff
        elif sig == "SHORT":
            expected_contrib = -conf * eff
        else:
            expected_contrib = 0.0

        actual = float(comps[tf]["contribution"])
        assert_approx(actual, expected_contrib,
                      f"MF15.{tf}.contribution ≈ {expected_contrib:.4f}", tol=0.001)


def test_no_nan_inf_multi():
    section("MF16 — нет NaN/inf в числовых полях multi_analysis")

    tf_map = {
        "1M": make_analyze_result("LONG",  72.0),
        "1w": make_analyze_result("SHORT", 55.0),
        "1d": make_analyze_result("WAIT",  0.0),
        "4h": make_analyze_result("LONG",  40.0),
        "1h": make_analyze_result("WAIT",  0.0),
    }
    r     = _run_multi(tf_map)
    final = r["FINAL"]

    for key in ("score","confidence","directional_score"):
        assert_no_nan_inf(float(final[key]), f"MF16.FINAL.{key} not NaN/Inf")

    comps = final["timeframe_components"]
    for tf in TIMEFRAMES:
        c = comps[tf]
        assert_no_nan_inf(float(c["effective_weight"]), f"MF16.{tf}.eff_w not NaN/Inf")
        assert_no_nan_inf(float(c["contribution"]),     f"MF16.{tf}.contrib not NaN/Inf")
        if c["confidence"] is not None:
            assert_no_nan_inf(float(c["confidence"]),   f"MF16.{tf}.conf not NaN/Inf")


def test_full_final_format():
    section("MF17 — полный формат FINAL при всех сценариях")

    scenarios = [
        ("all_long",  {tf: make_analyze_result("LONG",  80.0) for tf in TIMEFRAMES}, set()),
        ("all_short", {tf: make_analyze_result("SHORT", 80.0) for tf in TIMEFRAMES}, set()),
        ("all_wait",  {tf: make_analyze_result("WAIT",  0.0)  for tf in TIMEFRAMES}, set()),
        ("one_miss",  {tf: make_analyze_result("LONG",  70.0) for tf in TIMEFRAMES}, {"1d"}),
        ("no_avail",  {},                                                              set(TIMEFRAMES)),
    ]

    for tag, tf_map, err_tfs in scenarios:
        r     = _run_multi(tf_map, error_tfs=err_tfs)
        final = r["FINAL"]
        assert_final_format(final, f"MF17.{tag}")

        comps = final.get("timeframe_components", {})
        for tf in TIMEFRAMES:
            if tf in comps:
                assert_component_format(comps[tf], tf, f"MF17.{tag}")


def test_directional_score_range():
    section("MF18 — directional_score в диапазоне [-100, +100]")

    for sig, conf in [("LONG", 100.0), ("SHORT", 100.0), ("WAIT", 0.0)]:
        tf_map = {tf: make_analyze_result(sig, conf) for tf in TIMEFRAMES}
        r  = _run_multi(tf_map)
        ds = r["FINAL"]["directional_score"]
        assert_in_range(ds, -100.0, 100.0, f"MF18.{sig}_{conf}.ds in [-100,100]")

    print(f"     directional_score bounded correctly")


def test_score_equals_confidence_multi():
    section("MF19 — FINAL.score == FINAL.confidence")

    scenarios = [
        {tf: make_analyze_result("LONG",  80.0) for tf in TIMEFRAMES},
        {tf: make_analyze_result("SHORT", 60.0) for tf in TIMEFRAMES},
        {tf: make_analyze_result("WAIT",  0.0)  for tf in TIMEFRAMES},
    ]
    for i, tf_map in enumerate(scenarios):
        r     = _run_multi(tf_map)
        final = r["FINAL"]
        assert_approx(final["score"], final["confidence"],
                      f"MF19.scenario_{i}.score == confidence", tol=0.001)
        assert_in_range(final["score"], 0, 100, f"MF19.scenario_{i}.score in [0,100]")


# ═════════════════════════════════════════════════════════════════════════════
#  ИТОГОВЫЙ ОТЧЁТ
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("  ANALYSIS INTEGRATION TESTS")
    print("═" * 60)

    suites = [
        # analysis.py
        ("AF1 — analyze_timeframe LONG",            test_analyze_long),
        ("AF2 — analyze_timeframe SHORT",           test_analyze_short),
        ("AF3 — analyze_timeframe WAIT",            test_analyze_wait),
        ("AF4 — pipeline exception",                test_analyze_pipeline_exception),
        ("AF5 — empty DataFrame",                   test_analyze_empty_df),
        ("AF6 — df=None",                           test_analyze_none_df),
        ("AF7 — incomplete FINAL",                  test_analyze_incomplete_final),
        ("AF8 — score == confidence",               test_analyze_score_equals_confidence),
        ("AF9 — stable format",                     test_analyze_stable_format),
        # multi_tf.py
        ("MF1 — all LONG",                          test_multi_all_long),
        ("MF2 — all SHORT",                         test_multi_all_short),
        ("MF3 — mixed L/S/W",                       test_multi_mixed),
        ("MF4 — threshold >= 40 LONG",              test_multi_threshold_long),
        ("MF5 — threshold <= -40 SHORT",            test_multi_threshold_short),
        ("MF6 — below threshold → WAIT",            test_multi_below_threshold),
        ("MF7 — one TF missing renorm",             test_multi_one_missing),
        ("MF8 — several TFs missing renorm",        test_multi_several_missing),
        ("MF9 — one TF exception no stop",          test_multi_one_exception),
        ("MF10 — no available TFs",                 test_multi_no_available),
        ("MF11 — HTF conflict L/S → WAIT",          test_htf_conflict_long_short),
        ("MF12 — HTF conflict S/L → WAIT",          test_htf_conflict_short_long),
        ("MF13 — WAIT HTF no conflict",             test_htf_no_conflict_wait),
        ("MF14 — eff_weight sum == 1.0",            test_effective_weight_sum),
        ("MF15 — contribution formula",             test_contribution_formula),
        ("MF16 — no NaN/inf",                       test_no_nan_inf_multi),
        ("MF17 — full FINAL format",                test_full_final_format),
        ("MF18 — directional_score [-100,+100]",    test_directional_score_range),
        ("MF19 — FINAL.score == confidence",        test_score_equals_confidence_multi),
    ]

    suite_results: list[tuple[str, bool]] = []
    for suite_name, func in suites:
        before_fail = len(_failed)
        try:
            func()
            passed_suite = len(_failed) == before_fail
        except Exception:
            print(f"\n  !!! EXCEPTION in {suite_name} !!!")
            traceback.print_exc()
            passed_suite = False
            _failed.append(f"{suite_name} (EXCEPTION)")
        suite_results.append((suite_name, passed_suite))

    total  = len(_passed) + len(_failed)
    n_pass = len(_passed)

    print("\n" + "═" * 60)
    print("  ANALYSIS INTEGRATION REPORT")
    print("═" * 60)
    for suite_name, suite_ok in suite_results:
        mark = "✓" if suite_ok else "✗"
        print(f"  {mark}  {suite_name}")

    if _failed:
        print("\n  Failed checks:")
        for f in _failed:
            print(f"    ✗  {f}")

    print(f"\n  Analysis integration tests: {n_pass}/{total}")
    status = "PASSED" if not _failed else "FAILED"
    print(f"\n  ANALYSIS INTEGRATION STATUS: {status}")
    print("═" * 60 + "\n")
    sys.exit(0 if not _failed else 1)


if __name__ == "__main__":
    main()
