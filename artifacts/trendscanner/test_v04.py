"""
test_v04.py
~~~~~~~~~~~
Валидационные тесты для Trend Scanner v0.4 (с интегрированным breakout_quality).

Проверяет независимые модули ДО их интеграции в Streamlit / multi_tf.py:
    • trend_quality.py       → calc_trend_quality()
    • volume_quality.py      → score_volume()
    • breakout_quality.py    → calculate_breakout_quality()
    • confidence.py          → calculate_confidence()  [теперь с breakout_quality]

НЕ изменяет, НЕ импортирует scanner.py / analysis.py / multi_tf.py / app.py.
Запуск: python test_v04.py
"""

import sys
import math
import traceback

import pandas as pd
import numpy as np

from trend_quality    import calc_trend_quality
from volume_quality   import score_volume
from breakout_quality import calculate_breakout_quality
from confidence       import calculate_confidence, WEIGHTS


# ══════════════════════════════════════════════════════════════════════════════
#  Инструменты
# ══════════════════════════════════════════════════════════════════════════════

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


def approx_eq(a: float, b: float, tol: float = 0.5) -> bool:
    return abs(a - b) <= tol


def assert_in_range(val, lo, hi, name: str) -> bool:
    r = f"got {val!r}, expected [{lo}, {hi}]"
    return check(lo <= val <= hi, name, r, f"{val}")


def assert_approx(val: float, expected: float, name: str, tol: float = 0.5) -> bool:
    ok_flag = math.isfinite(val) and abs(val - expected) <= tol
    return check(ok_flag, name,
                 f"got {val!r}, expected ≈{expected} (±{tol})",
                 f"{val}")


def assert_no_nan_inf(val, name: str) -> bool:
    if isinstance(val, float):
        return check(math.isfinite(val), name, f"got {val!r} (NaN or Inf)")
    return check(True, name)


def assert_type(val, typ, name: str) -> bool:
    return check(isinstance(val, typ), name,
                 f"expected {typ.__name__}, got {type(val).__name__}")


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ══════════════════════════════════════════════════════════════════════════════
#  Фабрики синтетических данных
# ══════════════════════════════════════════════════════════════════════════════

def make_baseline_df(n: int = 25,
                     base_price: float = 100.0,
                     avg_volume: float = 1000.0) -> pd.DataFrame:
    opens  = np.full(n, base_price - 0.3)
    closes = np.full(n, base_price)
    highs  = np.full(n, base_price + 0.5)
    lows   = np.full(n, base_price - 0.5)
    vols   = np.full(n, avg_volume)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def set_candle(df: pd.DataFrame, idx: int,
               open_: float, high: float, low: float,
               close: float, volume: float = 1000.0) -> None:
    df.at[idx, "open"]   = open_
    df.at[idx, "high"]   = high
    df.at[idx, "low"]    = low
    df.at[idx, "close"]  = close
    df.at[idx, "volume"] = volume


def descending_line(x1: int = 0, y1: float = 110.0,
                    x2: int = 20, y2: float = 100.0) -> dict:
    slope = (y2 - y1) / (x2 - x1)
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "slope": slope}


