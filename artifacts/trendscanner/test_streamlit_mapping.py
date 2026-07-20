"""
test_streamlit_mapping.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Тесты чистых helper-функций из ui_helpers.py.

НЕ импортирует Streamlit.
НЕ ослабляет test_v04.py / test_quality_pipeline.py / test_analysis_integration.py.

Запуск: python test_streamlit_mapping.py
"""

import sys
import math
import traceback

from ui_helpers import (
    normalize_timeframe_result,
    format_directional_score,
    extract_quality_summary,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Инструменты
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


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


VALID_SIGNALS = {"LONG", "SHORT", "WAIT", "ERROR"}
VALID_LABELS  = {"LOW", "MEDIUM", "HIGH", "VERY HIGH"}


# ─────────────────────────────────────────────────────────────────────────────
#  Фабрики данных
# ─────────────────────────────────────────────────────────────────────────────

def _make_confidence_components(
    tq_score=72.0, vq_score=85.0, bq_score=60.0,
    tq_avail=True, vq_avail=True, bq_avail=True
) -> dict:
    return {
        "trend_quality": {
            "score":            tq_score,
            "base_weight":      0.50,
            "effective_weight": 0.50,
            "contribution":     tq_score * 0.50,
            "available":        tq_avail,
            "reason":           "",
        },
        "volume_quality": {
            "score":            vq_score,
            "base_weight":      0.20,
            "effective_weight": 0.20,
            "contribution":     vq_score * 0.20,
            "available":        vq_avail,
            "reason":           "",
        },
        "breakout_quality": {
            "score":            bq_score,
            "base_weight":      0.30,
            "effective_weight": 0.30,
            "contribution":     bq_score * 0.30,
            "available":        bq_avail,
            "reason":           "",
        },
    }


def _make_direction_data(
    direction="LONG", signal="LONG", confirmed=True,
    tq_score=72.0, vq_score=85.0, bq_score=60.0,
    confidence=68.0, label="MEDIUM", reason=""
) -> dict:
    return {
        "direction":  direction,
        "signal":     signal,
        "confirmed":  confirmed,
        "line":       None,
        "trend_quality":    {"trend_quality_score": tq_score, "touches_score": 80},
        "volume_quality":   {"volume_score": vq_score, "volume_ratio": 1.5},
        "breakout_quality": {
            "breakout_score": bq_score,
            "confirmed":      confirmed,
            "components":     {"cross": 1.0, "close_distance": 0.8},
            "reason":         "",
        },
        "confidence": {
            "confidence": confidence,
            "label":      label,
            "components": _make_confidence_components(tq_score, vq_score, bq_score),
            "available_components": 3,
            "reason":     "",
        },
        "reason": reason or (signal if signal != "WAIT" else "WAIT: no breakout"),
    }


def _make_full_tf_result(
    signal="LONG", confidence=68.0, trend="BULLISH", label="MEDIUM"
) -> dict:
    direction_data = {
        "LONG":  _make_direction_data("LONG",  signal if signal == "LONG"  else "WAIT",
                                      signal == "LONG",  72.0, 85.0, 60.0, confidence),
        "SHORT": _make_direction_data("SHORT", signal if signal == "SHORT" else "WAIT",
                                      signal == "SHORT", 45.0, 50.0, 30.0, 20.0),
        "FINAL": {
            "signal":     signal,
            "confidence": confidence,
            "label":      label,
            "reason":     signal,
        },
    }
    return {
        "trend":            trend,
        "signal":           signal,
        "score":            confidence,
        "confidence":       confidence,
        "confidence_label": label,
        "reason":           signal,
        "quality":          direction_data,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  A. normalize_timeframe_result
# ═════════════════════════════════════════════════════════════════════════════

def test_normalize_new_dict_long():
    section("N1 — normalize: новый dict-формат LONG")

    r = normalize_timeframe_result(_make_full_tf_result("LONG", 72.5, "BULLISH", "HIGH"))

    check(r["signal"]           == "LONG",    "N1.signal == LONG")
    check(r["trend"]            == "BULLISH", "N1.trend == BULLISH")
    check(abs(r["confidence"] - 72.5) < 0.01,"N1.confidence == 72.5", f"got {r['confidence']}")
    check(r["confidence_label"] == "HIGH",    "N1.label == HIGH")
    check(isinstance(r["quality"], dict),     "N1.quality is dict")
    check(isinstance(r["reason"], str),       "N1.reason is str")


def test_normalize_new_dict_short():
    section("N2 — normalize: новый dict-формат SHORT")

    r = normalize_timeframe_result(_make_full_tf_result("SHORT", 55.0, "BEARISH", "MEDIUM"))

    check(r["signal"]           == "SHORT",   "N2.signal == SHORT")
    check(r["trend"]            == "BEARISH", "N2.trend == BEARISH")
    check(abs(r["confidence"] - 55.0) < 0.01,"N2.confidence == 55.0", f"got {r['confidence']}")
    check(r["confidence_label"] == "MEDIUM",  "N2.label == MEDIUM")


def test_normalize_new_dict_wait():
    section("N3 — normalize: новый dict-формат WAIT")

    r = normalize_timeframe_result(_make_full_tf_result("WAIT", 18.0, "NEUTRAL", "LOW"))

    check(r["signal"]           == "WAIT",    "N3.signal == WAIT")
    check(r["trend"]            == "NEUTRAL", "N3.trend == NEUTRAL")
    check(r["confidence_label"] == "LOW",     "N3.label == LOW")


def test_normalize_old_string_long():
    section("N4 — normalize: старый строковый формат LONG")

    r = normalize_timeframe_result("LONG")

    check(r["signal"]           == "LONG",    "N4.signal == LONG")
    check(r["trend"]            == "BULLISH", "N4.trend == BULLISH")
    check(r["confidence"]       == 0.0,       "N4.confidence == 0.0")
    check(r["confidence_label"] == "LOW",     "N4.label == LOW")
    check(r["quality"]          == {},        "N4.quality == {}")


def test_normalize_old_string_short():
    section("N5 — normalize: старый строковый формат SHORT")

    for s in ("SHORT", "short", "Short"):
        r = normalize_timeframe_result(s)
        check(r["signal"] == "SHORT", f"N5.{s!r}.signal == SHORT")


def test_normalize_old_string_wait():
    section("N6 — normalize: WAIT / ERROR строки")

    for s, expected_sig in [("WAIT", "WAIT"), ("ERROR", "ERROR")]:
        r = normalize_timeframe_result(s)
        check(r["signal"] == expected_sig, f"N6.{s!r}.signal == {expected_sig}")


def test_normalize_error_dict():
    section("N7 — normalize: dict с trend=ERROR")

    r = normalize_timeframe_result({
        "trend": "ERROR", "signal": "WAIT",
        "score": 0.0, "confidence": 0.0,
        "confidence_label": "LOW", "reason": "Connection error",
        "quality": {},
    })

    check(r["signal"] == "WAIT",  "N7.signal == WAIT")
    check(r["trend"]  == "ERROR", "N7.trend == ERROR")


def test_normalize_none():
    section("N8 — normalize: None → WAIT")

    r = normalize_timeframe_result(None)

    check(r["signal"]           == "WAIT", "N8.signal == WAIT")
    check(r["confidence"]       == 0.0,    "N8.confidence == 0.0")
    check(r["confidence_label"] == "LOW",  "N8.label == LOW")
    check(r["quality"]          == {},     "N8.quality == {}")
    check("No data" in r["reason"],        "N8.reason mentions 'No data'")


def test_normalize_missing_confidence():
    section("N9 — normalize: dict без ключа confidence")

    r = normalize_timeframe_result({
        "signal": "LONG",
        "trend":  "BULLISH",
        # нет confidence / confidence_label
    })

    check(r["signal"]     == "LONG",   "N9.signal == LONG")
    check(r["confidence"] == 0.0,      "N9.confidence defaults to 0.0")
    check(r["confidence_label"] in VALID_LABELS, "N9.label valid")


def test_normalize_unknown_signal():
    section("N10 — normalize: неизвестный signal в dict → WAIT")

    r = normalize_timeframe_result({"signal": "SIDEWAYS", "trend": "NEUTRAL"})

    check(r["signal"] == "WAIT", "N10.unknown_signal → WAIT",
          f"got {r['signal']!r}")


def test_normalize_nan_inf_confidence():
    section("N11 — normalize: NaN/inf confidence → 0.0")

    for val, tag in [(float("nan"), "NaN"), (float("inf"), "inf"), (float("-inf"), "-inf")]:
        r = normalize_timeframe_result({"signal": "LONG", "confidence": val})
        check(math.isfinite(r["confidence"]),
              f"N11.{tag}.confidence is finite", f"got {r['confidence']}")
        check(r["confidence"] == 0.0, f"N11.{tag}.confidence == 0.0")


def test_normalize_confidence_clamped():
    section("N12 — normalize: confidence вне [0,100] → зажимается")

    r_high = normalize_timeframe_result({"signal": "LONG", "confidence": 150.0})
    check(r_high["confidence"] == 100.0, "N12.over_100 → 100.0",
          f"got {r_high['confidence']}")

    r_low = normalize_timeframe_result({"signal": "LONG", "confidence": -10.0})
    check(r_low["confidence"] == 0.0, "N12.below_0 → 0.0",
          f"got {r_low['confidence']}")


def test_normalize_invalid_label():
    section("N13 — normalize: невалидный label → LOW")

    for bad_label in ("EXTREME", "", None, 42):
        r = normalize_timeframe_result({
            "signal": "LONG", "confidence": 50.0, "confidence_label": bad_label
        })
        check(r["confidence_label"] == "LOW",
              f"N13.{bad_label!r} → LOW", f"got {r['confidence_label']!r}")


def test_normalize_returns_all_keys():
    section("N14 — normalize: всегда возвращает все 6 ключей")

    expected = {"signal", "trend", "confidence", "confidence_label", "reason", "quality"}

    for val in [
        "LONG", "SHORT", "WAIT", None,
        {}, {"signal": "LONG"}, 42,
        _make_full_tf_result("LONG", 60.0),
    ]:
        r   = normalize_timeframe_result(val)
        got = set(r.keys())
        check(expected == got, f"N14.{type(val).__name__}.keys complete",
              f"missing={expected - got}, extra={got - expected}")


# ═════════════════════════════════════════════════════════════════════════════
#  B. format_directional_score
# ═════════════════════════════════════════════════════════════════════════════

def test_fmt_ds_positive():
    section("DS1 — format_directional_score: положительные значения")

    cases = [(42.5, "+42.5"), (100.0, "+100.0"), (0.1, "+0.1"), (40.0, "+40.0")]
    for val, expected in cases:
        got = format_directional_score(val)
        check(got == expected, f"DS1.{val} → {expected!r}", f"got {got!r}")


def test_fmt_ds_negative():
    section("DS2 — format_directional_score: отрицательные значения")

    cases = [(-61.2, "-61.2"), (-100.0, "-100.0"), (-0.1, "-0.1"), (-40.0, "-40.0")]
    for val, expected in cases:
        got = format_directional_score(val)
        check(got == expected, f"DS2.{val} → {expected!r}", f"got {got!r}")


def test_fmt_ds_zero():
    section("DS3 — format_directional_score: ноль")

    for val in (0, 0.0, -0.0):
        got = format_directional_score(val)
        check(got == "0.0", f"DS3.{val!r} → '0.0'", f"got {got!r}")


def test_fmt_ds_nan_inf():
    section("DS4 — format_directional_score: NaN/inf → '0.0'")

    for val, tag in [
        (float("nan"), "NaN"), (float("inf"), "inf"),
        (float("-inf"), "-inf"),
    ]:
        got = format_directional_score(val)
        check(got == "0.0", f"DS4.{tag} → '0.0'", f"got {got!r}")


def test_fmt_ds_non_numeric():
    section("DS5 — format_directional_score: не число → '0.0'")

    for val in (None, "bad", [], {}):
        got = format_directional_score(val)
        check(got == "0.0", f"DS5.{val!r} → '0.0'", f"got {got!r}")


def test_fmt_ds_sign_always_explicit():
    section("DS6 — format_directional_score: знак всегда явный")

    pos = format_directional_score(15.3)
    neg = format_directional_score(-15.3)
    check(pos.startswith("+"), "DS6.positive starts with '+'", f"got {pos!r}")
    check(neg.startswith("-"), "DS6.negative starts with '-'", f"got {neg!r}")


# ═════════════════════════════════════════════════════════════════════════════
#  C. extract_quality_summary
# ═════════════════════════════════════════════════════════════════════════════

EXPECTED_SUMMARY_KEYS = {
    "direction", "signal", "confirmed", "confidence", "confidence_label",
    "reason", "trend_quality_score", "volume_quality_score",
    "breakout_quality_score", "breakout_confirmed", "components",
}
EXPECTED_COMP_KEYS = {"trend_quality", "volume_quality", "breakout_quality"}
EXPECTED_COMP_FIELD_KEYS = {"score", "effective_weight", "contribution", "available"}


def _assert_summary_format(qs: dict, tag: str) -> None:
    got = set(qs.keys())
    check(EXPECTED_SUMMARY_KEYS == got,
          f"{tag}.keys complete",
          f"missing={EXPECTED_SUMMARY_KEYS - got}, extra={got - EXPECTED_SUMMARY_KEYS}")

    check(isinstance(qs["components"], dict), f"{tag}.components is dict")
    comps_got = set(qs["components"].keys())
    check(EXPECTED_COMP_KEYS == comps_got,
          f"{tag}.component_keys",
          f"missing={EXPECTED_COMP_KEYS - comps_got}")

    for ck in EXPECTED_COMP_KEYS:
        c    = qs["components"][ck]
        fgot = set(c.keys())
        check(EXPECTED_COMP_FIELD_KEYS == fgot,
              f"{tag}.{ck}.fields complete",
              f"missing={EXPECTED_COMP_FIELD_KEYS - fgot}")
        check(isinstance(c["available"], bool), f"{tag}.{ck}.available is bool")


def test_quality_long():
    section("Q1 — extract_quality_summary: LONG с полными данными")

    tf_r = _make_full_tf_result("LONG", 68.0)
    qs   = extract_quality_summary(tf_r, "LONG")

    _assert_summary_format(qs, "Q1")
    check(qs["direction"] == "LONG",   "Q1.direction == LONG")
    check(qs["signal"]    == "LONG",   "Q1.signal == LONG",  f"got {qs['signal']!r}")
    check(qs["confirmed"] is True,     "Q1.confirmed == True")
    check(qs["breakout_confirmed"] is True, "Q1.bq_confirmed == True")

    # Scores должны быть числами (не N/A)
    for key in ("trend_quality_score", "volume_quality_score", "breakout_quality_score"):
        check(qs[key] != "N/A", f"Q1.{key} != N/A", f"got {qs[key]!r}")

    print(f"     tq={qs['trend_quality_score']}, vq={qs['volume_quality_score']}, "
          f"bq={qs['breakout_quality_score']}, conf={qs['confidence']}")


def test_quality_short():
    section("Q2 — extract_quality_summary: SHORT с полными данными")

    tf_r = _make_full_tf_result("SHORT", 62.0)
    qs   = extract_quality_summary(tf_r, "SHORT")

    _assert_summary_format(qs, "Q2")
    check(qs["direction"] == "SHORT",  "Q2.direction == SHORT")
    check(qs["signal"]    == "SHORT",  "Q2.signal == SHORT",  f"got {qs['signal']!r}")
    check(qs["confirmed"] is True,     "Q2.confirmed == True")

    print(f"     tq={qs['trend_quality_score']}, bq={qs['breakout_quality_score']}, "
          f"conf={qs['confidence']}")


def test_quality_wait():
    section("Q3 — extract_quality_summary: WAIT (нет пробоя)")

    tf_r = _make_full_tf_result("WAIT", 15.0)
    qs   = extract_quality_summary(tf_r, "LONG")

    _assert_summary_format(qs, "Q3")
    check(qs["signal"]    == "WAIT",  "Q3.signal == WAIT")
    check(qs["confirmed"] is False,   "Q3.confirmed == False")
    check(qs["breakout_confirmed"] is False, "Q3.bq_confirmed == False")


def test_quality_missing_quality_key():
    section("Q4 — extract_quality_summary: нет ключа quality → N/A")

    tf_no_quality = {"signal": "LONG", "confidence": 70.0, "trend": "BULLISH"}
    qs = extract_quality_summary(tf_no_quality, "LONG")

    _assert_summary_format(qs, "Q4")
    check(qs["trend_quality_score"]    == "N/A", "Q4.tq_score == N/A")
    check(qs["volume_quality_score"]   == "N/A", "Q4.vq_score == N/A")
    check(qs["breakout_quality_score"] == "N/A", "Q4.bq_score == N/A")
    check(qs["confidence"]             == "N/A", "Q4.confidence == N/A")
    print(f"     All scores N/A when quality missing")


def test_quality_none_input():
    section("Q5 — extract_quality_summary: None → N/A, не падает")

    for val in (None, "LONG", 42, []):
        qs = extract_quality_summary(val, "LONG")
        _assert_summary_format(qs, f"Q5.{type(val).__name__}")
        check(qs["trend_quality_score"] == "N/A", f"Q5.{type(val).__name__}.tq == N/A")


def test_quality_missing_components():
    section("Q6 — extract_quality_summary: components отсутствуют в confidence")

    # Нет поля components в confidence
    tf_r = _make_full_tf_result("LONG", 65.0)
    # Убираем components из confidence
    tf_r["quality"]["LONG"]["confidence"].pop("components", None)

    qs = extract_quality_summary(tf_r, "LONG")
    _assert_summary_format(qs, "Q6")

    # Scores берутся из trend_quality / volume_quality / breakout_quality напрямую
    check(qs["trend_quality_score"]    != "N/A", "Q6.tq_score from tq dict",
          f"got {qs['trend_quality_score']!r}")
    check(qs["volume_quality_score"]   != "N/A", "Q6.vq_score from vq dict")
    check(qs["breakout_quality_score"] != "N/A", "Q6.bq_score from bq dict")

    # Но component effective_weight/contribution будут N/A
    for comp_key in ("trend_quality", "volume_quality", "breakout_quality"):
        c = qs["components"][comp_key]
        check(c["effective_weight"] == "N/A",
              f"Q6.{comp_key}.eff_weight == N/A (no components)", f"got {c['effective_weight']!r}")


def test_quality_partial_components():
    section("Q7 — extract_quality_summary: частичные компоненты (один available=False)")

    tf_r = _make_full_tf_result("LONG", 60.0)
    # volume_quality недоступен
    tf_r["quality"]["LONG"]["confidence"]["components"]["volume_quality"]["available"] = False

    qs = extract_quality_summary(tf_r, "LONG")
    _assert_summary_format(qs, "Q7")

    vq_comp = qs["components"]["volume_quality"]
    check(vq_comp["available"] is False,  "Q7.vq.available == False")
    check(vq_comp["score"]     == "N/A",  "Q7.vq.score == N/A when not available",
          f"got {vq_comp['score']!r}")

    # tq и bq всё ещё доступны
    check(qs["components"]["trend_quality"]["available"] is True,
          "Q7.tq.available == True")


def test_quality_na_not_zero():
    section("Q8 — extract_quality_summary: N/A, а не 0 для отсутствующих значений")

    # Полностью пустой quality dict
    tf_r = {"signal": "WAIT", "quality": {"LONG": {}, "SHORT": {}, "FINAL": {}}}
    qs   = extract_quality_summary(tf_r, "LONG")

    _assert_summary_format(qs, "Q8")
    for key in ("trend_quality_score", "volume_quality_score", "breakout_quality_score"):
        check(qs[key] == "N/A", f"Q8.{key} == N/A (not '0')",
              f"got {qs[key]!r}")
    check(qs["confidence"] == "N/A", "Q8.confidence == N/A")


def test_quality_nan_inf_scores():
    section("Q9 — extract_quality_summary: NaN/inf в score → N/A")

    tf_r = _make_full_tf_result("LONG", 65.0)
    tf_r["quality"]["LONG"]["trend_quality"]["trend_quality_score"] = float("nan")
    tf_r["quality"]["LONG"]["volume_quality"]["volume_score"]       = float("inf")

    qs = extract_quality_summary(tf_r, "LONG")
    _assert_summary_format(qs, "Q9")

    check(qs["trend_quality_score"]  == "N/A", "Q9.tq_score NaN → N/A",
          f"got {qs['trend_quality_score']!r}")
    check(qs["volume_quality_score"] == "N/A", "Q9.vq_score inf → N/A",
          f"got {qs['volume_quality_score']!r}")


def test_quality_wrong_direction():
    section("Q10 — extract_quality_summary: неверный direction → fallback LONG")

    tf_r = _make_full_tf_result("LONG", 65.0)

    for bad in ("UP", "", "buy", 42, None):
        qs = extract_quality_summary(tf_r, bad)
        check(qs["direction"] == "LONG", f"Q10.{bad!r} → direction=LONG",
              f"got {qs['direction']!r}")
        _assert_summary_format(qs, f"Q10.{bad!r}")


def test_quality_both_directions_independent():
    section("Q11 — LONG и SHORT суммари независимы")

    tf_r = _make_full_tf_result("LONG", 68.0)
    qs_l = extract_quality_summary(tf_r, "LONG")
    qs_s = extract_quality_summary(tf_r, "SHORT")

    check(qs_l["direction"] == "LONG",  "Q11.LONG.direction == LONG")
    check(qs_s["direction"] == "SHORT", "Q11.SHORT.direction == SHORT")

    # Signals не должны быть одинаковыми (LONG confirmed, SHORT не confirmed)
    check(qs_l["confirmed"] is True,  "Q11.LONG.confirmed == True")
    check(qs_s["confirmed"] is False, "Q11.SHORT.confirmed == False",
          f"SHORT signal={qs_s['signal']!r}")


def test_quality_component_weight_format():
    section("Q12 — компоненты: effective_weight форматируется как '%'")

    tf_r = _make_full_tf_result("LONG", 70.0)
    qs   = extract_quality_summary(tf_r, "LONG")

    for comp_key in ("trend_quality", "volume_quality", "breakout_quality"):
        c = qs["components"][comp_key]
        ew = c["effective_weight"]
        if ew != "N/A":
            check(ew.endswith("%"), f"Q12.{comp_key}.eff_weight ends with '%'",
                  f"got {ew!r}")
            pct = float(ew.rstrip("%"))
            check(0 <= pct <= 100, f"Q12.{comp_key}.eff_weight value in [0,100]",
                  f"got {pct}")


# ═════════════════════════════════════════════════════════════════════════════
#  D. Интеграция helpers
# ═════════════════════════════════════════════════════════════════════════════

def test_pipeline_through_normalize():
    section("D1 — normalize → extract_quality_summary: полная цепочка")

    full_tf = _make_full_tf_result("LONG", 74.0, "BULLISH", "HIGH")
    norm    = normalize_timeframe_result(full_tf)
    qs      = extract_quality_summary(norm, "LONG")

    check(norm["signal"]   == "LONG",   "D1.norm.signal == LONG")
    check(qs["confirmed"]  is True,     "D1.qs.confirmed == True")
    check(qs["confidence"] != "N/A",    "D1.qs.confidence != N/A")
    print(f"     norm.conf={norm['confidence']}, qs.conf={qs['confidence']}, "
          f"tq={qs['trend_quality_score']}")


def test_old_string_through_normalize_then_extract():
    section("D2 — старая строка → normalize → extract_quality_summary")

    norm = normalize_timeframe_result("LONG")
    qs   = extract_quality_summary(norm, "LONG")

    # Нет quality данных → всё N/A, но не падает
    check(norm["quality"]           == {},    "D2.quality == {}")
    check(qs["trend_quality_score"] == "N/A", "D2.tq == N/A (no quality)")
    _assert_summary_format(qs, "D2")


def test_ds_with_normalize():
    section("D3 — format_directional_score + normalize вместе")

    for ds_val, expected in [(42.5, "+42.5"), (-30.0, "-30.0"), (0.0, "0.0")]:
        ds_str = format_directional_score(ds_val)
        check(isinstance(ds_str, str), f"D3.{ds_val}.is_str")

        # Также normalize не ломается при произвольных confidence
        norm = normalize_timeframe_result({"signal": "LONG", "confidence": abs(ds_val)})
        check(norm["signal"] in VALID_SIGNALS, f"D3.{ds_val}.norm.signal valid")


# ═════════════════════════════════════════════════════════════════════════════
#  ИТОГОВЫЙ ОТЧЁТ
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("  STREAMLIT MAPPING TESTS (ui_helpers.py)")
    print("═" * 60)

    suites = [
        # normalize_timeframe_result
        ("N1  — normalize: new dict LONG",                test_normalize_new_dict_long),
        ("N2  — normalize: new dict SHORT",               test_normalize_new_dict_short),
        ("N3  — normalize: new dict WAIT",                test_normalize_new_dict_wait),
        ("N4  — normalize: old string LONG",              test_normalize_old_string_long),
        ("N5  — normalize: old string SHORT variants",    test_normalize_old_string_short),
        ("N6  — normalize: WAIT/ERROR strings",           test_normalize_old_string_wait),
        ("N7  — normalize: ERROR dict",                   test_normalize_error_dict),
        ("N8  — normalize: None → WAIT",                  test_normalize_none),
        ("N9  — normalize: missing confidence key",       test_normalize_missing_confidence),
        ("N10 — normalize: unknown signal → WAIT",        test_normalize_unknown_signal),
        ("N11 — normalize: NaN/inf confidence → 0.0",    test_normalize_nan_inf_confidence),
        ("N12 — normalize: confidence clamped",           test_normalize_confidence_clamped),
        ("N13 — normalize: invalid label → LOW",          test_normalize_invalid_label),
        ("N14 — normalize: always 6 keys",                test_normalize_returns_all_keys),
        # format_directional_score
        ("DS1 — fmt_ds: positive",                        test_fmt_ds_positive),
        ("DS2 — fmt_ds: negative",                        test_fmt_ds_negative),
        ("DS3 — fmt_ds: zero",                            test_fmt_ds_zero),
        ("DS4 — fmt_ds: NaN/inf → 0.0",                  test_fmt_ds_nan_inf),
        ("DS5 — fmt_ds: non-numeric → 0.0",              test_fmt_ds_non_numeric),
        ("DS6 — fmt_ds: explicit sign",                   test_fmt_ds_sign_always_explicit),
        # extract_quality_summary
        ("Q1  — quality: LONG full data",                 test_quality_long),
        ("Q2  — quality: SHORT full data",                test_quality_short),
        ("Q3  — quality: WAIT",                           test_quality_wait),
        ("Q4  — quality: missing quality key",            test_quality_missing_quality_key),
        ("Q5  — quality: None/bad input",                 test_quality_none_input),
        ("Q6  — quality: missing components",             test_quality_missing_components),
        ("Q7  — quality: partial components",             test_quality_partial_components),
        ("Q8  — quality: N/A not 0",                      test_quality_na_not_zero),
        ("Q9  — quality: NaN/inf scores → N/A",          test_quality_nan_inf_scores),
        ("Q10 — quality: wrong direction",                test_quality_wrong_direction),
        ("Q11 — quality: LONG/SHORT independent",         test_quality_both_directions_independent),
        ("Q12 — quality: weight % format",                test_quality_component_weight_format),
        # Integration
        ("D1  — chain: normalize → extract",              test_pipeline_through_normalize),
        ("D2  — chain: old string → extract → N/A",      test_old_string_through_normalize_then_extract),
        ("D3  — chain: ds + normalize",                   test_ds_with_normalize),
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
    print("  UI MAPPING REPORT")
    print("═" * 60)
    for suite_name, suite_ok in suite_results:
        mark = "✓" if suite_ok else "✗"
        print(f"  {mark}  {suite_name}")

    if _failed:
        print("\n  Failed checks:")
        for f in _failed:
            print(f"    ✗  {f}")

    print(f"\n  UI mapping tests: {n_pass}/{total}")
    status = "PASSED" if not _failed else "FAILED"
    print(f"\n  UI MAPPING STATUS: {status}")
    print("═" * 60 + "\n")
    sys.exit(0 if not _failed else 1)


if __name__ == "__main__":
    main()
