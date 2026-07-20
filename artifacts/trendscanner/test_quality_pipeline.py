"""
test_quality_pipeline.py
~~~~~~~~~~~~~~~~~~~~~~~~
Валидационные тесты для quality_pipeline.py.

Проверяет:
    • analyze_quality()         — анализ одного направления
    • analyze_both_directions() — оба направления + FINAL логика

НЕ изменяет существующие 250 тестов test_v04.py.
НЕ импортирует scanner.py / analysis.py / multi_tf.py / app.py.
Запуск: python test_quality_pipeline.py
"""

import sys
import math
import traceback

import numpy as np
import pandas as pd

from quality_pipeline import (
    analyze_quality,
    analyze_both_directions,
    MIN_ROWS,
    SIGNAL_THRESHOLD,
    REQUIRED_COLUMNS,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Инструменты (идентичны test_v04.py)
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

def make_long_breakout_df(quality: str = "good") -> pd.DataFrame:
    """
    DataFrame с нисходящей линией сопротивления и LONG-пробоем.

    Pivot highs:
        index 8  → high = 115.0   (первая опорная точка)
        index 20 → high = 110.0   (вторая опорная точка)
    Нисходящая линия: slope = -5/12 ≈ -0.4167
        line_at_37 = 115 - 0.4167*29 = 102.917
        line_at_38 = 115 - 0.4167*30 = 102.500

    Сигнальная свеча — индекс 38 (предпоследняя).

    quality="good"  → сильный пробой (breakout_score > 50, confirmed=True)
    quality="weak"  → слабый пробой (breakout_score < 50, confirmed=False)
    """
    n        = 40
    base     = 100.0
    avg_vol  = 1000.0

    opens  = np.full(n, base - 0.3)
    closes = np.full(n, base)
    # Линейно растущие highs → нет спонтанных pivot highs (кроме спайков)
    highs  = np.linspace(base, base + 1.0, n)
    # Линейно убывающие lows → нет спонтанных pivot lows
    lows   = np.linspace(base - 0.5, base - 1.5, n)
    vols   = np.full(n, avg_vol, dtype=float)

    # ── pivot highs ──────────────────────────────────────────────────────────
    highs[8]  = 115.0
    highs[20] = 110.0

    # ── prev candle (37) ─────────────────────────────────────────────────────
    closes[37] = 101.0    # ниже line_at_37=102.917 ✓
    opens[37]  = 100.8
    highs[37]  = 101.5
    lows[37]   = 100.5

    if quality == "good":
        # Качественный пробой: большое тело, маленькая верхняя тень
        opens[38]  = 102.0
        closes[38] = 103.5    # выше line_at_38=102.5 ✓
        highs[38]  = 104.0
        lows[38]   = 101.8
    else:
        # Слабый пробой: маленькое тело, огромная верхняя тень → score < 50
        opens[38]  = 102.0
        closes[38] = 102.6    # чуть выше line_at_38=102.5 ✓ (cross=True)
        highs[38]  = 105.0    # большая верхняя тень
        lows[38]   = 101.8

    # ── сигнальная свеча (38) с высоким объёмом ─────────────────────────────
    # score_volume(signal_index=-2) читает iloc[-2]=индекс 38, а не 39
    vols[38]   = avg_vol * 2.5    # ratio=2.5 → volume_score=100

    # ── last candle (39) — открытая незакрытая свеча ─────────────────────────
    opens[39]  = closes[38]
    closes[39] = closes[38] + 0.5
    highs[39]  = closes[38] + 1.0
    lows[39]   = closes[38] - 0.3
    vols[39]   = avg_vol          # последняя свеча: нейтральный объём

    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def make_short_breakout_df(quality: str = "good") -> pd.DataFrame:
    """
    DataFrame с восходящей линией поддержки и SHORT-пробоем.

    Pivot lows:
        index 8  → low = 85.0    (первая опорная точка)
        index 20 → low = 90.0    (вторая опорная точка)
    Восходящая линия: slope = 5/12 ≈ 0.4167
        line_at_37 = 85 + 0.4167*29 = 97.083
        line_at_38 = 85 + 0.4167*30 = 97.500

    Сигнальная свеча — индекс 38 (предпоследняя).

    quality="good" → сильный пробой (confirmed=True)
    quality="weak" → слабый пробой (confirmed=False)
    """
    n        = 40
    base     = 100.0
    avg_vol  = 1000.0

    opens  = np.full(n, base - 0.3)
    closes = np.full(n, base)
    # Линейно растущие highs → нет спонтанных pivot highs
    highs  = np.linspace(base, base + 1.0, n)
    # Линейно убывающие lows → нет спонтанных pivot lows (кроме спайков)
    lows   = np.linspace(base, base - 1.0, n)
    vols   = np.full(n, avg_vol, dtype=float)

    # ── pivot lows ───────────────────────────────────────────────────────────
    lows[8]  = 85.0
    lows[20] = 90.0

    # ── prev candle (37) ─────────────────────────────────────────────────────
    closes[37] = 98.5     # выше line_at_37=97.083 ✓
    opens[37]  = 98.7
    highs[37]  = 99.0
    lows[37]   = 98.2

    if quality == "good":
        # Качественный пробой: большое тело вниз, маленькая нижняя тень
        opens[38]  = 97.8
        closes[38] = 96.5     # ниже line_at_38=97.5 ✓
        highs[38]  = 98.2
        lows[38]   = 96.2
    else:
        # Слабый пробой: маленькое тело, огромная нижняя тень → score < 50
        opens[38]  = 97.8
        closes[38] = 97.3     # чуть ниже line_at_38=97.5 ✓ (cross=True)
        highs[38]  = 98.5
        lows[38]   = 95.0     # огромная нижняя тень

    # ── сигнальная свеча (38) с высоким объёмом ─────────────────────────────
    # score_volume(signal_index=-2) читает iloc[-2]=индекс 38
    vols[38]   = avg_vol * 2.5    # ratio=2.5 → volume_score=100

    # ── last candle (39) — открытая незакрытая свеча ─────────────────────────
    opens[39]  = closes[38]
    closes[39] = closes[38] - 0.5
    highs[39]  = closes[38] + 0.3
    lows[39]   = closes[38] - 1.0
    vols[39]   = avg_vol          # последняя свеча: нейтральный объём

    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def make_no_pivot_df(n: int = 15) -> pd.DataFrame:
    """
    DataFrame без пивотов: линейно растущие highs → нет pivot highs.
    """
    base   = 100.0
    closes = np.linspace(base, base + 1.0, n)
    highs  = np.linspace(base, base + 1.0, n)   # монотонно растут → нет пивотов
    lows   = np.linspace(base - 1.0, base, n)   # монотонно растут → нет пивотов
    opens  = closes - 0.3
    vols   = np.full(n, 1000.0)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  Вспомогательная: валидация структуры результата
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_ANALYZE_KEYS = {
    "direction", "signal", "confirmed", "line",
    "trend_quality", "volume_quality", "breakout_quality",
    "confidence", "reason",
}

EXPECTED_FINAL_KEYS = {"signal", "confidence", "label", "reason"}

VALID_SIGNALS = {"LONG", "SHORT", "WAIT"}


def assert_analyze_format(result: dict, tag: str) -> None:
    """Проверяет полноту и типы словаря analyze_quality."""
    actual_keys = set(result.keys())
    check(EXPECTED_ANALYZE_KEYS == actual_keys,
          f"{tag}.keys == expected",
          f"missing={EXPECTED_ANALYZE_KEYS - actual_keys}, "
          f"extra={actual_keys - EXPECTED_ANALYZE_KEYS}")

    check(result["signal"] in VALID_SIGNALS,
          f"{tag}.signal in VALID_SIGNALS",
          f"got {result['signal']!r}")

    assert_type(result["confirmed"], bool, f"{tag}.confirmed is bool")
    assert_type(result["reason"],    str,  f"{tag}.reason is str")

    check(result["direction"] in ("LONG", "SHORT"),
          f"{tag}.direction in ('LONG','SHORT')",
          f"got {result['direction']!r}")

    # confidence sub-dict
    conf = result.get("confidence", {})
    check(isinstance(conf, dict), f"{tag}.confidence is dict",
          f"got {type(conf).__name__}")
    if isinstance(conf, dict) and "confidence" in conf:
        c = conf["confidence"]
        assert_no_nan_inf(float(c), f"{tag}.confidence.confidence not NaN/Inf")
        assert_in_range(float(c), 0, 100, f"{tag}.confidence.confidence in [0,100]")

    # breakout_quality sub-dict
    bq = result.get("breakout_quality", {})
    if isinstance(bq, dict) and "breakout_score" in bq:
        s = bq["breakout_score"]
        assert_no_nan_inf(float(s), f"{tag}.bq.breakout_score not NaN/Inf")
        assert_in_range(float(s), 0, 100, f"{tag}.bq.breakout_score in [0,100]")


def assert_both_format(result: dict, tag: str) -> None:
    """Проверяет полноту словаря analyze_both_directions."""
    check("LONG"  in result, f"{tag}.LONG key present")
    check("SHORT" in result, f"{tag}.SHORT key present")
    check("FINAL" in result, f"{tag}.FINAL key present")

    final = result.get("FINAL", {})
    actual_final_keys = set(final.keys())
    check(EXPECTED_FINAL_KEYS <= actual_final_keys,
          f"{tag}.FINAL has required keys",
          f"missing={EXPECTED_FINAL_KEYS - actual_final_keys}")

    check(final.get("signal") in VALID_SIGNALS,
          f"{tag}.FINAL.signal in VALID_SIGNALS",
          f"got {final.get('signal')!r}")

    conf_val = final.get("confidence")
    if conf_val is not None:
        assert_no_nan_inf(float(conf_val), f"{tag}.FINAL.confidence not NaN/Inf")
        assert_in_range(float(conf_val), 0, 100, f"{tag}.FINAL.confidence in [0,100]")

    check(isinstance(final.get("label"), str), f"{tag}.FINAL.label is str")
    check(isinstance(final.get("reason"), str), f"{tag}.FINAL.reason is str")


# ══════════════════════════════════════════════════════════════════════════════
#  A. analyze_quality — направление LONG
# ══════════════════════════════════════════════════════════════════════════════

def test_quality_long():
    section("A1 — качественный LONG-пробой")

    df = make_long_breakout_df(quality="good")
    r  = analyze_quality(df, "LONG")

    assert_analyze_format(r, "A1.LONG")
    check(r["direction"] == "LONG", "A1.direction == LONG")
    check(r["signal"]    == "LONG", "A1.signal == LONG",
          f"got {r['signal']!r}, reason={r['reason']!r}")
    check(r["confirmed"] is True,   "A1.confirmed == True",
          f"got {r['confirmed']}, reason={r['reason']!r}")
    check(r["line"] is not None,    "A1.line is not None")

    bq   = r["breakout_quality"]
    conf = r["confidence"]
    check(bq.get("confirmed") is True,        "A1.bq.confirmed == True",
          f"bq_score={bq.get('breakout_score')}, reason={bq.get('reason')!r}")
    assert_in_range(float(bq.get("breakout_score", 0)), 50, 100,
                    "A1.bq.breakout_score in [50,100]")
    assert_in_range(float(conf.get("confidence", 0)), SIGNAL_THRESHOLD, 100,
                    f"A1.confidence >= {SIGNAL_THRESHOLD}")

    print(f"     signal={r['signal']}, conf={conf.get('confidence'):.1f}, "
          f"bq_score={bq.get('breakout_score')}")


def test_weak_long():
    section("A2 — слабый LONG-пробой → WAIT")

    df = make_long_breakout_df(quality="weak")
    r  = analyze_quality(df, "LONG")

    assert_analyze_format(r, "A2.LONG")
    check(r["signal"]    == "WAIT", "A2.signal == WAIT",
          f"got {r['signal']!r}, reason={r['reason']!r}")
    check(r["confirmed"] is False,  "A2.confirmed == False",
          f"bq_score={r['breakout_quality'].get('breakout_score')}")

    bq = r["breakout_quality"]
    check(bq.get("confirmed") is False, "A2.bq.confirmed == False",
          f"bq_score={bq.get('breakout_score')}")
    assert_in_range(float(bq.get("breakout_score", 0)), 0, 49.9,
                    "A2.bq.breakout_score < 50")

    print(f"     signal={r['signal']}, bq_score={bq.get('breakout_score')}, "
          f"reason={r['reason']!r}")


def test_quality_short():
    section("A3 — качественный SHORT-пробой")

    df = make_short_breakout_df(quality="good")
    r  = analyze_quality(df, "SHORT")

    assert_analyze_format(r, "A3.SHORT")
    check(r["direction"] == "SHORT", "A3.direction == SHORT")
    check(r["signal"]    == "SHORT", "A3.signal == SHORT",
          f"got {r['signal']!r}, reason={r['reason']!r}")
    check(r["confirmed"] is True,    "A3.confirmed == True",
          f"got {r['confirmed']}, reason={r['reason']!r}")
    check(r["line"] is not None,     "A3.line is not None")

    bq   = r["breakout_quality"]
    conf = r["confidence"]
    check(bq.get("confirmed") is True,       "A3.bq.confirmed == True",
          f"bq_score={bq.get('breakout_score')}, reason={bq.get('reason')!r}")
    assert_in_range(float(bq.get("breakout_score", 0)), 50, 100,
                    "A3.bq.breakout_score in [50,100]")
    assert_in_range(float(conf.get("confidence", 0)), SIGNAL_THRESHOLD, 100,
                    f"A3.confidence >= {SIGNAL_THRESHOLD}")

    print(f"     signal={r['signal']}, conf={conf.get('confidence'):.1f}, "
          f"bq_score={bq.get('breakout_score')}")


def test_weak_short():
    section("A4 — слабый SHORT-пробой → WAIT")

    df = make_short_breakout_df(quality="weak")
    r  = analyze_quality(df, "SHORT")

    assert_analyze_format(r, "A4.SHORT")
    check(r["signal"]    == "WAIT", "A4.signal == WAIT",
          f"got {r['signal']!r}, reason={r['reason']!r}")
    check(r["confirmed"] is False,  "A4.confirmed == False",
          f"bq_score={r['breakout_quality'].get('breakout_score')}")

    bq = r["breakout_quality"]
    check(bq.get("confirmed") is False, "A4.bq.confirmed == False",
          f"bq_score={bq.get('breakout_score')}")
    assert_in_range(float(bq.get("breakout_score", 0)), 0, 49.9,
                    "A4.bq.breakout_score < 50")

    print(f"     signal={r['signal']}, bq_score={bq.get('breakout_score')}, "
          f"reason={r['reason']!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  B. Edge Cases
# ══════════════════════════════════════════════════════════════════════════════

def test_no_line():
    section("B1 — нет линии (монотонный рынок, нет пивотов)")

    df = make_no_pivot_df(n=20)
    r  = analyze_quality(df, "LONG")

    assert_analyze_format(r, "B1.no_line")
    check(r["signal"]    == "WAIT", "B1.signal == WAIT")
    check(r["confirmed"] is False,  "B1.confirmed == False")
    check(r["line"] is None,        "B1.line is None",
          f"got line={r['line']}")
    check(isinstance(r["reason"], str) and len(r["reason"]) > 0,
          "B1.reason non-empty")

    # SHORT тоже
    r2 = analyze_quality(df, "SHORT")
    check(r2["signal"] == "WAIT", "B1.SHORT.signal == WAIT")
    check(r2["line"] is None,     "B1.SHORT.line is None")

    print(f"     reason={r['reason']!r}")


def test_insufficient_data():
    section("B2 — недостаточно свечей (< MIN_ROWS)")

    small_df = pd.DataFrame({
        "open":   [100.0] * 5,
        "high":   [101.0] * 5,
        "low":    [99.0]  * 5,
        "close":  [100.5] * 5,
        "volume": [1000.0] * 5,
    })

    check(len(small_df) < MIN_ROWS, f"B2.fixture len({len(small_df)}) < MIN_ROWS({MIN_ROWS})")

    for d in ("LONG", "SHORT"):
        r = analyze_quality(small_df, d)
        check(r["signal"]    == "WAIT", f"B2.{d}.signal == WAIT")
        check(r["confirmed"] is False,  f"B2.{d}.confirmed == False")
        check(isinstance(r["reason"], str) and len(r["reason"]) > 0,
              f"B2.{d}.reason non-empty")
        assert_analyze_format(r, f"B2.{d}")

    print(f"     MIN_ROWS={MIN_ROWS}, df_len=5, reason={r['reason']!r}")


def test_missing_volume_column():
    section("B3 — отсутствует колонка volume → не падает")

    df_no_vol = make_long_breakout_df(quality="good").drop(columns=["volume"])

    try:
        r = analyze_quality(df_no_vol, "LONG")
        crashed = False
    except Exception as exc:
        r       = {}
        crashed = True
        print(f"     EXCEPTION: {exc}")

    check(not crashed, "B3.no_crash — analyze_quality не упал", "raised exception")
    if not crashed:
        assert_analyze_format(r, "B3")
        check(r["signal"] in VALID_SIGNALS, "B3.signal valid",
              f"got {r.get('signal')!r}")
        # volume_score должен быть 0 (нет колонки)
        vq = r.get("volume_quality", {})
        check(vq.get("volume_score", -1) == 0,
              "B3.volume_score == 0 (no column)",
              f"got {vq.get('volume_score')!r}")
        print(f"     signal={r['signal']}, vq={vq}")


def test_invalid_direction():
    section("B4 — invalid direction → ValueError")

    df = make_long_breakout_df()

    # "long" / "short" принимаются (convert to upper internally, как в breakout_quality.py)
    for bad in ("SIDEWAYS", "BUY", "", 42, None):
        raised_ve = False
        try:
            analyze_quality(df, bad)
        except ValueError:
            raised_ve = True
        except Exception as exc:
            check(False, f"B4.{bad!r} raises ValueError",
                  f"got {type(exc).__name__}: {exc}")
            continue
        check(raised_ve, f"B4.{bad!r} raises ValueError",
              "no exception raised")

    print(f"     ValueError raised for all invalid directions")


def test_empty_df():
    section("B5 — пустой DataFrame → не падает")

    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    for d in ("LONG", "SHORT"):
        r = analyze_quality(empty_df, d)
        check(r["signal"]    == "WAIT", f"B5.{d}.signal == WAIT")
        check(r["confirmed"] is False,  f"B5.{d}.confirmed == False")

    print(f"     reason={r['reason']!r}")


def test_none_df():
    section("B6 — df=None → не падает")

    for d in ("LONG", "SHORT"):
        r = analyze_quality(None, d)
        check(r["signal"]    == "WAIT", f"B6.{d}.signal == WAIT")
        check(r["confirmed"] is False,  f"B6.{d}.confirmed == False")
        check("None" in r["reason"],    f"B6.{d}.reason mentions None",
              f"got {r['reason']!r}")

    print(f"     reason={r['reason']!r}")


def test_missing_ohlc_columns():
    section("B7 — отсутствуют OHLCV-колонки → не падает")

    # DataFrame только с volume, без OHLC
    df_bad = pd.DataFrame({"volume": [1000.0] * 20})

    for d in ("LONG", "SHORT"):
        r = analyze_quality(df_bad, d)
        check(r["signal"]    == "WAIT", f"B7.{d}.signal == WAIT")
        check(r["confirmed"] is False,  f"B7.{d}.confirmed == False")
        check(isinstance(r["reason"], str), f"B7.{d}.reason is str")

    print(f"     reason={r['reason']!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  C. analyze_both_directions
# ══════════════════════════════════════════════════════════════════════════════

def test_both_directions_long_only():
    section("C1 — analyze_both_directions: только LONG подтверждён")

    df = make_long_breakout_df(quality="good")
    r  = analyze_both_directions(df)

    assert_both_format(r, "C1")

    long_sig  = r["LONG"]["signal"]
    short_sig = r["SHORT"]["signal"]
    final_sig = r["FINAL"]["signal"]

    check(long_sig == "LONG",  "C1.LONG.signal == LONG",
          f"got {long_sig!r}")
    check(short_sig == "WAIT", "C1.SHORT.signal == WAIT",
          f"got {short_sig!r}")
    check(final_sig == "LONG", "C1.FINAL.signal == LONG",
          f"got {final_sig!r}")
    check(r["LONG"]["confirmed"],      "C1.LONG.confirmed == True")
    check(not r["SHORT"]["confirmed"], "C1.SHORT.confirmed == False")

    print(f"     LONG={long_sig}, SHORT={short_sig}, FINAL={final_sig}, "
          f"conf={r['FINAL']['confidence']}")


def test_both_directions_short_only():
    section("C2 — analyze_both_directions: только SHORT подтверждён")

    df = make_short_breakout_df(quality="good")
    r  = analyze_both_directions(df)

    assert_both_format(r, "C2")

    long_sig  = r["LONG"]["signal"]
    short_sig = r["SHORT"]["signal"]
    final_sig = r["FINAL"]["signal"]

    check(short_sig == "SHORT", "C2.SHORT.signal == SHORT",
          f"got {short_sig!r}")
    check(long_sig  == "WAIT",  "C2.LONG.signal == WAIT",
          f"got {long_sig!r}")
    check(final_sig == "SHORT", "C2.FINAL.signal == SHORT",
          f"got {final_sig!r}")
    check(r["SHORT"]["confirmed"],    "C2.SHORT.confirmed == True")
    check(not r["LONG"]["confirmed"], "C2.LONG.confirmed == False")

    print(f"     LONG={long_sig}, SHORT={short_sig}, FINAL={final_sig}, "
          f"conf={r['FINAL']['confidence']}")


def test_both_directions_wait():
    section("C3 — analyze_both_directions: ни один не подтверждён → WAIT")

    df = make_no_pivot_df(n=20)
    r  = analyze_both_directions(df)

    assert_both_format(r, "C3")

    check(r["LONG"]["signal"]  == "WAIT", "C3.LONG.signal == WAIT")
    check(r["SHORT"]["signal"] == "WAIT", "C3.SHORT.signal == WAIT")
    check(r["FINAL"]["signal"] == "WAIT", "C3.FINAL.signal == WAIT",
          f"got {r['FINAL']['signal']!r}")

    print(f"     FINAL={r['FINAL']['signal']}, reason={r['FINAL']['reason']!r}")


def test_both_directions_none_df():
    section("C4 — analyze_both_directions: df=None → не падает")

    r = analyze_both_directions(None)

    assert_both_format(r, "C4")
    check(r["FINAL"]["signal"] == "WAIT", "C4.FINAL.signal == WAIT",
          f"got {r['FINAL']['signal']!r}")

    print(f"     No crash, FINAL={r['FINAL']['signal']}")


def test_both_directions_insufficient():
    section("C5 — analyze_both_directions: недостаточно свечей → не падает")

    small_df = pd.DataFrame({
        "open":   [100.0] * 5,
        "high":   [101.0] * 5,
        "low":    [99.0]  * 5,
        "close":  [100.5] * 5,
        "volume": [1000.0] * 5,
    })

    r = analyze_both_directions(small_df)

    assert_both_format(r, "C5")
    check(r["FINAL"]["signal"] == "WAIT", "C5.FINAL.signal == WAIT",
          f"got {r['FINAL']['signal']!r}")

    print(f"     FINAL={r['FINAL']['signal']}")


# ══════════════════════════════════════════════════════════════════════════════
#  D. Логика сигнала
# ══════════════════════════════════════════════════════════════════════════════

def test_signal_logic():
    section("D1 — сигнал только при breakout_confirmed=True И confidence >= 50")

    # Качественный LONG: оба условия выполнены
    df_good = make_long_breakout_df(quality="good")
    r_good  = analyze_quality(df_good, "LONG")
    bq_conf = r_good["breakout_quality"].get("confirmed", False)
    conf_v  = r_good["confidence"].get("confidence", 0.0)

    check(bq_conf is True,       "D1.good.bq_confirmed == True")
    check(conf_v >= SIGNAL_THRESHOLD,
          f"D1.good.confidence >= {SIGNAL_THRESHOLD}",
          f"got {conf_v}")
    check(r_good["signal"] == "LONG", "D1.good.signal == LONG")

    # Слабый LONG: breakout не подтверждён
    df_weak = make_long_breakout_df(quality="weak")
    r_weak  = analyze_quality(df_weak, "LONG")
    bq_conf_weak = r_weak["breakout_quality"].get("confirmed", False)

    check(bq_conf_weak is False,   "D1.weak.bq_confirmed == False")
    check(r_weak["signal"] == "WAIT", "D1.weak.signal == WAIT (no breakout)")

    print(f"     good: bq_confirmed={bq_conf}, conf={conf_v:.1f}, signal={r_good['signal']}")
    print(f"     weak: bq_confirmed={bq_conf_weak}, signal={r_weak['signal']}")


def test_signal_threshold():
    section("D2 — SIGNAL_THRESHOLD константа корректна")

    check(isinstance(SIGNAL_THRESHOLD, (int, float)),
          "D2.SIGNAL_THRESHOLD is numeric")
    assert_in_range(SIGNAL_THRESHOLD, 0, 100, "D2.SIGNAL_THRESHOLD in [0, 100]")
    assert_approx(SIGNAL_THRESHOLD, 50.0,
                  "D2.SIGNAL_THRESHOLD == 50.0", tol=0.01)

    print(f"     SIGNAL_THRESHOLD={SIGNAL_THRESHOLD}")


def test_min_rows():
    section("D3 — MIN_ROWS константа корректна")

    check(isinstance(MIN_ROWS, int), "D3.MIN_ROWS is int")
    check(MIN_ROWS == 11,            "D3.MIN_ROWS == 11",
          f"got {MIN_ROWS}")

    # Граничный случай: ровно MIN_ROWS строк не должен падать
    df_min = pd.DataFrame({
        "open":   np.full(MIN_ROWS, 100.0),
        "high":   np.linspace(100.0, 101.0, MIN_ROWS),
        "low":    np.full(MIN_ROWS, 99.0),
        "close":  np.full(MIN_ROWS, 100.5),
        "volume": np.full(MIN_ROWS, 1000.0),
    })
    r = analyze_quality(df_min, "LONG")
    check(r["signal"] in VALID_SIGNALS, "D3.at_min_rows.no_crash")

    # MIN_ROWS - 1 строка → WAIT (недостаточно)
    df_short = df_min.iloc[:-1]
    r2 = analyze_quality(df_short, "LONG")
    check(r2["signal"] == "WAIT", f"D3.min_rows-1.signal == WAIT",
          f"got {r2['signal']}")

    print(f"     MIN_ROWS={MIN_ROWS}, at_boundary={r['signal']}, below={r2['signal']}")


# ══════════════════════════════════════════════════════════════════════════════
#  E. Формат и числовые инварианты
# ══════════════════════════════════════════════════════════════════════════════

def test_output_ranges():
    section("E1 — все score и confidence в диапазоне 0–100")

    for tag, df, direction in [
        ("LONG_good",  make_long_breakout_df("good"),  "LONG"),
        ("LONG_weak",  make_long_breakout_df("weak"),  "LONG"),
        ("SHORT_good", make_short_breakout_df("good"), "SHORT"),
        ("SHORT_weak", make_short_breakout_df("weak"), "SHORT"),
        ("no_pivot",   make_no_pivot_df(20),           "LONG"),
    ]:
        r = analyze_quality(df, direction)

        c = float(r["confidence"]["confidence"])
        assert_in_range(c, 0, 100, f"E1.{tag}.confidence in [0,100]")

        bq_s = float(r["breakout_quality"].get("breakout_score", 0))
        assert_in_range(bq_s, 0, 100, f"E1.{tag}.bq_score in [0,100]")

        tq_s = r["trend_quality"].get("trend_quality_score", 0)
        assert_in_range(int(tq_s), 0, 100, f"E1.{tag}.tq_score in [0,100]")

        vq_s = r["volume_quality"].get("volume_score", 0)
        assert_in_range(int(vq_s), 0, 100, f"E1.{tag}.vq_score in [0,100]")


def test_no_nan_inf():
    section("E2 — нет NaN/inf в числовых полях")

    fixtures = [
        ("LONG_good",  make_long_breakout_df("good"),  "LONG"),
        ("SHORT_good", make_short_breakout_df("good"), "SHORT"),
        ("no_pivot",   make_no_pivot_df(20),           "LONG"),
    ]

    for tag, df, direction in fixtures:
        r = analyze_quality(df, direction)

        assert_no_nan_inf(float(r["confidence"]["confidence"]),
                          f"E2.{tag}.confidence not NaN/Inf")
        assert_no_nan_inf(float(r["breakout_quality"]["breakout_score"]),
                          f"E2.{tag}.bq_score not NaN/Inf")

        # Компоненты breakout
        for comp_name, comp_val in r["breakout_quality"].get("components", {}).items():
            assert_no_nan_inf(float(comp_val),
                              f"E2.{tag}.bq.{comp_name} not NaN/Inf")


def test_stable_dict_format():
    section("E3 — формат словаря стабилен при разных входах")

    inputs = [
        ("LONG_good",  make_long_breakout_df("good"),  "LONG"),
        ("LONG_weak",  make_long_breakout_df("weak"),  "LONG"),
        ("SHORT_good", make_short_breakout_df("good"), "SHORT"),
        ("no_pivot",   make_no_pivot_df(20),           "LONG"),
        ("small_df",   pd.DataFrame({
            "open":   [100.0]*5, "high": [101.0]*5,
            "low":    [99.0]*5,  "close":[100.5]*5, "volume":[1000.0]*5
        }), "LONG"),
    ]

    for tag, df, direction in inputs:
        r = analyze_quality(df, direction)
        assert_analyze_format(r, f"E3.{tag}")


def test_both_directions_format_always_complete():
    section("E4 — analyze_both_directions: формат всегда полный")

    test_cases = [
        ("long_fixture",  make_long_breakout_df("good")),
        ("short_fixture", make_short_breakout_df("good")),
        ("no_pivot",      make_no_pivot_df(20)),
        ("none_df",       None),
    ]

    for tag, df in test_cases:
        r = analyze_both_directions(df)
        assert_both_format(r, f"E4.{tag}")

        # LONG и SHORT всегда содержат полный набор ключей
        for direction in ("LONG", "SHORT"):
            assert_analyze_format(r[direction], f"E4.{tag}.{direction}")


def test_label_matches_confidence():
    section("E5 — FINAL.label соответствует FINAL.confidence")

    fixtures = [
        ("long_good",  make_long_breakout_df("good")),
        ("short_good", make_short_breakout_df("good")),
        ("no_pivot",   make_no_pivot_df(20)),
    ]

    label_ranges = {
        "LOW":       (0,  39),
        "MEDIUM":    (40, 69),
        "HIGH":      (70, 84),
        "VERY HIGH": (85, 100),
    }

    for tag, df in fixtures:
        r     = analyze_both_directions(df)
        conf  = float(r["FINAL"]["confidence"])
        label = r["FINAL"]["label"]
        lo, hi = label_ranges.get(label, (0, 100))
        check(lo <= conf <= hi,
              f"E5.{tag}.label={label!r} matches conf={conf:.1f} in [{lo},{hi}]",
              f"conf={conf}, label={label}")


def test_conflict_equal_confidence_returns_wait():
    section("E6 — равная confidence или конфликт → WAIT в FINAL")

    # В реальных данных оба направления не подтверждаются одновременно.
    # Проверяем логику FINAL на WAIT-сценарии (ни одно не подтверждено).
    df = make_no_pivot_df(n=20)
    r  = analyze_both_directions(df)

    # Ни одно направление не подтверждено → FINAL = WAIT
    check(not r["LONG"]["confirmed"],  "E6.LONG.confirmed == False")
    check(not r["SHORT"]["confirmed"], "E6.SHORT.confirmed == False")
    check(r["FINAL"]["signal"] == "WAIT", "E6.FINAL == WAIT (neither confirmed)",
          f"got {r['FINAL']['signal']!r}")

    # reason должен содержать информацию
    check(isinstance(r["FINAL"]["reason"], str) and len(r["FINAL"]["reason"]) > 0,
          "E6.FINAL.reason non-empty")

    print(f"     FINAL={r['FINAL']['signal']}, reason={r['FINAL']['reason']!r}")


def test_higher_confidence_selection():
    section("E7 — выбор направления с большей confidence")

    # Тестируем оба fixture и убеждаемся, что FINAL.signal совпадает
    # с направлением, у которого signal != WAIT (т.е. большая confidence).
    for fixture_fn, expected_final in [
        (lambda: make_long_breakout_df("good"),  "LONG"),
        (lambda: make_short_breakout_df("good"), "SHORT"),
    ]:
        df    = fixture_fn()
        r     = analyze_both_directions(df)
        final = r["FINAL"]["signal"]
        check(final == expected_final,
              f"E7.{expected_final}_fixture.FINAL.signal == {expected_final}",
              f"got {final!r}")

        # Подтверждённое направление должно иметь бо́льшую confidence
        confirmed_conf = r[expected_final]["confidence"]["confidence"]
        other_dir      = "SHORT" if expected_final == "LONG" else "LONG"
        other_conf     = r[other_dir]["confidence"]["confidence"]
        check(confirmed_conf >= other_conf,
              f"E7.{expected_final}.conf({confirmed_conf:.1f}) >= other({other_conf:.1f})")

    print(f"     Higher-confidence selection verified for LONG and SHORT fixtures")


# ══════════════════════════════════════════════════════════════════════════════
#  ИТОГОВЫЙ ОТЧЁТ
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("  TREND SCANNER — QUALITY PIPELINE VALIDATION")
    print(f"  MIN_ROWS={MIN_ROWS}  SIGNAL_THRESHOLD={SIGNAL_THRESHOLD}")
    print("═" * 60)

    suites = [
        ("A1 — Quality LONG",                    test_quality_long),
        ("A2 — Weak LONG → WAIT",                test_weak_long),
        ("A3 — Quality SHORT",                   test_quality_short),
        ("A4 — Weak SHORT → WAIT",               test_weak_short),
        ("B1 — No line (no pivots)",             test_no_line),
        ("B2 — Insufficient data",               test_insufficient_data),
        ("B3 — Missing volume column",           test_missing_volume_column),
        ("B4 — Invalid direction → ValueError",  test_invalid_direction),
        ("B5 — Empty DataFrame",                 test_empty_df),
        ("B6 — df=None",                         test_none_df),
        ("B7 — Missing OHLC columns",            test_missing_ohlc_columns),
        ("C1 — Both dirs: LONG only",            test_both_directions_long_only),
        ("C2 — Both dirs: SHORT only",           test_both_directions_short_only),
        ("C3 — Both dirs: neither → WAIT",       test_both_directions_wait),
        ("C4 — Both dirs: df=None",              test_both_directions_none_df),
        ("C5 — Both dirs: insufficient data",    test_both_directions_insufficient),
        ("D1 — Signal logic (confirmed + conf)", test_signal_logic),
        ("D2 — SIGNAL_THRESHOLD constant",       test_signal_threshold),
        ("D3 — MIN_ROWS boundary",               test_min_rows),
        ("E1 — Scores in [0, 100]",              test_output_ranges),
        ("E2 — No NaN/inf",                      test_no_nan_inf),
        ("E3 — Stable dict format",              test_stable_dict_format),
        ("E4 — Both dirs format always complete", test_both_directions_format_always_complete),
        ("E5 — Label matches confidence",        test_label_matches_confidence),
        ("E6 — Equal/no confidence → WAIT",      test_conflict_equal_confidence_returns_wait),
        ("E7 — Higher confidence selection",     test_higher_confidence_selection),
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
    print("  PIPELINE VALIDATION REPORT")
    print("═" * 60)
    for suite_name, suite_ok in suite_results:
        mark = "✓" if suite_ok else "✗"
        print(f"  {mark}  {suite_name}")

    if _failed:
        print("\n  Failed checks:")
        for f in _failed:
            print(f"    ✗  {f}")

    print(f"\n  Pipeline tests: {n_pass}/{total}")
    print()
    status = "PASSED" if not _failed else "FAILED"
    print(f"  PIPELINE VALIDATION STATUS: {status}")
    print("═" * 60 + "\n")

    sys.exit(0 if not _failed else 1)


if __name__ == "__main__":
    main()
