"""
test_v04.py
~~~~~~~~~~~
Валидационные тесты для Trend Scanner v0.4.

Проверяет независимые модули ДО их интеграции в confidence.py:
    • trend_quality.py       → calc_trend_quality()
    • volume_quality.py      → score_volume()
    • breakout_quality.py    → calculate_breakout_quality()
    • confidence.py          → calculate_confidence()

НЕ изменяет, НЕ импортирует scanner.py / analysis.py / multi_tf.py / app.py.
Запуск: python test_v04.py
"""

import sys
import math
import traceback

import pandas as pd
import numpy as np

# ── импорты тестируемых модулей ───────────────────────────────────────────────
from trend_quality    import calc_trend_quality
from volume_quality   import score_volume
from breakout_quality import calculate_breakout_quality
from confidence       import calculate_confidence, WEIGHTS


# ══════════════════════════════════════════════════════════════════════════════
#  Вспомогательные инструменты
# ══════════════════════════════════════════════════════════════════════════════

_passed: list[str] = []
_failed: list[str] = []


def ok(name: str, detail: str = "") -> None:
    tag = f"PASS  {name}"
    if detail:
        tag += f"  [{detail}]"
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
    r = f"got {val!r}, expected [{lo}, {hi}]"
    return check(lo <= val <= hi, name, r, f"{val}")


def assert_no_nan_inf(val, name: str) -> bool:
    if isinstance(val, float):
        return check(math.isfinite(val), name, f"got {val!r} (NaN or Inf)")
    return check(True, name)


def assert_type(val, typ, name: str) -> bool:
    return check(isinstance(val, typ), name, f"expected {typ.__name__}, got {type(val).__name__}")


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
    """
    Создаёт однородный DataFrame с n строк вокруг base_price.
    Последние две строки остаются для перезаписи в конкретных сценариях.
    """
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
    """Перезаписывает свечу в строке idx."""
    df.at[idx, "open"]   = open_
    df.at[idx, "high"]   = high
    df.at[idx, "low"]    = low
    df.at[idx, "close"]  = close
    df.at[idx, "volume"] = volume


def descending_line(x1: int = 0, y1: float = 110.0,
                    x2: int = 20, y2: float = 100.0) -> dict:
    """Нисходящая линия сопротивления (slope < 0)."""
    slope = (y2 - y1) / (x2 - x1)
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "slope": slope}


def ascending_line(x1: int = 0, y1: float = 90.0,
                   x2: int = 20, y2: float = 100.0) -> dict:
    """Восходящая линия поддержки (slope > 0)."""
    slope = (y2 - y1) / (x2 - x1)
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "slope": slope}


def line_val(line: dict, x: int) -> float:
    """Inline line_value для расчётов в тестах без импорта trendlines."""
    return line["y1"] + line["slope"] * (x - line["x1"])


# ══════════════════════════════════════════════════════════════════════════════
#  СЦЕНАРИЙ A — качественный LONG-пробой
# ══════════════════════════════════════════════════════════════════════════════

