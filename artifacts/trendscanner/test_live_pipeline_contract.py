"""
test_live_pipeline_contract.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Контрактные тесты для полного пайплайна.

Проверяет инварианты без реальных запросов к бирже (синтетические данные + mock).

Тесты:
 1. Любой активный LONG имеет confidence >= 50
 2. Любой активный SHORT имеет confidence >= 50
 3. Активный LONG имеет LONG.confirmed=True
 4. Активный SHORT имеет SHORT.confirmed=True
 5. signal и pipeline FINAL.signal совпадают
 6. confidence=0 никогда не сопровождается LONG/SHORT
 7. Неполные quality-данные дают WAIT
 8. Старый строковый сигнал не обходит новый pipeline
 9. Одна и та же signal_index используется volume и breakout
10. Нет NaN/inf в результатах
"""

import math
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analysis         import analyze_timeframe
from quality_pipeline import analyze_both_directions, SIGNAL_THRESHOLD
from volume_quality   import score_volume, _get_current_volume, _get_average_volume
from breakout_quality import calculate_breakout_quality
from confidence       import calculate_confidence
from ui_helpers       import normalize_timeframe_result


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательные
# ─────────────────────────────────────────────────────────────────────────────

PASS_COUNT = 0
FAIL_COUNT = 0
RESULTS    = []