def ascending_line(x1: int = 0, y1: float = 90.0,
                   x2: int = 20, y2: float = 100.0) -> dict:
    slope = (y2 - y1) / (x2 - x1)
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "slope": slope}


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ A — качественный LONG-пробой
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_a():
    section("СЦЕНАРИЙ A — качественный LONG-пробой")

    df = make_baseline_df(n=25, base_price=97.5, avg_volume=1000.0)
    line = descending_line(x1=0, y1=110.0, x2=20, y2=100.0)
    # line at 22=99.0, line at 23=98.5
    set_candle(df, 22, open_=97.5, high=98.9, low=97.0, close=98.0, volume=950.0)
    set_candle(df, 23, open_=98.2, high=99.6, low=98.0, close=99.5, volume=1800.0)
    set_candle(df, 24, open_=99.5, high=99.8, low=99.3, close=99.6, volume=400.0)

    r = calculate_breakout_quality(df, line, "LONG")

    check(r["confirmed"] is True,     "A.confirmed == True",
          f"got confirmed={r['confirmed']}, reason={r['reason']}")
    assert_in_range(r["breakout_score"], 50, 100, "A.breakout_score in [50, 100]")
    assert_in_range(r["components"]["cross"], 29.9, 30.1,  "A.cross == 30")
    assert_in_range(r["components"]["close_distance"], 15.0, 30.0, "A.close_distance ≥ 15")
    assert_in_range(r["components"]["candle_body"],    18.0, 20.0, "A.candle_body ≈ 20")
    assert_in_range(r["components"]["rejection_wick"], 18.0, 20.0, "A.rejection_wick ≈ 20")
    assert_type(r["confirmed"], bool, "A.confirmed is bool")
    assert_no_nan_inf(r["breakout_score"], "A.breakout_score finite")

    df_vol = make_baseline_df(n=22, avg_volume=1000.0)
    set_candle(df_vol, 21, open_=98.2, high=99.6, low=98.0, close=99.5, volume=1800.0)
    vr = score_volume(df_vol)
    assert_in_range(vr["volume_score"], 60, 100, "A.volume_score in [60, 100]")
    assert_in_range(vr["volume_ratio"],  1.0, 4.0, "A.volume_ratio ≥ 1.0")
    assert_no_nan_inf(float(vr["volume_score"]), "A.volume_score finite")

    print(f"     breakout_score={r['breakout_score']}, "
          f"components={r['components']}, volume_ratio={vr['volume_ratio']}")


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ B — ложный LONG-пробой
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_b():
    section("СЦЕНАРИЙ B — ложный LONG-пробой")

    df = make_baseline_df(n=25, base_price=98.0, avg_volume=1000.0)
    line = descending_line(x1=0, y1=110.0, x2=20, y2=100.0)

    set_candle(df, 22, open_=98.5, high=99.1, low=98.2, close=98.8, volume=900.0)
    set_candle(df, 23, open_=98.55, high=99.5, low=98.4, close=98.6, volume=600.0)
    set_candle(df, 24, open_=98.6, high=98.9, low=98.4, close=98.7, volume=400.0)

    r = calculate_breakout_quality(df, line, "LONG")

    check(r["confirmed"] is False,   "B.confirmed == False",
          f"got confirmed={r['confirmed']}, score={r['breakout_score']}")
    assert_in_range(r["breakout_score"],           0, 49.9, "B.breakout_score < 50")
    assert_in_range(r["components"]["candle_body"], 0,  5.0, "B.candle_body small (≤5)")
    assert_in_range(r["components"]["rejection_wick"], 0, 5.0, "B.rejection_wick low (≤5)")
    assert_type(r["confirmed"], bool, "B.confirmed is bool")

    df_vol = make_baseline_df(n=22, avg_volume=1000.0)
    set_candle(df_vol, 21, open_=98.55, high=99.5, low=98.4, close=98.6, volume=600.0)
    vr = score_volume(df_vol)
    assert_in_range(vr["volume_score"], 0, 60, "B.volume_score low (< 60)")

    print(f"     breakout_score={r['breakout_score']}, "
          f"components={r['components']}, volume_score={vr['volume_score']}")


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ C — качественный SHORT-пробой
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_c():
    section("СЦЕНАРИЙ C — качественный SHORT-пробой")

    df = make_baseline_df(n=25, base_price=102.0, avg_volume=1000.0)
    line = ascending_line(x1=0, y1=90.0, x2=20, y2=100.0)
    # line at 22=101.0, line at 23=101.5

    set_candle(df, 22, open_=101.8, high=102.2, low=101.2, close=101.5, volume=950.0)
    set_candle(df, 23, open_=101.5, high=101.8, low=100.3, close=100.5, volume=1900.0)
    set_candle(df, 24, open_=100.5, high=100.8, low=100.2, close=100.4, volume=400.0)

    r = calculate_breakout_quality(df, line, "SHORT")

    check(r["confirmed"] is True,   "C.confirmed == True",
          f"got confirmed={r['confirmed']}, score={r['breakout_score']}, reason={r['reason']}")
    assert_in_range(r["breakout_score"], 50, 100, "C.breakout_score in [50, 100]")
    assert_in_range(r["components"]["cross"],          29.9, 30.1, "C.cross == 30")
    assert_in_range(r["components"]["close_distance"], 10.0, 30.0, "C.close_distance ≥ 10")
    assert_in_range(r["components"]["candle_body"],    10.0, 20.0, "C.candle_body ≥ 10")
    assert_in_range(r["components"]["rejection_wick"], 15.0, 20.0, "C.rejection_wick ≥ 15")
    assert_type(r["confirmed"], bool, "C.confirmed is bool")
    assert_no_nan_inf(r["breakout_score"], "C.breakout_score finite")

    print(f"     breakout_score={r['breakout_score']}, "
          f"components={r['components']}")


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ D — нет пересечения линии
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_d():
    section("СЦЕНАРИЙ D — нет пересечения линии")

    df = make_baseline_df(n=25, base_price=97.0)
    line = descending_line(x1=0, y1=110.0, x2=20, y2=100.0)
    set_candle(df, 22, open_=96.5, high=97.5, low=96.0, close=97.0)
    set_candle(df, 23, open_=97.0, high=98.0, low=96.8, close=97.8)
    set_candle(df, 24, open_=97.8, high=98.2, low=97.5, close=98.0)

    r = calculate_breakout_quality(df, line, "LONG")

    check(r["confirmed"] is False,  "D.confirmed == False",
          f"cross={r['components']['cross']}, score={r['breakout_score']}")
    assert_in_range(r["components"]["cross"], -0.1, 0.1, "D.cross == 0")
    assert_type(r["confirmed"], bool, "D.confirmed is bool")

    print(f"     cross={r['components']['cross']}, "
          f"breakout_score={r['breakout_score']}, reason={r['reason']!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ E — недостаточно данных (< 3 строк)
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_e():
    section("СЦЕНАРИЙ E — недостаточно данных (< 3 строк)")

    line = descending_line()
    df2  = make_baseline_df(n=2)

    for direction in ("LONG", "SHORT"):
        r = calculate_breakout_quality(df2, line, direction)
        assert_in_range(r["breakout_score"], 0, 0,    f"E.{direction}.score == 0")
        check(r["confirmed"] is False,               f"E.{direction}.confirmed == False",
              f"got {r['confirmed']}")
        check(isinstance(r["reason"], str) and len(r["reason"]) > 0,
              f"E.{direction}.reason is non-empty string",
              f"got reason={r['reason']!r}")
        assert_type(r["confirmed"], bool, f"E.{direction}.confirmed is bool")

    print(f"     reason={r['reason']!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ F — line = None
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_f():
    section("СЦЕНАРИЙ F — line = None")

    df = make_baseline_df(n=25)

    for direction in ("LONG", "SHORT"):
        r = calculate_breakout_quality(df, None, direction)
        assert_in_range(r["breakout_score"], 0, 0,   f"F.{direction}.score == 0")
        check(r["confirmed"] is False,              f"F.{direction}.confirmed == False",
              f"got {r['confirmed']}")
        check(isinstance(r["reason"], str) and len(r["reason"]) > 0,
              f"F.{direction}.reason non-empty",
              f"got {r['reason']!r}")
        assert_type(r["confirmed"], bool, f"F.{direction}.confirmed is bool")

    print(f"     reason={r['reason']!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ G — DataFrame без колонки volume
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_g():
    section("СЦЕНАРИЙ G — DataFrame без колонки 'volume'")

    df_no_vol = pd.DataFrame({
        "open":  [100.0, 101.0, 102.0],
        "high":  [100.5, 101.5, 102.5],
        "low":   [99.5,  100.5, 101.5],
        "close": [100.2, 101.2, 102.2],
    })

    try:
        result = score_volume(df_no_vol)
        crashed = False
    except Exception as e:
        result = {}
        crashed = True
        print(f"     EXCEPTION: {e}")

    check(not crashed, "G.no_crash — score_volume не упал", "raised exception")
    if not crashed:
        check("volume_score" in result, "G.volume_score key present",
              f"keys={list(result.keys())}")
        check("reason" in result, "G.reason key present",
              f"keys={list(result.keys())}")
        if "volume_score" in result:
            assert_in_range(result["volume_score"], 0, 0,
                            "G.volume_score == 0 (no data)")
        print(f"     result={result}")


# ══════════════════════════════════════════════════════════════════════════════
#  ASSERT — диапазоны, типы, ключи
# ══════════════════════════════════════════════════════════════════════════════

def test_assert_ranges_and_types():
    section("ASSERT — диапазоны, типы, ключи компонентов")

    df = make_baseline_df(n=25, base_price=97.0)
    line = descending_line()
    set_candle(df, 22, open_=97.5, high=98.9, low=97.0, close=98.0)
    set_candle(df, 23, open_=98.2, high=99.6, low=98.0, close=99.5)
    set_candle(df, 24, open_=99.5, high=99.8, low=99.3, close=99.6)

    r = calculate_breakout_quality(df, line, "LONG")

    assert_in_range(r["breakout_score"], 0, 100, "ASSERT.breakout_score in [0,100]")
    for comp_name, comp_val in r["components"].items():
        assert_in_range(comp_val, 0, 30,
                        f"ASSERT.component.{comp_name} in [0,30]")

    assert_no_nan_inf(r["breakout_score"], "ASSERT.breakout_score not NaN/Inf")
    for comp_name, comp_val in r["components"].items():
        assert_no_nan_inf(comp_val, f"ASSERT.component.{comp_name} not NaN/Inf")

    assert_type(r["confirmed"], bool, "ASSERT.confirmed type==bool")

    expected_comp_keys = {"cross", "close_distance", "candle_body", "rejection_wick"}
    actual_keys = set(r["components"].keys())
    check(expected_comp_keys == actual_keys,
          "ASSERT.components keys correct",
          f"expected {expected_comp_keys}, got {actual_keys}")

    # invalid direction → ValueError
    try:
        calculate_breakout_quality(df, line, "SIDEWAYS")
        check(False, "ASSERT.invalid_direction raises ValueError", "no exception raised")
    except ValueError:
        check(True, "ASSERT.invalid_direction raises ValueError")
    except Exception as e:
        check(False, "ASSERT.invalid_direction raises ValueError",
              f"got {type(e).__name__}: {e}")


def test_assert_symmetry():
    section("ASSERT — LONG и SHORT обрабатываются симметрично")

    n = 25
    df_long = make_baseline_df(n=n, base_price=97.0)
    line_down = descending_line(x1=0, y1=110.0, x2=20, y2=100.0)
    set_candle(df_long, 22, open_=97.5, high=98.9, low=97.0, close=98.0)
    set_candle(df_long, 23, open_=98.2, high=99.6, low=98.0, close=99.5)
    set_candle(df_long, 24, open_=99.5, high=99.8, low=99.3, close=99.6)
    r_long = calculate_breakout_quality(df_long, line_down, "LONG")

    df_short = make_baseline_df(n=n, base_price=102.0)
    line_up = ascending_line(x1=0, y1=90.0, x2=20, y2=100.0)
    set_candle(df_short, 22, open_=101.8, high=102.2, low=101.2, close=101.5)
    set_candle(df_short, 23, open_=101.5, high=101.8, low=100.3, close=100.5)
    set_candle(df_short, 24, open_=100.5, high=100.8, low=100.2, close=100.4)
    r_short = calculate_breakout_quality(df_short, line_up, "SHORT")

    check(r_long["confirmed"] and r_short["confirmed"],
          "ASSERT.symmetry — оба подтверждены",
          f"long={r_long['confirmed']}, short={r_short['confirmed']}")
    check(r_long["components"]["cross"]  == 30.0,
          "ASSERT.symmetry LONG cross==30",  f"got {r_long['components']['cross']}")
    check(r_short["components"]["cross"] == 30.0,
          "ASSERT.symmetry SHORT cross==30", f"got {r_short['components']['cross']}")

    print(f"     LONG score={r_long['breakout_score']}, "
          f"SHORT score={r_short['breakout_score']}")


# ══════════════════════════════════════════════════════════════════════════════
#  TREND QUALITY
# ══════════════════════════════════════════════════════════════════════════════

def test_trend_quality():
    section("TREND QUALITY — ключи и диапазон 0–100")

    n = 60
    closes = np.linspace(100.0, 110.0, n)
    df = pd.DataFrame({
        "open":   closes - 0.3,
        "high":   closes + 0.5,
        "low":    closes - 0.5,
        "close":  closes,
        "volume": np.full(n, 1000.0),
    })
    line = {
        "x1": 5,  "y1": closes[5],
        "x2": 50, "y2": closes[50],
        "slope": (closes[50] - closes[5]) / (50 - 5),
    }

    result = calc_trend_quality(df, line)

    check("trend_quality_score" in result, "TQ.key trend_quality_score present",
          f"keys={list(result.keys())}")
    assert_in_range(result["trend_quality_score"], 0, 100, "TQ.score in [0,100]")
    assert_no_nan_inf(float(result["trend_quality_score"]), "TQ.score not NaN/Inf")

    for k in ("touches_score", "length_score", "angle_score", "freshness_score"):
        assert_in_range(result[k], 0, 100, f"TQ.{k} in [0,100]")

    r_none = calc_trend_quality(df, None)
    check(r_none["trend_quality_score"] == 0, "TQ.none → score==0",
          f"got {r_none['trend_quality_score']}")

    print(f"     result={result}")


# ══════════════════════════════════════════════════════════════════════════════
#  VOLUME QUALITY
# ══════════════════════════════════════════════════════════════════════════════

def test_volume_quality():
    section("VOLUME QUALITY — ключи и диапазон 0–100")

    df = make_baseline_df(n=25, avg_volume=1000.0)
    df.at[24, "volume"] = 1700.0

    result = score_volume(df)

    check("volume_score" in result, "VQ.key volume_score present",
          f"keys={list(result.keys())}")
    assert_in_range(result["volume_score"], 0, 100, "VQ.volume_score in [0,100]")
    assert_no_nan_inf(float(result["volume_score"]), "VQ.volume_score not NaN/Inf")
    check(result["volume_score"] == 100, "VQ.high_volume → score==100",
          f"got {result['volume_score']}, ratio={result['volume_ratio']}")

    df_short = make_baseline_df(n=5)
    r_short = score_volume(df_short, period=20)
    assert_in_range(r_short["volume_score"], 0, 0, "VQ.insufficient_data score==0")

    print(f"     result={result}")


# ══════════════════════════════════════════════════════════════════════════════
#  MUTABLE DEFAULT ARGUMENTS
# ══════════════════════════════════════════════════════════════════════════════

def test_no_mutable_defaults():
    section("MUTABLE DEFAULTS — нет параметров вида param={} или param=[]")
    import inspect

    modules_to_check = [
        ("trend_quality",    "calc_trend_quality"),
        ("volume_quality",   "score_volume"),
        ("breakout_quality", "calculate_breakout_quality"),
        ("confidence",       "calculate_confidence"),
    ]

    for mod_name, func_name in modules_to_check:
        mod = sys.modules.get(mod_name) or __import__(mod_name)
        func = getattr(mod, func_name, None)
        if func is None:
            fail(f"MD.{func_name} found", "function not found")
            continue

        sig = inspect.signature(func)
        bad_params = []
        for pname, param in sig.parameters.items():
            if param.default in (inspect.Parameter.empty,):
                continue
            d = param.default
            if isinstance(d, (dict, list)):
                bad_params.append(f"{pname}={d!r}")

        if bad_params:
            fail(f"MD.{func_name} no mutable defaults",
                 f"found: {', '.join(bad_params)}")
        else:
            ok(f"MD.{func_name} no mutable defaults")


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIDENCE — полная проверка трёх компонентов
# ══════════════════════════════════════════════════════════════════════════════

def test_confidence():
    section("CONFIDENCE — WEIGHTS, перенормировка, все три компонента")

    # Ожидаемые базовые веса
    W_TQ = WEIGHTS["trend_quality"]      # 0.50
    W_VQ = WEIGHTS["volume_quality"]     # 0.20
    W_BQ = WEIGHTS["breakout_quality"]   # 0.30

    # ─ 1. Проверка WEIGHTS ──────────────────────────────────────────────────
    w_sum = sum(v for v in WEIGHTS.values() if v > 0.0)
    check(abs(w_sum - 1.0) < 1e-9,
          f"CONF.WEIGHTS_sum == 1.0  (sum={w_sum:.4f})",
          f"got {w_sum}")
    assert_approx(W_TQ, 0.50, "CONF.WEIGHTS.trend_quality == 0.50",  tol=1e-9)
    assert_approx(W_VQ, 0.20, "CONF.WEIGHTS.volume_quality == 0.20", tol=1e-9)
    assert_approx(W_BQ, 0.30, "CONF.WEIGHTS.breakout_quality == 0.30", tol=1e-9)

    tq_d  = {"trend_quality_score":  80}
    vq_d  = {"volume_score":         60}
    bq_d  = {"breakout_score":       70}

    # ─ 2. Все три компонента присутствуют ────────────────────────────────────
    # Ожидается: (80*0.50 + 60*0.20 + 70*0.30) = 40+12+21 = 73.0
    r_all = calculate_confidence(tq_d, vq_d, breakout_quality=bq_d)
    assert_approx(r_all["confidence"], 73.0,
                  "CONF.all3.confidence ≈ 73.0", tol=0.1)
    assert_in_range(r_all["confidence"], 0, 100, "CONF.all3.confidence in [0,100]")
    check(r_all["available_components"] == 3,
          "CONF.all3.available_components == 3",
          f"got {r_all['available_components']}")
    check(isinstance(r_all["reason"], str) and len(r_all["reason"]) > 0,
          "CONF.all3.reason non-empty")
    check("все 3" in r_all["reason"].lower() or "все 3" in r_all["reason"],
          "CONF.all3.reason mentions all 3",
          f"got: {r_all['reason']!r}")

    # ─ 3. Нет breakout (None) ────────────────────────────────────────────────
    # Ожидается: (80*0.50 + 60*0.20) / (0.50+0.20) = 52/0.70 ≈ 74.29
    r_no_bq = calculate_confidence(tq_d, vq_d, breakout_quality=None)
    assert_approx(r_no_bq["confidence"], 74.29,
                  "CONF.no_bq.confidence ≈ 74.29", tol=0.5)
    check(r_no_bq["available_components"] == 2,
          "CONF.no_bq.available_components == 2",
          f"got {r_no_bq['available_components']}")
    # breakout должен присутствовать в components с available=False
    check("breakout_quality" in r_no_bq["components"],
          "CONF.no_bq.components has breakout_quality key")
    check(r_no_bq["components"]["breakout_quality"]["available"] is False,
          "CONF.no_bq.breakout_quality.available == False",
          f"got {r_no_bq['components']['breakout_quality']['available']}")
    check(r_no_bq["components"]["breakout_quality"]["contribution"] == 0.0,
          "CONF.no_bq.breakout_quality.contribution == 0")

    # ─ 4. Нет volume ({}) ────────────────────────────────────────────────────
    # Ожидается: (80*0.50 + 70*0.30) / (0.50+0.30) = 61/0.80 = 76.25
    r_no_vq = calculate_confidence(tq_d, {}, breakout_quality=bq_d)
    assert_approx(r_no_vq["confidence"], 76.25,
                  "CONF.no_vq.confidence ≈ 76.25", tol=0.5)
    check(r_no_vq["available_components"] == 2,
          "CONF.no_vq.available_components == 2",
          f"got {r_no_vq['available_components']}")
    check(r_no_vq["components"]["volume_quality"]["available"] is False,
          "CONF.no_vq.volume_quality.available == False")

    # ─ 5. Нет trend (None) ───────────────────────────────────────────────────
    # Ожидается: (60*0.20 + 70*0.30) / (0.20+0.30) = 33/0.50 = 66.0
    r_no_tq = calculate_confidence(None, vq_d, breakout_quality=bq_d)
    assert_approx(r_no_tq["confidence"], 66.0,
                  "CONF.no_tq.confidence ≈ 66.0", tol=0.5)
    check(r_no_tq["available_components"] == 2,
          "CONF.no_tq.available_components == 2",
          f"got {r_no_tq['available_components']}")
    check(r_no_tq["components"]["trend_quality"]["available"] is False,
          "CONF.no_tq.trend_quality.available == False")

    # ─ 6. Только один компонент: trend ───────────────────────────────────────
    # Ожидается: 80 * (0.50/0.50) = 80.0
    r_only_tq = calculate_confidence(tq_d, {}, breakout_quality=None)
    assert_approx(r_only_tq["confidence"], 80.0,
                  "CONF.only_tq.confidence ≈ 80.0", tol=0.1)
    check(r_only_tq["available_components"] == 1,
          "CONF.only_tq.available_components == 1",
          f"got {r_only_tq['available_components']}")
    # effective_weight единственного компонента = 1.0
    eff_w = r_only_tq["components"]["trend_quality"]["effective_weight"]
    assert_approx(eff_w, 1.0, "CONF.only_tq.effective_weight == 1.0", tol=1e-6)

    # ─ 7. Только один компонент: volume ──────────────────────────────────────
    r_only_vq = calculate_confidence({}, vq_d, breakout_quality=None)
    assert_approx(r_only_vq["confidence"], 60.0,
                  "CONF.only_vq.confidence ≈ 60.0", tol=0.1)
    check(r_only_vq["available_components"] == 1,
          "CONF.only_vq.available_components == 1")

    # ─ 8. Только один компонент: breakout ────────────────────────────────────
    # Ожидается: 70 * (0.30/0.30) = 70.0
    r_only_bq = calculate_confidence({}, {}, breakout_quality=bq_d)
    assert_approx(r_only_bq["confidence"], 70.0,
                  "CONF.only_bq.confidence ≈ 70.0", tol=0.1)
    check(r_only_bq["available_components"] == 1,
          "CONF.only_bq.available_components == 1")

    # ─ 9. Нет ни одного компонента ───────────────────────────────────────────
    r_none = calculate_confidence({}, {}, breakout_quality=None)
    assert_approx(r_none["confidence"], 0.0, "CONF.none.confidence == 0", tol=0.01)
    check(r_none["available_components"] == 0,
          "CONF.none.available_components == 0",
          f"got {r_none['available_components']}")
    check(isinstance(r_none["reason"], str) and len(r_none["reason"]) > 0,
          "CONF.none.reason non-empty")

    # ─ 10. Сумма effective_weight доступных компонентов = 1.0 ─────────────────
    for label, r in [("all3", r_all), ("no_bq", r_no_bq),
                     ("no_vq", r_no_vq), ("no_tq", r_no_tq)]:
        eff_sum = sum(
            comp["effective_weight"]
            for comp in r["components"].values()
            if comp["available"]
        )
        assert_approx(eff_sum, 1.0,
                      f"CONF.{label}.sum(effective_weight)==1.0", tol=1e-6)

    # ─ 11. Сумма contribution ≈ confidence ───────────────────────────────────
    for label, r in [("all3", r_all), ("no_bq", r_no_bq),
                     ("no_vq", r_no_vq), ("no_tq", r_no_tq),
                     ("only_tq", r_only_tq), ("only_bq", r_only_bq)]:
        contrib_sum = sum(
            comp["contribution"]
            for comp in r["components"].values()
        )
        # Допускаем погрешность из-за round() в dict
        assert_approx(contrib_sum, r["confidence"],
                      f"CONF.{label}.sum(contribution)≈confidence", tol=1.0)

    # ─ 12. Нет NaN / inf ─────────────────────────────────────────────────────
    # score может быть None для недоступных компонентов — пропускаем None,
    # но проверяем все float-значения.
    for label, r in [("all3", r_all), ("no_bq", r_no_bq), ("none", r_none)]:
        assert_no_nan_inf(float(r["confidence"]), f"CONF.{label}.confidence not NaN/Inf")
        for comp_name, comp in r["components"].items():
            for field in ("score", "effective_weight", "contribution"):
                v = comp[field]
                if v is None:
                    ok(f"CONF.{label}.{comp_name}.{field} is None (unavailable)",
                       "skipping NaN/Inf check for None")
                else:
                    assert_no_nan_inf(float(v),
                                      f"CONF.{label}.{comp_name}.{field} not NaN/Inf")

    # ─ 13. Label корректен ───────────────────────────────────────────────────
    # При одинаковых score во всех компонентах → confidence = score (вес=1.0)
    label_cases = [(0, "LOW"), (39, "LOW"), (40, "MEDIUM"), (69, "MEDIUM"),
                   (70, "HIGH"), (84, "HIGH"), (85, "VERY HIGH"), (100, "VERY HIGH")]
    for val, expected_label in label_cases:
        d = {"trend_quality_score": val, "volume_score": val}
        r_l = calculate_confidence(
            {"trend_quality_score": val},
            {"volume_score": val},
            breakout_quality={"breakout_score": val},
        )
        # Все три компонента с одинаковым val → confidence = val
        assert_approx(r_l["confidence"], float(val),
                      f"CONF.label_math({val})", tol=0.5)
        check(r_l["label"] == expected_label,
              f"CONF.label({val})=={expected_label!r}",
              f"got {r_l['label']!r}")

    # ─ 14. Структура components ──────────────────────────────────────────────
    # "reason" добавлен в формат v0.4 — каждый компонент содержит объяснение
    expected_comp_fields = {"score", "base_weight", "effective_weight",
                            "contribution", "available", "reason"}
    for comp_name, comp in r_all["components"].items():
        actual_fields = set(comp.keys())
        check(expected_comp_fields == actual_fields,
              f"CONF.all3.{comp_name} has correct fields",
              f"expected {expected_comp_fields}, got {actual_fields}")
        assert_type(comp["available"], bool,
                    f"CONF.all3.{comp_name}.available is bool")
        assert_in_range(comp["score"],            0, 100,
                        f"CONF.all3.{comp_name}.score in [0,100]")
        assert_in_range(comp["effective_weight"], 0, 1,
                        f"CONF.all3.{comp_name}.effective_weight in [0,1]")
        assert_in_range(comp["contribution"],     0, 100,
                        f"CONF.all3.{comp_name}.contribution in [0,100]")

    # ─ 15. available_components и reason присутствуют ────────────────────────
    check("available_components" in r_all,
          "CONF.all3 has available_components key")
    check("reason" in r_all,
          "CONF.all3 has reason key")

    print(f"\n     all3={r_all['confidence']}, "
          f"no_bq={r_no_bq['confidence']}, "
          f"no_vq={r_no_vq['confidence']}, "
          f"no_tq={r_no_tq['confidence']}, "
          f"none={r_none['confidence']}")
    print(f"     only_tq={r_only_tq['confidence']}, "
          f"only_vq={r_only_vq['confidence']}, "
          f"only_bq={r_only_bq['confidence']}")


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIDENCE — НЕВАЛИДНЫЕ ВХОДНЫЕ ДАННЫЕ
# ══════════════════════════════════════════════════════════════════════════════

def test_confidence_invalid_inputs():
    section("CONFIDENCE — невалидные входные данные (None/NaN/inf/clamp/type)")

    # ─ score = None (ключ присутствует, значение None) ────────────────────────
    r = calculate_confidence({"trend_quality_score": None}, {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is False,
          "INV.score_None → available==False",
          f"got {r['components']['trend_quality']['available']}")
    assert_approx(r["confidence"], 0.0, "INV.score_None → confidence==0", tol=0.01)
    assert_no_nan_inf(r["confidence"], "INV.score_None → confidence finite")

    # ─ score = NaN ───────────────────────────────────────────────────────────
    r = calculate_confidence({"trend_quality_score": float("nan")}, {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is False,
          "INV.score_NaN → available==False",
          f"got {r['components']['trend_quality']['available']}")
    assert_no_nan_inf(r["confidence"], "INV.score_NaN → confidence finite")
    assert_in_range(r["confidence"], 0, 100, "INV.score_NaN → confidence in [0,100]")

    # ─ score = inf ───────────────────────────────────────────────────────────
    r = calculate_confidence({"trend_quality_score": float("inf")}, {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is False,
          "INV.score_inf → available==False",
          f"got {r['components']['trend_quality']['available']}")
    assert_no_nan_inf(r["confidence"], "INV.score_inf → confidence finite")
    assert_in_range(r["confidence"], 0, 100, "INV.score_inf → confidence in [0,100]")

    # ─ score = -inf ──────────────────────────────────────────────────────────
    r = calculate_confidence({"trend_quality_score": float("-inf")}, {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is False,
          "INV.score_neg_inf → available==False",
          f"got {r['components']['trend_quality']['available']}")
    assert_no_nan_inf(r["confidence"], "INV.score_neg_inf → confidence finite")

    # ─ score < 0 → clamp до 0, компонент ДОСТУПЕН ────────────────────────────
    r = calculate_confidence({"trend_quality_score": -20.0}, {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is True,
          "INV.score_neg → available==True (clamped)",
          f"got {r['components']['trend_quality']['available']}")
    check(r["components"]["trend_quality"]["score"] == 0.0,
          "INV.score_neg → score clamped to 0.0",
          f"got {r['components']['trend_quality']['score']}")
    assert_approx(r["confidence"], 0.0,
                  "INV.score_neg → confidence==0 (clamped)", tol=0.01)

    # ─ score > 100 → clamp до 100, компонент ДОСТУПЕН ────────────────────────
    r = calculate_confidence({"trend_quality_score": 150.0}, {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is True,
          "INV.score_over100 → available==True (clamped)",
          f"got {r['components']['trend_quality']['available']}")
    check(r["components"]["trend_quality"]["score"] == 100.0,
          "INV.score_over100 → score clamped to 100.0",
          f"got {r['components']['trend_quality']['score']}")
    assert_approx(r["confidence"], 100.0,
                  "INV.score_over100 → confidence==100", tol=0.01)

    # ─ неверный тип: int ─────────────────────────────────────────────────────
    r = calculate_confidence(42, {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is False,
          "INV.non_dict_int → available==False",
          f"got {r['components']['trend_quality']['available']}")
    assert_approx(r["confidence"], 0.0, "INV.non_dict_int → confidence==0", tol=0.01)

    # ─ неверный тип: str ─────────────────────────────────────────────────────
    r = calculate_confidence("bad_input", {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is False,
          "INV.non_dict_str → available==False",
          f"got {r['components']['trend_quality']['available']}")

    # ─ неверный тип: list ────────────────────────────────────────────────────
    r = calculate_confidence([1, 2, 3], {}, breakout_quality=None)
    check(r["components"]["trend_quality"]["available"] is False,
          "INV.non_dict_list → available==False",
          f"got {r['components']['trend_quality']['available']}")

    # ─ effective_weight отсутствующих компонентов == 0.0 ─────────────────────
    r_no_bq = calculate_confidence(
        {"trend_quality_score": 80},
        {"volume_score": 60},
        breakout_quality=None,
    )
    check(r_no_bq["components"]["breakout_quality"]["effective_weight"] == 0.0,
          "INV.absent_bq.effective_weight == 0.0",
          f"got {r_no_bq['components']['breakout_quality']['effective_weight']}")
    check(r_no_bq["components"]["volume_quality"]["effective_weight"] > 0.0,
          "INV.present_vq.effective_weight > 0",
          f"got {r_no_bq['components']['volume_quality']['effective_weight']}")

    # ─ сумма effective_weight доступных = 1.0 ────────────────────────────────
    eff_sum = sum(
        c["effective_weight"]
        for c in r_no_bq["components"].values()
        if c["available"]
    )
    assert_approx(eff_sum, 1.0,
                  "INV.no_bq.sum(available effective_weight)==1.0", tol=1e-6)

    # ─ сумма contributions == confidence ─────────────────────────────────────
    contrib_sum = sum(c["contribution"] for c in r_no_bq["components"].values())
    assert_approx(contrib_sum, r_no_bq["confidence"],
                  "INV.no_bq.sum(contribution)≈confidence", tol=1.0)

    # ─ confidence всегда в [0, 100] ──────────────────────────────────────────
    for tag, args in [
        ("score_neg",   ({"trend_quality_score": -20},  {},                   None)),
        ("score_over",  ({"trend_quality_score": 150},  {},                   None)),
        ("nan_input",   ({"trend_quality_score": float("nan")}, {},           None)),
        ("all_missing", ({},                             {},                   None)),
        ("non_dict",    (42,                             {},                   None)),
    ]:
        r_t = calculate_confidence(*args)
        assert_in_range(r_t["confidence"], 0, 100,
                        f"INV.{tag}.confidence in [0,100]")

    # ─ label соответствует итоговому confidence ───────────────────────────────
    label_edge_cases = [
        (0,   "LOW"),
        (39,  "LOW"),
        (40,  "MEDIUM"),
        (69,  "MEDIUM"),
        (70,  "HIGH"),
        (84,  "HIGH"),
        (85,  "VERY HIGH"),
        (100, "VERY HIGH"),
    ]
    for val, expected in label_edge_cases:
        r_l = calculate_confidence(
            {"trend_quality_score": val},
            {"volume_score":        val},
            breakout_quality={"breakout_score": val},
        )
        assert_approx(r_l["confidence"], float(val),
                      f"INV.label_math({val})", tol=0.5)
        check(r_l["label"] == expected,
              f"INV.label({val})=={expected!r}",
              f"got {r_l['label']!r}")

    # ─ базовые WEIGHTS в сумме = 1.0 ─────────────────────────────────────────
    w_sum = sum(v for v in WEIGHTS.values() if v > 0.0)
    check(abs(w_sum - 1.0) < 1e-9,
          f"INV.WEIGHTS_sum==1.0  (sum={w_sum:.6f})",
          f"got {w_sum}")

    print(f"\n     Все тесты невалидных входов выполнены.")


# ══════════════════════════════════════════════════════════════════════════════
#  FORMAT COMPATIBILITY
# ══════════════════════════════════════════════════════════════════════════════

def test_format_compatibility():
    section("FORMAT COMPATIBILITY — ожидаемые ключи словарей")

    df = make_baseline_df(n=25)
    line = descending_line()
    set_candle(df, 22, open_=97.5, high=98.9, low=97.0, close=98.0)
    set_candle(df, 23, open_=98.2, high=99.6, low=98.0, close=99.5)
    set_candle(df, 24, open_=99.5, high=99.8, low=99.3, close=99.6)

    tq = calc_trend_quality(df, line)
    vq = score_volume(df)
    bq = calculate_breakout_quality(df, line, "LONG")

    check("trend_quality_score" in tq,
          "COMPAT.trend_quality → trend_quality_score", f"keys={list(tq.keys())}")
    check("volume_score" in vq,
          "COMPAT.volume_quality → volume_score",       f"keys={list(vq.keys())}")
    check("breakout_score" in bq,
          "COMPAT.breakout_quality → breakout_score",   f"keys={list(bq.keys())}")

    # calculate_confidence читает все три ключа без ошибок
    try:
        r = calculate_confidence(tq, vq, breakout_quality=bq)
        check(True, "COMPAT.confidence reads all 3 without error")
        assert_in_range(r["confidence"], 0, 100, "COMPAT.confidence in [0,100]")
    except Exception as e:
        check(False, "COMPAT.confidence reads all 3 without error", str(e))

    # confidence не падает при любом отсутствующем компоненте
    for scenario, args in [
        ("missing_vq",  (tq, {},   bq)),
        ("missing_tq",  ({}, vq,   bq)),
        ("missing_bq",  (tq, vq,   None)),
        ("missing_all", ({}, {},   None)),
    ]:
        try:
            r2 = calculate_confidence(*args)
            check(True,  f"COMPAT.{scenario} no crash")
            assert_in_range(r2["confidence"], 0, 100,
                            f"COMPAT.{scenario}.confidence in [0,100]")
        except Exception as e:
            check(False, f"COMPAT.{scenario} no crash", str(e))

    # проверяем наличие ключа base_weight (не weight) в components
    r_full = calculate_confidence(tq, vq, breakout_quality=bq)
    for comp_name, comp in r_full["components"].items():
        check("base_weight" in comp,
              f"COMPAT.{comp_name} has base_weight (not weight)",
              f"keys={list(comp.keys())}")
        check("available" in comp,
              f"COMPAT.{comp_name} has available",
              f"keys={list(comp.keys())}")


# ══════════════════════════════════════════════════════════════════════════════
#  ИТОГОВЫЙ ОТЧЁТ
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("  TREND SCANNER v0.4 — VALIDATION TEST SUITE")
    print(f"  WEIGHTS: TQ={WEIGHTS['trend_quality']}, "
          f"VQ={WEIGHTS['volume_quality']}, "
          f"BQ={WEIGHTS['breakout_quality']}")
    print("═" * 60)

    suites = [
        ("SCENARIO A — quality LONG",         test_scenario_a),
        ("SCENARIO B — fake LONG",             test_scenario_b),
        ("SCENARIO C — quality SHORT",         test_scenario_c),
        ("SCENARIO D — no crossing",           test_scenario_d),
        ("SCENARIO E — insufficient data",     test_scenario_e),
        ("SCENARIO F — line=None",             test_scenario_f),
        ("SCENARIO G — no volume column",      test_scenario_g),
        ("RANGES & TYPES",                     test_assert_ranges_and_types),
        ("SYMMETRY LONG/SHORT",                test_assert_symmetry),
        ("TREND QUALITY",                      test_trend_quality),
        ("VOLUME QUALITY",                     test_volume_quality),
        ("MUTABLE DEFAULTS",                   test_no_mutable_defaults),
        ("CONFIDENCE (3 components)",          test_confidence),
        ("CONFIDENCE INVALID INPUTS",          test_confidence_invalid_inputs),
        ("FORMAT COMPATIBILITY",               test_format_compatibility),
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
    print("  VALIDATION REPORT")
    print("═" * 60)
    for suite_name, suite_ok in suite_results:
        mark = "✓" if suite_ok else "✗"
        print(f"  {mark}  {suite_name}")

    print(f"\n  Tests passed: {n_pass}/{total}")

    if _failed:
        print("\n  Failed checks:")
        for f in _failed:
            print(f"    ✗  {f}")

    print()
    status = "PASSED" if not _failed else "FAILED"
    print(f"  VALIDATION STATUS: {status}")
    print()
    print(f"  Confidence weights:")
    print(f"    • trend_quality:    {WEIGHTS['trend_quality']}")
    print(f"    • volume_quality:   {WEIGHTS['volume_quality']}")
    print(f"    • breakout_quality: {WEIGHTS['breakout_quality']}")
    renorm_status  = "PASSED" if any(
        "CONFIDENCE (3" in s for s, passed in suite_results if passed
    ) else "FAILED"
    invalid_status = "PASSED" if any(
        "INVALID" in s.upper() for s, passed in suite_results if passed
    ) else "FAILED"
    print(f"  Renormalization tests:  {renorm_status}")
    print(f"  Invalid input tests:    {invalid_status}")
    print(f"  Ready for pipeline integration: {'YES' if not _failed else 'NO'}")
    print("═" * 60 + "\n")

    sys.exit(0 if not _failed else 1)


if __name__ == "__main__":
    main()