def test_scenario_a():
    section("СЦЕНАРИЙ A — качественный LONG-пробой")

    df = make_baseline_df(n=25, base_price=97.5, avg_volume=1000.0)
    # signal_idx = len-2 = 23, prev_idx = 22
    line = descending_line(x1=0, y1=110.0, x2=20, y2=100.0)  # slope = -0.5
    # line_val at 22 = 99.0;  line_val at 23 = 98.5

    # prev_close = 98.0 < 99.0 → ниже линии ✓
    set_candle(df, 22, open_=97.5, high=98.9, low=97.0, close=98.0, volume=950.0)
    # signal_close = 99.5 > 98.5 → выше линии ✓
    # body=1.3, range=1.6, body_ratio=0.8125 → score_body=20
    # upper_wick=0.1, wick_ratio=0.0625 → score_wick=20
    set_candle(df, 23, open_=98.2, high=99.6, low=98.0, close=99.5, volume=1800.0)
    # row 24 = placeholder (незакрытая свеча)
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

    # volume: current=row[-1]=row24=400, avg=rows4-23 (mostly 1000)
    # Лучше тестировать volume отдельно с df_vol где last row = signal
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
    line = descending_line(x1=0, y1=110.0, x2=20, y2=100.0)  # slope=-0.5
    # line at 22=99.0, line at 23=98.5

    # prev_close = 98.8 < 99.0 ✓ (пересечение по факту есть)
    set_candle(df, 22, open_=98.5, high=99.1, low=98.2, close=98.8, volume=900.0)
    # signal_close = 98.6 > 98.5 — едва выше, маленькое тело, большая верхняя тень
    # open=98.55, close=98.6 → body=0.05, range=1.1
    # high=99.5 → upper_wick=99.5-98.6=0.9, wick_ratio=0.9/1.1≈0.818 → score_wick=0
    # body_ratio=0.05/1.1≈0.045 → score_body=0
    set_candle(df, 23, open_=98.55, high=99.5, low=98.4, close=98.6, volume=600.0)
    set_candle(df, 24, open_=98.6, high=98.9, low=98.4, close=98.7, volume=400.0)

    r = calculate_breakout_quality(df, line, "LONG")

    check(r["confirmed"] is False,   "B.confirmed == False",
          f"got confirmed={r['confirmed']}, score={r['breakout_score']}")
    assert_in_range(r["breakout_score"],           0, 49.9, "B.breakout_score < 50")
    assert_in_range(r["components"]["candle_body"], 0,  5.0, "B.candle_body small (≤5)")
    assert_in_range(r["components"]["rejection_wick"], 0, 5.0, "B.rejection_wick low (≤5)")
    assert_type(r["confirmed"], bool, "B.confirmed is bool")

    # объём ниже среднего → volume_score низкий
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
    line = ascending_line(x1=0, y1=90.0, x2=20, y2=100.0)  # slope=0.5
    # line at 22 = 101.0, line at 23 = 101.5

    # prev_close = 101.5 > 101.0 ✓ (выше линии)
    set_candle(df, 22, open_=101.8, high=102.2, low=101.2, close=101.5, volume=950.0)
    # signal_close = 100.5 < 101.5 ✓ (ниже линии)
    # body=1.0, range=1.5, body_ratio=0.667 → score≈16.7
    # lower_wick=min(101.5,100.5)-100.3=0.2, wick_ratio=0.133 → score≈18
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
    line = descending_line(x1=0, y1=110.0, x2=20, y2=100.0)  # line at 23=98.5
    # Обе свечи закрываются ниже линии → LONG cross = 0
    set_candle(df, 22, open_=96.5, high=97.5, low=96.0, close=97.0)
    set_candle(df, 23, open_=97.0, high=98.0, low=96.8, close=97.8)  # 97.8 < 98.5
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
    df2  = make_baseline_df(n=2)   # только 2 строки

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
#  ASSERT-ПРОВЕРКИ ОБЩЕГО ХАРАКТЕРА
# ══════════════════════════════════════════════════════════════════════════════

def test_assert_ranges_and_types():
    section("ASSERT — диапазоны, типы, ключи компонентов")

    df = make_baseline_df(n=25, base_price=97.0)
    line = descending_line()
    set_candle(df, 22, open_=97.5, high=98.9, low=97.0, close=98.0)
    set_candle(df, 23, open_=98.2, high=99.6, low=98.0, close=99.5, volume=1800.0)
    set_candle(df, 24, open_=99.5, high=99.8, low=99.3, close=99.6)

    r = calculate_breakout_quality(df, line, "LONG")

    # все score в [0, 100]
    assert_in_range(r["breakout_score"], 0, 100, "ASSERT.breakout_score in [0,100]")
    for comp_name, comp_val in r["components"].items():
        assert_in_range(comp_val, 0, 30,
                        f"ASSERT.component.{comp_name} in [0,30]")

    # нет NaN / inf
    assert_no_nan_inf(r["breakout_score"], "ASSERT.breakout_score not NaN/Inf")
    for comp_name, comp_val in r["components"].items():
        assert_no_nan_inf(comp_val, f"ASSERT.component.{comp_name} not NaN/Inf")

    # confirmed — bool
    assert_type(r["confirmed"], bool, "ASSERT.confirmed type==bool")

    # components содержит ожидаемые ключи
    expected_comp_keys = {"cross", "close_distance", "candle_body", "rejection_wick"}
    actual_keys = set(r["components"].keys())
    check(expected_comp_keys == actual_keys,
          "ASSERT.components keys correct",
          f"expected {expected_comp_keys}, got {actual_keys}")

    # invalid direction → ValueError
    try:
        calculate_breakout_quality(df, line, "SIDEWAYS")
        check(False, "ASSERT.invalid_direction raises ValueError",
              "no exception raised")
    except ValueError:
        check(True, "ASSERT.invalid_direction raises ValueError")
    except Exception as e:
        check(False, "ASSERT.invalid_direction raises ValueError",
              f"got {type(e).__name__}: {e}")