def check(cond: bool, name: str, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        RESULTS.append(("✓", name))
        print(f"  ✓  {name}")
    else:
        FAIL_COUNT += 1
        RESULTS.append(("✗", name))
        print(f"  ✗  {name}")
        if detail:
            print(f"       {detail}")


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def make_df(n: int = 50, avg_vol: float = 1000.0) -> pd.DataFrame:
    """Базовый DataFrame с синтетическими данными."""
    np.random.seed(42)
    closes = 100.0 + np.cumsum(np.random.randn(n) * 0.3)
    opens  = closes - np.abs(np.random.randn(n) * 0.2)
    highs  = closes + np.abs(np.random.randn(n) * 0.4)
    lows   = opens  - np.abs(np.random.randn(n) * 0.4)
    vols   = np.full(n, avg_vol, dtype=float)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def make_analyze_result(signal: str, confidence: float, confirmed: bool = None) -> dict:
    """
    Создаёт минимальный результат analyze_timeframe с заданными полями.
    Используется для тестирования слоёв выше (multi_tf, ui_helpers).
    """
    if confirmed is None:
        confirmed = (signal in ("LONG", "SHORT") and confidence >= 50)

    dir_q = {
        "direction": signal if signal in ("LONG", "SHORT") else "LONG",
        "signal":    signal,
        "confirmed": confirmed,
        "line":      None,
        "trend_quality":    {"trend_quality_score": 60},
        "volume_quality":   {"volume_score": 60},
        "breakout_quality": {"breakout_score": 60.0, "confirmed": confirmed,
                             "components": {"cross": 30.0, "close_distance": 15.0,
                                            "candle_body": 10.0, "rejection_wick": 10.0}},
        "confidence": {"confidence": confidence, "label": "MEDIUM",
                       "available_components": 3, "reason": "test",
                       "components": {}},
        "reason": "test",
    }
    return {
        "trend":            "BULLISH" if signal == "LONG" else
                            ("BEARISH" if signal == "SHORT" else "NEUTRAL"),
        "signal":           signal,
        "score":            confidence,
        "confidence":       confidence,
        "confidence_label": "MEDIUM",
        "reason":           "test",
        "quality": {
            "LONG":  {**dir_q, "direction": "LONG"},
            "SHORT": {**dir_q, "direction": "SHORT"},
            "FINAL": {"signal": signal, "confidence": confidence,
                      "label": "MEDIUM", "reason": "test"},
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 1: LONG имеет confidence >= 50
# ─────────────────────────────────────────────────────────────────────────────

def test_1_long_confidence():
    section("1 — Активный LONG имеет confidence >= 50")

    # Мокируем pipeline так, чтобы он вернул LONG с confidence >= 50
    good_result = {
        "LONG": {
            "direction": "LONG", "signal": "LONG", "confirmed": True,
            "line": {"x1": 0, "y1": 100, "x2": 10, "y2": 95, "slope": -0.5},
            "trend_quality":    {"trend_quality_score": 60},
            "volume_quality":   {"volume_score": 60},
            "breakout_quality": {"breakout_score": 70.0, "confirmed": True,
                                 "components": {"cross": 30.0, "close_distance": 20.0,
                                                "candle_body": 10.0, "rejection_wick": 10.0},
                                 "reason": "ok"},
            "confidence": {"confidence": 65.0, "label": "MEDIUM",
                           "available_components": 3, "reason": "ok", "components": {}},
            "reason": "LONG подтверждён",
        },
        "SHORT": {
            "direction": "SHORT", "signal": "WAIT", "confirmed": False,
            "line": None,
            "trend_quality":    {"trend_quality_score": 0},
            "volume_quality":   {"volume_score": 0},
            "breakout_quality": {"breakout_score": 0.0, "confirmed": False,
                                 "components": {}, "reason": "no cross"},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "no data", "components": {}},
            "reason": "WAIT",
        },
        "FINAL": {"signal": "LONG", "confidence": 65.0, "label": "MEDIUM",
                  "reason": "LONG подтверждён"},
    }

    with patch("analysis.analyze_both_directions", return_value=good_result):
        r = analyze_timeframe(make_df(50))

    check(r["signal"] == "LONG",    "1.1  signal == LONG",    f"got {r['signal']}")
    check(r["confidence"] >= 50.0,  "1.2  confidence >= 50",  f"got {r['confidence']}")
    check(r["trend"] == "BULLISH",  "1.3  trend == BULLISH",  f"got {r['trend']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 2: SHORT имеет confidence >= 50
# ─────────────────────────────────────────────────────────────────────────────

def test_2_short_confidence():
    section("2 — Активный SHORT имеет confidence >= 50")

    good_short = {
        "LONG": {
            "direction": "LONG", "signal": "WAIT", "confirmed": False,
            "line": None,
            "trend_quality": {}, "volume_quality": {}, "breakout_quality": {},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "no data", "components": {}},
            "reason": "WAIT",
        },
        "SHORT": {
            "direction": "SHORT", "signal": "SHORT", "confirmed": True,
            "line": {"x1": 0, "y1": 90, "x2": 10, "y2": 95, "slope": 0.5},
            "trend_quality":    {"trend_quality_score": 55},
            "volume_quality":   {"volume_score": 70},
            "breakout_quality": {"breakout_score": 75.0, "confirmed": True,
                                 "components": {"cross": 30.0, "close_distance": 25.0,
                                                "candle_body": 12.0, "rejection_wick": 8.0},
                                 "reason": "ok"},
            "confidence": {"confidence": 62.5, "label": "MEDIUM",
                           "available_components": 3, "reason": "ok", "components": {}},
            "reason": "SHORT подтверждён",
        },
        "FINAL": {"signal": "SHORT", "confidence": 62.5, "label": "MEDIUM",
                  "reason": "SHORT подтверждён"},
    }

    with patch("analysis.analyze_both_directions", return_value=good_short):
        r = analyze_timeframe(make_df(50))

    check(r["signal"] == "SHORT",   "2.1  signal == SHORT",   f"got {r['signal']}")
    check(r["confidence"] >= 50.0,  "2.2  confidence >= 50",  f"got {r['confidence']}")
    check(r["trend"] == "BEARISH",  "2.3  trend == BEARISH",  f"got {r['trend']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 3: LONG.confirmed = True
# ─────────────────────────────────────────────────────────────────────────────

def test_3_long_confirmed():
    section("3 — Активный LONG имеет LONG.confirmed=True")

    # Если FINAL.signal=LONG но LONG.confirmed=False — инвариант должен дать WAIT
    bad_result = {
        "LONG": {
            "direction": "LONG", "signal": "WAIT", "confirmed": False,
            "line": None, "trend_quality": {}, "volume_quality": {},
            "breakout_quality": {"confirmed": False},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "", "components": {}},
            "reason": "no confirm",
        },
        "SHORT": {
            "direction": "SHORT", "signal": "WAIT", "confirmed": False,
            "line": None, "trend_quality": {}, "volume_quality": {},
            "breakout_quality": {"confirmed": False},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "", "components": {}},
            "reason": "WAIT",
        },
        # Намеренное рассогласование: FINAL говорит LONG с confidence=70
        # но LONG.confirmed=False
        "FINAL": {"signal": "LONG", "confidence": 70.0, "label": "HIGH",
                  "reason": "bad pipeline"},
    }

    with patch("analysis.analyze_both_directions", return_value=bad_result):
        r = analyze_timeframe(make_df(50))

    # Инвариант должен поймать это и вернуть WAIT
    check(r["signal"] == "WAIT",       "3.1  invariant: LONG+unconfirmed → WAIT",
          f"got signal={r['signal']}")
    check("Invariant" in r["reason"],  "3.2  reason contains 'Invariant'",
          f"got reason={r['reason'][:60]}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 4: SHORT.confirmed = True
# ─────────────────────────────────────────────────────────────────────────────

def test_4_short_confirmed():
    section("4 — Активный SHORT имеет SHORT.confirmed=True")

    bad_short = {
        "LONG": {
            "direction": "LONG", "signal": "WAIT", "confirmed": False,
            "line": None, "trend_quality": {}, "volume_quality": {},
            "breakout_quality": {"confirmed": False},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "", "components": {}},
            "reason": "WAIT",
        },
        "SHORT": {
            "direction": "SHORT", "signal": "WAIT", "confirmed": False,
            "line": None, "trend_quality": {}, "volume_quality": {},
            "breakout_quality": {"confirmed": False},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "", "components": {}},
            "reason": "WAIT",
        },
        "FINAL": {"signal": "SHORT", "confidence": 65.0, "label": "MEDIUM",
                  "reason": "bad pipeline — SHORT unconfirmed"},
    }

    with patch("analysis.analyze_both_directions", return_value=bad_short):
        r = analyze_timeframe(make_df(50))

    check(r["signal"] == "WAIT",       "4.1  invariant: SHORT+unconfirmed → WAIT",
          f"got signal={r['signal']}")
    check("Invariant" in r["reason"],  "4.2  reason contains 'Invariant'",
          f"got reason={r['reason'][:60]}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 5: signal совпадает с pipeline FINAL.signal
# ─────────────────────────────────────────────────────────────────────────────

def test_5_signal_matches_pipeline():
    section("5 — analysis.signal совпадает с pipeline FINAL.signal")

    # Нормальный случай: оба LONG
    good = {
        "LONG": {
            "direction": "LONG", "signal": "LONG", "confirmed": True,
            "line": {"x1": 0, "y1": 100, "x2": 10, "y2": 95, "slope": -0.5},
            "trend_quality": {"trend_quality_score": 60},
            "volume_quality": {"volume_score": 60},
            "breakout_quality": {"breakout_score": 70.0, "confirmed": True,
                                 "components": {}, "reason": "ok"},
            "confidence": {"confidence": 60.0, "label": "MEDIUM",
                           "available_components": 3, "reason": "ok", "components": {}},
            "reason": "ok",
        },
        "SHORT": {
            "direction": "SHORT", "signal": "WAIT", "confirmed": False,
            "line": None, "trend_quality": {}, "volume_quality": {},
            "breakout_quality": {"confirmed": False},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "", "components": {}},
            "reason": "WAIT",
        },
        "FINAL": {"signal": "LONG", "confidence": 60.0, "label": "MEDIUM",
                  "reason": "LONG подтверждён"},
    }

    with patch("analysis.analyze_both_directions", return_value=good):
        r = analyze_timeframe(make_df(50))

    pipeline_signal = good["FINAL"]["signal"]
    check(r["signal"] == pipeline_signal, "5.1  signal == pipeline FINAL.signal",
          f"analysis.signal={r['signal']} != pipeline.FINAL.signal={pipeline_signal}")

    # Рассогласование: FINAL.signal=LONG, но analysis ищет SHORT.confirmed
    mismatch = {
        **good,
        "FINAL": {"signal": "SHORT", "confidence": 60.0, "label": "MEDIUM",
                  "reason": "mismatched"},
    }
    with patch("analysis.analyze_both_directions", return_value=mismatch):
        r2 = analyze_timeframe(make_df(50))

    check(r2["signal"] == "WAIT",      "5.2  mismatch FINAL.signal → WAIT via invariant",
          f"got {r2['signal']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 6: confidence=0 никогда не сопровождается LONG/SHORT
# ─────────────────────────────────────────────────────────────────────────────

def test_6_zero_confidence_not_active():
    section("6 — confidence=0 никогда не сопровождается LONG/SHORT")

    zero_conf = {
        "LONG": {
            "direction": "LONG", "signal": "LONG", "confirmed": True,
            "line": {"x1": 0, "y1": 100, "x2": 10, "y2": 95, "slope": -0.5},
            "trend_quality": {"trend_quality_score": 0},
            "volume_quality": {"volume_score": 0},
            "breakout_quality": {"breakout_score": 0.0, "confirmed": False,
                                 "components": {}, "reason": "bad"},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "all zero", "components": {}},
            "reason": "LONG but zero conf",
        },
        "SHORT": {
            "direction": "SHORT", "signal": "WAIT", "confirmed": False,
            "line": None, "trend_quality": {}, "volume_quality": {},
            "breakout_quality": {"confirmed": False},
            "confidence": {"confidence": 0.0, "label": "LOW",
                           "available_components": 0, "reason": "", "components": {}},
            "reason": "WAIT",
        },
        "FINAL": {"signal": "LONG", "confidence": 0.0, "label": "LOW",
                  "reason": "zero confidence LONG — bug!"},
    }

    with patch("analysis.analyze_both_directions", return_value=zero_conf):
        r = analyze_timeframe(make_df(50))

    check(r["signal"] != "LONG",  "6.1  LONG with conf=0 is caught by invariant",
          f"got signal={r['signal']}")
    check(r["signal"] == "WAIT",  "6.2  result is WAIT",
          f"got signal={r['signal']}")
    check(r["confidence"] == 0.0, "6.3  confidence is 0.0",
          f"got {r['confidence']}")

    # normalize_timeframe_result тоже не должен показывать LONG с 0 confidence
    norm = normalize_timeframe_result(r)
    check(not (norm["signal"] == "LONG" and norm["confidence"] == 0.0),
          "6.4  ui_helpers: не (signal=LONG AND confidence=0)",
          f"signal={norm['signal']}, confidence={norm['confidence']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 7: Неполные quality-данные дают WAIT
# ─────────────────────────────────────────────────────────────────────────────

def test_7_incomplete_data_gives_wait():
    section("7 — Неполные quality-данные дают WAIT")

    # Нет данных (df=None)
    r1 = analyze_timeframe(None)
    check(r1["signal"] == "WAIT", "7.1  None df → WAIT", f"got {r1['signal']}")
    check(r1["confidence"] == 0.0, "7.2  None df → confidence=0", f"got {r1['confidence']}")

    # Пустой DataFrame
    r2 = analyze_timeframe(pd.DataFrame())
    check(r2["signal"] == "WAIT", "7.3  Empty df → WAIT", f"got {r2['signal']}")

    # Слишком мало строк для пивотов (< 11)
    small_df = make_df(8)
    r3 = analyze_timeframe(small_df)
    check(r3["signal"] == "WAIT", "7.4  Small df (<11 rows) → WAIT",
          f"got {r3['signal']}, reason={r3['reason'][:60]}")

    # calculate_confidence без компонентов → confidence=0
    conf_empty = calculate_confidence(None, None, None)
    check(conf_empty["confidence"] == 0.0,
          "7.5  calculate_confidence(None, None, None) → 0",
          f"got {conf_empty['confidence']}")
    check(conf_empty["available_components"] == 0,
          "7.6  available_components == 0",
          f"got {conf_empty['available_components']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 8: Старый строковый сигнал не обходит pipeline
# ─────────────────────────────────────────────────────────────────────────────

def test_8_old_string_cannot_bypass():
    section("8 — Старый строковый сигнал не обходит pipeline")

    # normalize_timeframe_result получает старую строку "LONG"
    for old_signal in ["LONG", "SHORT", "WAIT", "long", "Long"]:
        norm = normalize_timeframe_result(old_signal)
        check(norm["confidence"] == 0.0,
              f"8.  str '{old_signal}' → confidence=0.0",
              f"got {norm['confidence']}")

    # Строка LONG → confidence=0, не может быть "активным" сигналом
    for s in ["LONG", "SHORT"]:
        norm = normalize_timeframe_result(s)
        check(not (norm["signal"] in ("LONG", "SHORT") and norm["confidence"] >= 50),
              f"8.  str '{s}' cannot be active (conf<50)",
              f"signal={norm['signal']}, confidence={norm['confidence']}")

    # analyze_timeframe со строкой вместо DataFrame → WAIT (не поднимает исключение)
    r = analyze_timeframe("LONG")
    check(r["signal"] == "WAIT", "8.  str input to analyze_timeframe → WAIT",
          f"got {r['signal']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 9: Volume и breakout используют одну signal_index
# ─────────────────────────────────────────────────────────────────────────────

def test_9_signal_index_alignment():
    section("9 — volume и breakout используют одну signal_index")

    n = 50
    df = make_df(n, avg_vol=1000.0)

    # Намеренно ставим РАЗНЫЕ объёмы на n-1 и n-2
    df.iloc[-1,  df.columns.get_loc("volume")] = 9999.0   # последняя (незакрытая)
    df.iloc[-2,  df.columns.get_loc("volume")] = 500.0    # сигнальная (закрытая)

    # score_volume с default signal_index=-2 должна читать СИГНАЛЬНУЮ свечу
    result_default = score_volume(df)
    check(result_default["current_volume"] == 500.0,
          "9.1  score_volume default uses signal candle (n-2)",
          f"got current_volume={result_default['current_volume']}")

    # Явный signal_index=-1 должен читать последнюю
    result_last = score_volume(df, signal_index=-1)
    check(result_last["current_volume"] == 9999.0,
          "9.2  score_volume(signal_index=-1) uses last candle",
          f"got current_volume={result_last['current_volume']}")

    # breakout_quality всегда использует n-2
    line = {"x1": 0, "y1": 105.0, "x2": 40, "y2": 102.0,
            "slope": (102.0 - 105.0) / 40}
    bq = calculate_breakout_quality(df, line, "LONG")
    check(bq["signal_index"] == n - 2,
          f"9.3  breakout_quality.signal_index == n-2 ({n-2})",
          f"got {bq['signal_index']}")

    # volume и breakout согласованы: оба смотрят на n-2
    check(result_default["current_volume"] != result_last["current_volume"],
          "9.4  different volumes at signal vs last candle (test setup OK)")

    # _get_current_volume с разными signal_index
    vol_at_minus2 = _get_current_volume(df, signal_index=-2)
    vol_at_minus1 = _get_current_volume(df, signal_index=-1)
    check(vol_at_minus2 == 500.0,  "9.5  _get_current_volume(-2) == 500",
          f"got {vol_at_minus2}")
    check(vol_at_minus1 == 9999.0, "9.6  _get_current_volume(-1) == 9999",
          f"got {vol_at_minus1}")


# ─────────────────────────────────────────────────────────────────────────────
#  Тест 10: Нет NaN/inf в результатах
# ─────────────────────────────────────────────────────────────────────────────

def test_10_no_nan_inf():
    section("10 — Нет NaN/inf в результатах")

    df = make_df(30)

    # analyze_timeframe → проверяем ключевые числовые поля
    r = analyze_timeframe(df)
    numeric_fields = ["confidence", "score"]
    for field in numeric_fields:
        val = r.get(field)
        if val is not None:
            check(math.isfinite(float(val)),
                  f"10.1  analyze_timeframe.{field} is finite",
                  f"got {val}")

    # score_volume → числовые поля
    vr = score_volume(df)
    for field in ["volume_score", "current_volume", "average_volume", "volume_ratio"]:
        val = vr.get(field)
        if val is not None:
            try:
                f = float(val)
                check(math.isfinite(f),
                      f"10.2  score_volume.{field} is finite",
                      f"got {val}")
            except (TypeError, ValueError):
                check(False, f"10.2  score_volume.{field} cannot be float",
                      f"got {val!r}")

    # calculate_confidence с нормальными входными данными
    tq = {"trend_quality_score": 60}
    vq = {"volume_score": 70}
    bq = {"breakout_score": 80.0, "confirmed": True,
          "components": {"cross": 30.0, "close_distance": 20.0,
                         "candle_body": 15.0, "rejection_wick": 15.0},
          "reason": "test"}
    conf = calculate_confidence(tq, vq, bq)
    cv = conf.get("confidence")
    check(cv is not None and math.isfinite(float(cv)),
          "10.3  calculate_confidence result is finite",
          f"got {cv}")
    check(0.0 <= float(cv) <= 100.0,
          "10.4  calculate_confidence in [0, 100]",
          f"got {cv}")

    # normalize_timeframe_result с NaN confidence
    bad_result = {"signal": "LONG", "confidence": float("nan"),
                  "confidence_label": "HIGH", "trend": "BULLISH", "reason": "", "quality": {}}
    norm = normalize_timeframe_result(bad_result)
    check(math.isfinite(norm["confidence"]),
          "10.5  normalize: NaN confidence → finite",
          f"got {norm['confidence']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Главная
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print()
    print("═" * 60)
    print("  LIVE PIPELINE CONTRACT TESTS")
    print("═" * 60)

    test_1_long_confidence()
    test_2_short_confidence()
    test_3_long_confirmed()
    test_4_short_confirmed()
    test_5_signal_matches_pipeline()
    test_6_zero_confidence_not_active()
    test_7_incomplete_data_gives_wait()
    test_8_old_string_cannot_bypass()
    test_9_signal_index_alignment()
    test_10_no_nan_inf()

    total = PASS_COUNT + FAIL_COUNT
    print()
    print("═" * 60)
    for symbol, name in RESULTS:
        print(f"  {symbol}  {name}")
    print()
    print(f"  Contract tests: {PASS_COUNT}/{total}")
    print()
    if FAIL_COUNT == 0:
        print("  CONTRACT STATUS: PASSED")
    else:
        print(f"  CONTRACT STATUS: FAILED ({FAIL_COUNT} failures)")
    print("═" * 60)
    print()

    return FAIL_COUNT == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