def test_assert_symmetry():
    section("ASSERT — LONG и SHORT обрабатываются симметрично")

    # Один и тот же DataFrame, симметричные сценарии
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

    # Оба имеют cross=30
    check(r_long["components"]["cross"]  == 30.0,
          "ASSERT.symmetry LONG cross==30",
          f"got {r_long['components']['cross']}")
    check(r_short["components"]["cross"] == 30.0,
          "ASSERT.symmetry SHORT cross==30",
          f"got {r_short['components']['cross']}")

    print(f"     LONG score={r_long['breakout_score']}, "
          f"SHORT score={r_short['breakout_score']}")


# ══════════════════════════════════════════════════════════════════════════════
#  TREND QUALITY — ключи и диапазон
# ══════════════════════════════════════════════════════════════════════════════

def test_trend_quality():
    section("TREND QUALITY — ключи и диапазон 0–100")

    # Базовый тест: длинная линия касающаяся свечей
    n = 60
    closes = np.linspace(100.0, 110.0, n)
    df = pd.DataFrame({
        "open":   closes - 0.3,
        "high":   closes + 0.5,
        "low":    closes - 0.5,
        "close":  closes,
        "volume": np.full(n, 1000.0),
    })
    # Линия идёт примерно по ценам — много касаний
    line = {
        "x1": 5, "y1": closes[5],
        "x2": 50, "y2": closes[50],
        "slope": (closes[50] - closes[5]) / (50 - 5),
    }

    result = calc_trend_quality(df, line)

    # Ключ trend_quality_score присутствует
    check("trend_quality_score" in result,
          "TQ.key trend_quality_score present",
          f"keys={list(result.keys())}")
    assert_in_range(result["trend_quality_score"], 0, 100,
                    "TQ.trend_quality_score in [0,100]")
    assert_no_nan_inf(float(result["trend_quality_score"]),
                      "TQ.trend_quality_score not NaN/Inf")

    # Все субоценки в диапазоне
    for k in ("touches_score", "length_score", "angle_score", "freshness_score"):
        assert_in_range(result[k], 0, 100, f"TQ.{k} in [0,100]")

    # line=None → нули
    r_none = calc_trend_quality(df, None)
    check(r_none["trend_quality_score"] == 0,
          "TQ.none → score==0",
          f"got {r_none['trend_quality_score']}")

    print(f"     result={result}")


# ══════════════════════════════════════════════════════════════════════════════
#  VOLUME QUALITY — ключи и диапазон
# ══════════════════════════════════════════════════════════════════════════════

def test_volume_quality():
    section("VOLUME QUALITY — ключи и диапазон 0–100")

    df = make_baseline_df(n=25, avg_volume=1000.0)
    # Последняя строка — текущий объём
    df.at[24, "volume"] = 1700.0   # ratio ≈ 1.7 → score=100

    result = score_volume(df)

    check("volume_score" in result,
          "VQ.key volume_score present",
          f"keys={list(result.keys())}")
    assert_in_range(result["volume_score"], 0, 100, "VQ.volume_score in [0,100]")
    assert_no_nan_inf(float(result["volume_score"]), "VQ.volume_score not NaN/Inf")

    # Высокий объём → score = 100
    check(result["volume_score"] == 100,
          "VQ.high_volume → score==100",
          f"got {result['volume_score']}, ratio={result['volume_ratio']}")

    # Недостаточно данных → score=0, не падает
    df_short = make_baseline_df(n=5)
    r_short = score_volume(df_short, period=20)
    check(not r_short.get("reason") or True,  # может вернуть reason или просто 0
          "VQ.insufficient_data no crash", "crashed")
    assert_in_range(r_short["volume_score"], 0, 0, "VQ.insufficient_data score==0")

    print(f"     result={result}")


# ══════════════════════════════════════════════════════════════════════════════
#  MUTABLE DEFAULT ARGUMENTS — проверка
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

    all_clean = True
    for mod_name, func_name in modules_to_check:
        mod = sys.modules.get(mod_name) or __import__(mod_name)
        func = getattr(mod, func_name, None)
        if func is None:
            fail(f"MD.{func_name} found", "function not found")
            all_clean = False
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
            all_clean = False
        else:
            ok(f"MD.{func_name} no mutable defaults")

    return all_clean


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIDENCE — ключи, диапазон, перенормировка весов
# ══════════════════════════════════════════════════════════════════════════════

def test_confidence():
    section("CONFIDENCE — ключи, диапазон, WEIGHTS, перенормировка")

    tq_full = {"trend_quality_score":  80}
    vq_full = {"volume_score":         60}
    bq_full = {"breakout_score":       70}

    # 1. Оба компонента присутствуют
    r = calculate_confidence(tq_full, vq_full)
    assert_in_range(r["confidence"], 0, 100, "CONF.full.confidence in [0,100]")
    check("label"      in r, "CONF.full.label key present")
    check("components" in r, "CONF.full.components key present")
    assert_no_nan_inf(float(r["confidence"]), "CONF.full.confidence not NaN/Inf")
    # Ожидаем: (80*0.70 + 60*0.30) / 1.0 = 74
    check(r["confidence"] == 74,
          "CONF.full.confidence == 74",
          f"got {r['confidence']}")

    # 2. trend_quality отсутствует (пустой dict)
    # volume_score=60, только он, перенормировка → confidence = 60
    r2 = calculate_confidence({}, vq_full)
    check(r2["confidence"] == 60,
          "CONF.renorm_no_trend.confidence == 60 (renormalized)",
          f"got {r2['confidence']}, renormalized={r2.get('renormalized')}")
    check(r2.get("renormalized") is True,
          "CONF.renorm_no_trend.renormalized == True",
          f"got {r2.get('renormalized')}")
    assert_in_range(r2["confidence"], 0, 100,
                    "CONF.renorm_no_trend.confidence in [0,100]")

    # 3. volume_quality отсутствует (None)
    # trend_quality_score=80, только он → confidence = 80
    r3 = calculate_confidence(tq_full, None)
    check(r3["confidence"] == 80,
          "CONF.renorm_no_volume.confidence == 80 (renormalized)",
          f"got {r3['confidence']}, renormalized={r3.get('renormalized')}")
    check(r3.get("renormalized") is True,
          "CONF.renorm_no_volume.renormalized == True",
          f"got {r3.get('renormalized')}")

    # 4. Оба отсутствуют → confidence = 0
    r4 = calculate_confidence({}, {})
    check(r4["confidence"] == 0,
          "CONF.both_absent.confidence == 0",
          f"got {r4['confidence']}")

    # 5. WEIGHTS sum == 1.0 (среди активных компонентов, т.е. > 0.0)
    active_weights = {k: v for k, v in WEIGHTS.items() if v > 0.0}
    w_sum = sum(active_weights.values())
    check(abs(w_sum - 1.0) < 1e-9,
          f"CONF.WEIGHTS_sum == 1.0 (active)",
          f"sum={w_sum:.6f}, active={active_weights}")

    # 6. confidence label корректен
    label_cases = [(0, "LOW"), (39, "LOW"), (40, "MEDIUM"), (69, "MEDIUM"),
                   (70, "HIGH"), (84, "HIGH"), (85, "VERY HIGH"), (100, "VERY HIGH")]
    for val, expected in label_cases:
        r_l = calculate_confidence(
            {"trend_quality_score": val},
            {"volume_score": val},
        )
        check(r_l["label"] == expected,
              f"CONF.label({val})=={expected!r}",
              f"got {r_l['label']!r}")

    # 7. Будущий параметр breakout_quality=None не ломает вызов
    try:
        r_bq = calculate_confidence(tq_full, vq_full, breakout_quality=None)
        check(True, "CONF.breakout_quality=None no crash")
        assert_in_range(r_bq["confidence"], 0, 100,
                        "CONF.breakout_quality=None confidence in [0,100]")
    except TypeError as e:
        check(False, "CONF.breakout_quality=None no crash",
              f"TypeError: {e}")

    # 8. breakout_quality с данными (если WEIGHTS["breakout_quality"] > 0 — будущее)
    # Сейчас WEIGHTS["breakout_quality"]=0.0 → компонент игнорируется
    r_bq2 = calculate_confidence(tq_full, vq_full, breakout_quality=bq_full)
    check(r_bq2["confidence"] == 74,
          "CONF.breakout_quality=present but weight=0 → ignored (confidence==74)",
          f"got {r_bq2['confidence']}")

    print(f"     full={r['confidence']}, no_trend={r2['confidence']}, "
          f"no_volume={r3['confidence']}, both_absent={r4['confidence']}")


# ══════════════════════════════════════════════════════════════════════════════
#  FORMAT COMPATIBILITY — все модули отдают правильные ключи
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
          "COMPAT.trend_quality → trend_quality_score",
          f"keys={list(tq.keys())}")
    check("volume_score" in vq,
          "COMPAT.volume_quality → volume_score",
          f"keys={list(vq.keys())}")
    check("breakout_score" in bq,
          "COMPAT.breakout_quality → breakout_score",
          f"keys={list(bq.keys())}")

    # confidence читает эти ключи без ошибок
    try:
        r = calculate_confidence(tq, vq)
        check(True,  "COMPAT.confidence reads tq+vq without error")
        assert_in_range(r["confidence"], 0, 100,
                        "COMPAT.confidence in [0,100]")
    except Exception as e:
        check(False, "COMPAT.confidence reads tq+vq without error", str(e))

    # confidence не падает при отсутствующем компоненте
    try:
        r2 = calculate_confidence(tq, {})
        check(True,  "COMPAT.confidence survives missing volume_quality")
    except Exception as e:
        check(False, "COMPAT.confidence survives missing volume_quality", str(e))

    try:
        r3 = calculate_confidence({}, vq)
        check(True,  "COMPAT.confidence survives missing trend_quality")
    except Exception as e:
        check(False, "COMPAT.confidence survives missing trend_quality", str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  ИТОГОВЫЙ ОТЧЁТ
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("  TREND SCANNER v0.4 — VALIDATION TEST SUITE")
    print("═" * 60)

    suites = [
        ("SCENARIO A",        test_scenario_a),
        ("SCENARIO B",        test_scenario_b),
        ("SCENARIO C",        test_scenario_c),
        ("SCENARIO D",        test_scenario_d),
        ("SCENARIO E",        test_scenario_e),
        ("SCENARIO F",        test_scenario_f),
        ("SCENARIO G",        test_scenario_g),
        ("RANGES & TYPES",    test_assert_ranges_and_types),
        ("SYMMETRY",          test_assert_symmetry),
        ("TREND QUALITY",     test_trend_quality),
        ("VOLUME QUALITY",    test_volume_quality),
        ("MUTABLE DEFAULTS",  test_no_mutable_defaults),
        ("CONFIDENCE",        test_confidence),
        ("FORMAT COMPAT",     test_format_compatibility),
    ]

    suite_results: list[tuple[str, bool]] = []
    for suite_name, func in suites:
        before_fail = len(_failed)
        try:
            func()
            passed = len(_failed) == before_fail
        except Exception:
            print(f"\n  !!! EXCEPTION in {suite_name} !!!")
            traceback.print_exc()
            passed = False
            _failed.append(f"{suite_name} (EXCEPTION)")
        suite_results.append((suite_name, passed))

    total  = len(_passed) + len(_failed)
    passed = len(_passed)

    print("\n" + "═" * 60)
    print("  VALIDATION REPORT")
    print("═" * 60)
    for suite_name, suite_ok in suite_results:
        mark = "✓" if suite_ok else "✗"
        print(f"  {mark}  {suite_name}")

    print(f"\n  Tests passed: {passed}/{total}")

    if _failed:
        print("\n  Failed checks:")
        for f in _failed:
            print(f"    ✗  {f}")

    print()
    status = "PASSED" if not _failed else "FAILED"
    print(f"  VALIDATION STATUS: {status}")
    print("═" * 60 + "\n")

    sys.exit(0 if not _failed else 1)


if __name__ == "__main__":
    main()
