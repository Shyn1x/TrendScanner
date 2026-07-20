"""
test_market_structure.py
~~~~~~~~~~~~~~~~~~~~~~~~
Синтетические тесты для market_structure.py (v0.5).

Не модифицирует ничего из v0.4.
Все DataFrame — синтетические, без обращений к бирже.

Запуск:
    python test_market_structure.py

Покрывает:
    - Bullish / Bearish / Range структуры
    - TRANSITION_BULLISH / TRANSITION_BEARISH
    - BOS (bullish / bearish)
    - CHoCH (bullish / bearish)
    - BOS по направлению vs. против структуры
    - Ошибки данных (пустой df, отсутствующие колонки, NaN, нулевые цены)
    - Недостаточно пивотов → UNKNOWN
    - invalid pivot_type → ValueError
    - Equality tolerance
    - Score в диапазоне 0–100
    - Стабильный формат результата
    - Последняя незакрытая свеча НЕ используется для BOS
    - pivot_highs / pivot_lows ограничены lookback_pivots
"""

import math
import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from market_structure import (
    analyze_market_structure,
    classify_pivot_change,
    _calculate_structure_score,
    _determine_structure,
    _classify_pivot_sequence,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Инструменты отчётности
# ─────────────────────────────────────────────────────────────────────────────

_passed: list[str] = []
_failed: list[str] = []


def ok(name: str, detail: str = "") -> None:
    _passed.append(name)
    print(f"  ✓  {name}" + (f"  ({detail})" if detail else ""))


def fail(name: str, reason: str) -> None:
    _failed.append(name)
    print(f"  ✗  {name}  ←  {reason}")


def check(cond: bool, name: str, reason: str = "") -> bool:
    if cond:
        ok(name)
    else:
        fail(name, reason or "assertion failed")
    return cond


def assert_in_range(val, lo, hi, name: str) -> bool:
    return check(
        lo <= val <= hi, name,
        f"got {val!r}, expected [{lo}, {hi}]",
    )


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─────────────────────────────────────────────────────────────────────────────
#  Фабрика тестовых DataFrame
# ─────────────────────────────────────────────────────────────────────────────

def _make_zigzag_df(levels: list[float], pts_between: int = 10) -> pd.DataFrame:
    """
    Создаёт DataFrame с зигзагообразным движением цены.

    levels:      чередующиеся уровни пивотов [v1, p1, v2, p2, ...] или [p1, v1, p2, ...]
    pts_between: количество свечей между уровнями (не включая крайние точки)

    head (8 свечей) подходит к первому уровню с «нужной стороны»;
    tail (8 свечей) уходит от последнего уровня.
    """
    head_pad = 8

    # Направление подхода к первому уровню
    if len(levels) >= 2 and levels[1] > levels[0]:
        # первый уровень — долина (ценa пойдёт вверх) → подходим сверху
        head_start = levels[0] + 5
    else:
        # первый уровень — пик → подходим снизу
        head_start = levels[0] - 5
    head = np.linspace(head_start, levels[0], head_pad + 1)[:-1]  # exclude endpoint

    # Основной зигзаг
    main_parts: list[np.ndarray] = [np.array([levels[0]])]
    for i in range(1, len(levels)):
        seg = np.linspace(levels[i - 1], levels[i], pts_between + 2)[1:]  # skip start
        main_parts.append(seg)
    main = np.concatenate(main_parts)

    # Хвост: уходит в сторону последнего движения
    # Tail continues AWAY from the last level (opposite to arriving direction):
    #   last is a peak  (levels[-1] > levels[-2]) → tail goes DOWN (-1)
    #   last is a valley (levels[-1] < levels[-2]) → tail goes UP   (+1)
    if len(levels) >= 2:
        direction = -1.0 if levels[-1] > levels[-2] else 1.0
    else:
        direction = -1.0
    tail_end = levels[-1] + direction * 5
    tail = np.linspace(levels[-1], tail_end, head_pad + 1)[1:]  # skip start

    closes = np.concatenate([head, main, tail])
    n = len(closes)
    spread = 0.5
    highs = closes + spread
    lows  = closes - spread
    opens = np.roll(closes, 1)
    opens[0] = closes[0]

    return pd.DataFrame({
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": np.full(n, 1000.0),
    })


def _bullish_df() -> pd.DataFrame:
    """
    Чёткая бычья структура: долина→пик→долина→пик...
    Pivot highs: ~110, ~120, ~130  → HH, HH
    Pivot lows:  ~80,  ~88,  ~96   → HL, HL
    """
    return _make_zigzag_df([80, 110, 88, 120, 96, 130])


def _bearish_df() -> pd.DataFrame:
    """
    Чёткая медвежья структура: пик→долина→пик→долина...
    Pivot highs: ~130, ~120, ~110  → LH, LH
    Pivot lows:  ~115, ~105, ~90   → LL, LL
    """
    return _make_zigzag_df([130, 115, 120, 105, 110, 90])


def _range_df() -> pd.DataFrame:
    """
    Боковик: все пики ≈ 100 (в пределах 0.1%), все долины ≈ 99 → EH, EL.
    """
    return _make_zigzag_df([99.00, 100.00, 99.00, 100.05, 99.00, 100.03])


def _transition_bullish_df() -> pd.DataFrame:
    """
    Сначала LH+LL (медвежья фаза), затем HH (более свежий пик) → TRANSITION_BULLISH.
    """
    # долина115 → пик125(PH1) → долина110 → пик120(LH) → долина95(LL) → пик128(HH)
    # Последний HH индекс > последнего LL индекса → TRANSITION_BULLISH
    return _make_zigzag_df([115, 125, 110, 120, 95, 128])


def _transition_bearish_df() -> pd.DataFrame:
    """
    Сначала HH+HL (бычья фаза), затем LL (более свежая долина) → TRANSITION_BEARISH.
    """
    # пик105 → долина95(PL1) → пик115(HH) → долина100(HL) → пик125(HH) → долина85(LL)
    # Последний LL индекс > последнего HH индекса → TRANSITION_BEARISH
    return _make_zigzag_df([105, 95, 115, 100, 125, 85])


# ─────────────────────────────────────────────────────────────────────────────
#  Ожидаемые ключи результата
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_KEYS = {
    "structure", "structure_score", "bos", "choch",
    "last_closed_index", "pivot_highs", "pivot_lows",
    "latest_high_pattern", "latest_low_pattern", "reason",
}
EXPECTED_BOS_KEYS = {"direction", "level", "confirmed"}
VALID_STRUCTURES  = {"BULLISH", "BEARISH", "RANGE",
                     "TRANSITION_BULLISH", "TRANSITION_BEARISH", "UNKNOWN"}
VALID_BOS_DIRS    = {"BULLISH", "BEARISH", "NONE"}
VALID_CHOCH       = {"BULLISH", "BEARISH", "NONE"}


def _assert_format(r: dict, tag: str) -> bool:
    """Проверяет все обязательные ключи и типы результата."""
    ok_all = True

    missing_top = EXPECTED_KEYS - set(r.keys())
    ok_all &= check(not missing_top, f"{tag}.top_keys_present",
                    f"missing={missing_top}")

    ok_all &= check(r.get("structure") in VALID_STRUCTURES,
                    f"{tag}.structure_valid", f"got {r.get('structure')!r}")

    score = r.get("structure_score")
    ok_all &= check(isinstance(score, (int, float)) and math.isfinite(float(score)),
                    f"{tag}.score_finite", f"got {score!r}")
    if isinstance(score, (int, float)):
        ok_all &= assert_in_range(float(score), 0, 100, f"{tag}.score_in_[0,100]")

    bos = r.get("bos", {})
    missing_bos = EXPECTED_BOS_KEYS - set(bos.keys())
    ok_all &= check(not missing_bos, f"{tag}.bos_keys_present", f"missing={missing_bos}")
    ok_all &= check(bos.get("direction") in VALID_BOS_DIRS,
                    f"{tag}.bos_direction_valid", f"got {bos.get('direction')!r}")
    ok_all &= check(isinstance(bos.get("confirmed"), bool),
                    f"{tag}.bos_confirmed_bool")

    ok_all &= check(r.get("choch") in VALID_CHOCH,
                    f"{tag}.choch_valid", f"got {r.get('choch')!r}")

    ok_all &= check(isinstance(r.get("pivot_highs"), list),
                    f"{tag}.pivot_highs_is_list")
    ok_all &= check(isinstance(r.get("pivot_lows"), list),
                    f"{tag}.pivot_lows_is_list")
    ok_all &= check(isinstance(r.get("reason"), str),
                    f"{tag}.reason_is_str")

    return ok_all


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 1 — Стабильный формат результата
# ═════════════════════════════════════════════════════════════════════════════

def test_stable_format() -> None:
    section("T1 — Стабильный формат результата")

    for tag, df in [
        ("bullish", _bullish_df()),
        ("bearish", _bearish_df()),
        ("range",   _range_df()),
        ("unknown", pd.DataFrame()),
    ]:
        r = analyze_market_structure(df)
        _assert_format(r, f"T1.{tag}")

    # Каждый entry в pivot_highs / pivot_lows содержит index, price, classification
    r = analyze_market_structure(_bullish_df())
    for entry in r["pivot_highs"] + r["pivot_lows"]:
        check("index"          in entry, "T1.entry.index present")
        check("price"          in entry, "T1.entry.price present")
        check("classification" in entry, "T1.entry.classification present")
        check(isinstance(entry["index"], (int, np.integer)),
              "T1.entry.index is int", f"got {type(entry['index']).__name__}")
        check(math.isfinite(entry["price"]), "T1.entry.price finite")
        break  # достаточно одного


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 2 — Чёткая бычья структура (HH + HL)
# ═════════════════════════════════════════════════════════════════════════════

def test_bullish_structure() -> None:
    section("T2 — Чёткая бычья структура (HH + HL)")

    df = _bullish_df()
    r  = analyze_market_structure(df)

    check(r["structure"] == "BULLISH",
          "T2.structure == BULLISH", f"got {r['structure']!r}, reason={r['reason']}")

    ph_classes = [c["classification"] for c in r["pivot_highs"]]
    pl_classes = [c["classification"] for c in r["pivot_lows"]]
    non_first_h = [c for c in ph_classes if c != "FIRST"]
    non_first_l = [c for c in pl_classes if c != "FIRST"]

    check(all(c == "HH" for c in non_first_h),
          "T2.all_classified_highs == HH", f"got {non_first_h}")
    check(all(c == "HL" for c in non_first_l),
          "T2.all_classified_lows == HL",  f"got {non_first_l}")
    assert_in_range(r["structure_score"], 70, 100, "T2.score >= 70")
    print(f"     structure={r['structure']}, score={r['structure_score']}, "
          f"highs={ph_classes}, lows={pl_classes}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 3 — Чёткая медвежья структура (LH + LL)
# ═════════════════════════════════════════════════════════════════════════════

def test_bearish_structure() -> None:
    section("T3 — Чёткая медвежья структура (LH + LL)")

    df = _bearish_df()
    r  = analyze_market_structure(df)

    check(r["structure"] == "BEARISH",
          "T3.structure == BEARISH", f"got {r['structure']!r}, reason={r['reason']}")

    ph_classes = [c["classification"] for c in r["pivot_highs"]]
    pl_classes = [c["classification"] for c in r["pivot_lows"]]
    non_first_h = [c for c in ph_classes if c != "FIRST"]
    non_first_l = [c for c in pl_classes if c != "FIRST"]

    check(all(c == "LH" for c in non_first_h),
          "T3.all_classified_highs == LH", f"got {non_first_h}")
    check(all(c == "LL" for c in non_first_l),
          "T3.all_classified_lows == LL",  f"got {non_first_l}")
    assert_in_range(r["structure_score"], 70, 100, "T3.score >= 70")
    print(f"     structure={r['structure']}, score={r['structure_score']}, "
          f"highs={ph_classes}, lows={pl_classes}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 4 — Боковик (EH + EL)
# ═════════════════════════════════════════════════════════════════════════════

def test_range_structure() -> None:
    section("T4 — Боковик (EH + EL → RANGE)")

    df = _range_df()
    r  = analyze_market_structure(df)

    check(r["structure"] == "RANGE",
          "T4.structure == RANGE", f"got {r['structure']!r}, reason={r['reason']}")

    ph_classes = [c["classification"] for c in r["pivot_highs"]]
    pl_classes = [c["classification"] for c in r["pivot_lows"]]
    non_first_h = [c for c in ph_classes if c != "FIRST"]
    non_first_l = [c for c in pl_classes if c != "FIRST"]

    check(all(c == "EH" for c in non_first_h),
          "T4.all_classified_highs == EH", f"got {non_first_h}")
    check(all(c == "EL" for c in non_first_l),
          "T4.all_classified_lows == EL",  f"got {non_first_l}")
    assert_in_range(r["structure_score"], 0, 50, "T4.score in [0,50] for RANGE")
    print(f"     structure={r['structure']}, score={r['structure_score']}, "
          f"highs={non_first_h}, lows={non_first_l}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 5 — TRANSITION_BULLISH
# ═════════════════════════════════════════════════════════════════════════════

def test_transition_bullish() -> None:
    section("T5 — TRANSITION_BULLISH (HH после LL)")

    df = _transition_bullish_df()
    r  = analyze_market_structure(df)

    check(r["structure"] == "TRANSITION_BULLISH",
          "T5.structure == TRANSITION_BULLISH",
          f"got {r['structure']!r}, reason={r['reason']}")

    ph_classes = [c["classification"] for c in r["pivot_highs"]]
    pl_classes = [c["classification"] for c in r["pivot_lows"]]
    print(f"     structure={r['structure']}, score={r['structure_score']}, "
          f"highs={ph_classes}, lows={pl_classes}")

    # Последний classified high должен быть HH
    non_first_h = [c for c in ph_classes if c != "FIRST"]
    check(non_first_h[-1] == "HH" if non_first_h else False,
          "T5.latest_high == HH", f"got {non_first_h}")

    assert_in_range(r["structure_score"], 30, 80, "T5.score in [30,80] for transition")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 6 — TRANSITION_BEARISH
# ═════════════════════════════════════════════════════════════════════════════

def test_transition_bearish() -> None:
    section("T6 — TRANSITION_BEARISH (LL после HH)")

    df = _transition_bearish_df()
    r  = analyze_market_structure(df)

    check(r["structure"] == "TRANSITION_BEARISH",
          "T6.structure == TRANSITION_BEARISH",
          f"got {r['structure']!r}, reason={r['reason']}")

    pl_classes = [c["classification"] for c in r["pivot_lows"]]
    ph_classes = [c["classification"] for c in r["pivot_highs"]]
    print(f"     structure={r['structure']}, score={r['structure_score']}, "
          f"highs={ph_classes}, lows={pl_classes}")

    # Последний classified low должен быть LL
    non_first_l = [c for c in pl_classes if c != "FIRST"]
    check(non_first_l[-1] == "LL" if non_first_l else False,
          "T6.latest_low == LL", f"got {non_first_l}")

    assert_in_range(r["structure_score"], 30, 80, "T6.score in [30,80] for transition")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 7 — Bullish BOS
# ═════════════════════════════════════════════════════════════════════════════

def test_bullish_bos() -> None:
    section("T7 — Bullish BOS (close[-2] выше последнего pivot high)")

    df = _bullish_df()
    r0 = analyze_market_structure(df)

    # Получаем цену последнего пивотного хая из результата
    last_ph = r0["pivot_highs"][-1]["price"]

    # Форсируем bullish BOS: close[-2] > last_ph
    n = len(df)
    df.loc[n - 2, "close"] = last_ph + 10.0

    r = analyze_market_structure(df)

    check(r["bos"]["direction"] == "BULLISH",
          "T7.bos_direction == BULLISH", f"got {r['bos']['direction']!r}")
    check(r["bos"]["confirmed"] is True,
          "T7.bos_confirmed == True")
    check(r["bos"]["level"] is not None,
          "T7.bos_level is not None")
    print(f"     bos={r['bos']}, structure={r['structure']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 8 — Bearish BOS
# ═════════════════════════════════════════════════════════════════════════════

def test_bearish_bos() -> None:
    section("T8 — Bearish BOS (close[-2] ниже последнего pivot low)")

    df = _bearish_df()
    r0 = analyze_market_structure(df)

    # Получаем цену последнего пивотного лоу
    last_pl = r0["pivot_lows"][-1]["price"]

    # Форсируем bearish BOS: close[-2] < last_pl
    n = len(df)
    df.loc[n - 2, "close"] = last_pl - 10.0

    r = analyze_market_structure(df)

    check(r["bos"]["direction"] == "BEARISH",
          "T8.bos_direction == BEARISH", f"got {r['bos']['direction']!r}")
    check(r["bos"]["confirmed"] is True,
          "T8.bos_confirmed == True")
    check(r["bos"]["level"] is not None,
          "T8.bos_level is not None")
    print(f"     bos={r['bos']}, structure={r['structure']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 9 — Bullish CHoCH
# ═════════════════════════════════════════════════════════════════════════════

def test_bullish_choch() -> None:
    section("T9 — Bullish CHoCH (BEARISH структура + bullish BOS)")

    df = _bearish_df()
    r0 = analyze_market_structure(df)

    # Проверяем, что структура медвежья (без BOS)
    check(r0["structure"] in ("BEARISH", "TRANSITION_BEARISH"),
          "T9.base_structure is BEARISH/TRANSITION_BEARISH",
          f"got {r0['structure']!r}")

    # Форсируем bullish BOS
    last_ph = r0["pivot_highs"][-1]["price"]
    n = len(df)
    df.loc[n - 2, "close"] = last_ph + 10.0

    r = analyze_market_structure(df)

    check(r["bos"]["direction"] == "BULLISH",
          "T9.bos == BULLISH", f"got {r['bos']['direction']!r}")
    check(r["choch"] == "BULLISH",
          "T9.choch == BULLISH", f"got {r['choch']!r}")
    print(f"     structure={r['structure']}, bos={r['bos']['direction']}, choch={r['choch']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 10 — Bearish CHoCH
# ═════════════════════════════════════════════════════════════════════════════

def test_bearish_choch() -> None:
    section("T10 — Bearish CHoCH (BULLISH структура + bearish BOS)")

    df = _bullish_df()
    r0 = analyze_market_structure(df)

    check(r0["structure"] in ("BULLISH", "TRANSITION_BULLISH"),
          "T10.base_structure is BULLISH/TRANSITION_BULLISH",
          f"got {r0['structure']!r}")

    # Форсируем bearish BOS
    last_pl = r0["pivot_lows"][-1]["price"]
    n = len(df)
    df.loc[n - 2, "close"] = last_pl - 10.0

    r = analyze_market_structure(df)

    check(r["bos"]["direction"] == "BEARISH",
          "T10.bos == BEARISH", f"got {r['bos']['direction']!r}")
    check(r["choch"] == "BEARISH",
          "T10.choch == BEARISH", f"got {r['choch']!r}")
    print(f"     structure={r['structure']}, bos={r['bos']['direction']}, choch={r['choch']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 11 — BOS по направлению структуры (score +15)
# ═════════════════════════════════════════════════════════════════════════════

def test_bos_confirms_structure() -> None:
    section("T11 — BOS по направлению структуры → score +15")

    # Bullish структура + bullish BOS
    df = _bullish_df()
    r_no_bos = analyze_market_structure(df)

    last_ph = r_no_bos["pivot_highs"][-1]["price"]
    n = len(df)
    df.loc[n - 2, "close"] = last_ph + 10.0
    r_bos = analyze_market_structure(df)

    check(r_bos["structure"] in ("BULLISH", "TRANSITION_BULLISH"),
          "T11.structure bullish", f"got {r_bos['structure']!r}")
    check(r_bos["bos"]["direction"] == "BULLISH",
          "T11.bos == BULLISH")
    check(r_bos["structure_score"] >= r_no_bos["structure_score"],
          "T11.score_with_bos >= score_without_bos",
          f"with_bos={r_bos['structure_score']}, no_bos={r_no_bos['structure_score']}")
    print(f"     no_bos_score={r_no_bos['structure_score']}, "
          f"bos_score={r_bos['structure_score']}, diff={r_bos['structure_score']-r_no_bos['structure_score']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 12 — BOS против текущей структуры (score -25)
# ═════════════════════════════════════════════════════════════════════════════

def test_bos_contradicts_structure() -> None:
    section("T12 — BOS против структуры → score -25, CHoCH")

    # Bullish структура + bearish BOS
    df = _bullish_df()
    r_no_bos = analyze_market_structure(df)

    last_pl = r_no_bos["pivot_lows"][-1]["price"]
    n = len(df)
    df.loc[n - 2, "close"] = last_pl - 10.0
    r_bos = analyze_market_structure(df)

    check(r_bos["bos"]["direction"] == "BEARISH",
          "T12.bos == BEARISH")
    check(r_bos["choch"] == "BEARISH",
          "T12.choch == BEARISH", f"got {r_bos['choch']!r}")
    # Score should drop (CHoCH cap ≤ 60 AND -25 penalty)
    check(r_bos["structure_score"] < r_no_bos["structure_score"],
          "T12.score_drops_with_opposite_bos",
          f"with_bos={r_bos['structure_score']}, no_bos={r_no_bos['structure_score']}")
    assert_in_range(r_bos["structure_score"], 0, 60, "T12.score_capped_at_60 (CHoCH)")
    print(f"     no_bos_score={r_no_bos['structure_score']}, "
          f"bos_score={r_bos['structure_score']}, choch={r_bos['choch']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 13 — Недостаточно пивотов → UNKNOWN
# ═════════════════════════════════════════════════════════════════════════════

def test_insufficient_pivots() -> None:
    section("T13 — Недостаточно пивотов → UNKNOWN")

    # Слишком мало строк для pivot_window=5
    short_df = pd.DataFrame({
        "open":  [100.0] * 9,
        "high":  [101.0] * 9,
        "low":   [99.0]  * 9,
        "close": [100.0] * 9,
    })
    r = analyze_market_structure(short_df, pivot_window=5)
    check(r["structure"] == "UNKNOWN",
          "T13.short_df → UNKNOWN", f"got {r['structure']!r}")
    check(len(r["reason"]) > 0, "T13.reason non-empty")

    # Монотонно растущий df: нет локальных максимумов среди highs
    n = 30
    closes = np.linspace(100, 200, n)
    mono_df = pd.DataFrame({
        "open":  closes - 0.3,
        "high":  closes + 0.5,   # всегда растёт → нет пивот-хаев
        "low":   closes - 0.5,
        "close": closes,
    })
    r2 = analyze_market_structure(mono_df, pivot_window=5)
    check(r2["structure"] == "UNKNOWN",
          "T13.monotone_df → UNKNOWN (no pivot highs)",
          f"got {r2['structure']!r}")
    print(f"     short_df: {r['reason'][:50]}")
    print(f"     mono_df:  {r2['reason'][:50]}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 14 — Пустой DataFrame → UNKNOWN
# ═════════════════════════════════════════════════════════════════════════════

def test_empty_df() -> None:
    section("T14 — Пустой DataFrame → UNKNOWN")

    r = analyze_market_structure(pd.DataFrame())
    check(r["structure"] == "UNKNOWN",  "T14.empty_df → UNKNOWN")
    check(r["structure_score"] == 0.0,  "T14.score == 0")
    check(r["pivot_highs"] == [],       "T14.pivot_highs == []")
    check(r["pivot_lows"]  == [],       "T14.pivot_lows == []")
    check(len(r["reason"]) > 0,         "T14.reason non-empty")

    r2 = analyze_market_structure(None)
    check(r2["structure"] == "UNKNOWN", "T14.None → UNKNOWN")
    print(f"     empty: {r['reason']}, None: {r2['reason']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 15 — Отсутствующие колонки → UNKNOWN
# ═════════════════════════════════════════════════════════════════════════════

def test_missing_columns() -> None:
    section("T15 — Отсутствующие колонки → UNKNOWN")

    # Отсутствует 'high'
    df_no_high = pd.DataFrame({
        "low":   [99.0, 98.0, 97.0] * 5,
        "close": [100.0, 99.0, 98.0] * 5,
    })
    r1 = analyze_market_structure(df_no_high)
    check(r1["structure"] == "UNKNOWN", "T15.no_high → UNKNOWN")

    # Отсутствует 'low'
    df_no_low = pd.DataFrame({
        "high":  [101.0, 102.0, 103.0] * 5,
        "close": [100.0, 101.0, 102.0] * 5,
    })
    r2 = analyze_market_structure(df_no_low)
    check(r2["structure"] == "UNKNOWN", "T15.no_low → UNKNOWN")

    # Отсутствует 'close'
    df_no_close = pd.DataFrame({
        "high": [101.0, 102.0, 103.0] * 5,
        "low":  [99.0,  98.0,  97.0]  * 5,
    })
    r3 = analyze_market_structure(df_no_close)
    check(r3["structure"] == "UNKNOWN", "T15.no_close → UNKNOWN")
    print(f"     no_high: {r1['reason'][:40]}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 16 — NaN в ценах → UNKNOWN
# ═════════════════════════════════════════════════════════════════════════════

def test_nan_values() -> None:
    section("T16 — NaN/inf в ценах → graceful handling")

    # Все highs → NaN: find_pivots не найдёт пивотов → UNKNOWN
    df_nan = _bullish_df().copy()
    df_nan["high"] = float("nan")
    r = analyze_market_structure(df_nan)
    check(r["structure"] == "UNKNOWN",
          "T16.all_highs_nan → UNKNOWN", f"got {r['structure']!r}, reason={r['reason'][:50]}")

    # Нулевые цены → UNKNOWN
    df_zero = _bullish_df().copy()
    df_zero["close"] = 0.0
    df_zero["low"]   = 0.0
    r2 = analyze_market_structure(df_zero)
    check(r2["structure"] == "UNKNOWN",
          "T16.zero_prices → UNKNOWN", f"got {r2['structure']!r}")

    # Inf в одном значении — если в пивоте, должен перехватить _validate or NaN check
    df_inf = _bullish_df().copy()
    df_inf.loc[10, "high"] = float("inf")
    r3 = analyze_market_structure(df_inf)
    # Inf в одной точке может вызвать inf-пивот, который должен перехватиться
    check(r3 is not None and "structure" in r3,
          "T16.inf_value → no crash")
    print(f"     nan={r['reason'][:40]}, zero={r2['reason'][:30]}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 17 — invalid pivot_type → ValueError
# ═════════════════════════════════════════════════════════════════════════════

def test_invalid_pivot_type() -> None:
    section("T17 — classify_pivot_change: invalid pivot_type → ValueError")

    raised = False
    try:
        classify_pivot_change(100, 105, "MEDIUM")
    except ValueError as e:
        raised = True
        check("HIGH" in str(e) or "LOW" in str(e) or "pivot_type" in str(e),
              "T17.error_message_informative", f"got {e!r}")

    check(raised, "T17.ValueError_raised_for_MEDIUM")

    raised2 = False
    try:
        classify_pivot_change(100, 105, "")
    except ValueError:
        raised2 = True
    check(raised2, "T17.ValueError_raised_for_empty_string")

    # Корректные вызовы не должны поднимать
    for pt in ("HIGH", "LOW"):
        try:
            classify_pivot_change(100, 105, pt)
            ok(f"T17.valid_{pt}_no_error")
        except ValueError:
            fail(f"T17.valid_{pt}_no_error", "unexpected ValueError")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 18 — Equality tolerance
# ═════════════════════════════════════════════════════════════════════════════

def test_equality_tolerance() -> None:
    section("T18 — classify_pivot_change: equality tolerance")

    # < tolerance → Equal
    r = classify_pivot_change(100.0, 100.05, "HIGH", tolerance=0.001)
    check(r == "EH", "T18.100→100.05 with tol=0.001 → EH", f"got {r!r}")

    r2 = classify_pivot_change(100.0, 99.95, "LOW", tolerance=0.001)
    check(r2 == "EL", "T18.100→99.95 LOW with tol=0.001 → EL", f"got {r2!r}")

    # > tolerance → HH / LL
    r3 = classify_pivot_change(100.0, 100.15, "HIGH", tolerance=0.001)
    check(r3 == "HH", "T18.100→100.15 with tol=0.001 → HH (0.15% > 0.1%)", f"got {r3!r}")

    r4 = classify_pivot_change(100.0, 99.8, "LOW", tolerance=0.001)
    check(r4 == "LL", "T18.100→99.8 LOW with tol=0.001 → LL", f"got {r4!r}")

    # Строго на границе: rel_diff == tolerance → EH (<=, включительно)
    r5 = classify_pivot_change(100.0, 100.1, "HIGH", tolerance=0.001)
    check(r5 == "EH", "T18.100→100.1 at-exactly-tolerance → EH", f"got {r5!r}")

    # Тест с более широким допуском
    r6 = classify_pivot_change(100.0, 102.0, "HIGH", tolerance=0.05)
    check(r6 == "EH", "T18.100→102 with tol=0.05 → EH", f"got {r6!r}")

    # Стандартный допуск с защитой от деления на 0
    r7 = classify_pivot_change(0.0, 0.0, "HIGH", tolerance=0.001)
    check(r7 == "EH", "T18.0→0 → EH (zero denom guard)", f"got {r7!r}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 19 — Score в диапазоне 0–100
# ═════════════════════════════════════════════════════════════════════════════

def test_score_range() -> None:
    section("T19 — structure_score всегда в [0, 100]")

    test_dfs = [
        ("bullish",            _bullish_df()),
        ("bearish",            _bearish_df()),
        ("range",              _range_df()),
        ("transition_bull",    _transition_bullish_df()),
        ("transition_bear",    _transition_bearish_df()),
        ("empty",              pd.DataFrame()),
        ("short",              pd.DataFrame({"high": [1.0]*5, "low": [0.9]*5, "close": [0.95]*5})),
    ]

    for tag, df in test_dfs:
        r = analyze_market_structure(df)
        score = r["structure_score"]
        assert_in_range(score, 0, 100, f"T19.{tag}.score in [0,100]")
        check(math.isfinite(score), f"T19.{tag}.score finite")

    # Ручные вызовы _calculate_structure_score с экстремальными значениями
    high_cls = [{"classification": "FIRST"}, {"classification": "HH"},
                {"classification": "HH"},    {"classification": "HH"}]
    low_cls  = [{"classification": "FIRST"}, {"classification": "HL"},
                {"classification": "HL"},    {"classification": "HL"}]
    s = _calculate_structure_score("BULLISH", high_cls, low_cls, "BULLISH", "NONE")
    assert_in_range(s, 0, 100, "T19.manual_bullish+bos")
    s2 = _calculate_structure_score("BULLISH", high_cls, low_cls, "BEARISH", "BEARISH")
    assert_in_range(s2, 0, 60, "T19.manual_bullish+opposite_bos+choch ≤ 60")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 20 — Последняя незакрытая свеча НЕ используется для BOS
# ═════════════════════════════════════════════════════════════════════════════

def test_closed_candle_used() -> None:
    section("T20 — iloc[-1] не используется для BOS, используется iloc[-2]")

    df = _bullish_df()
    r0 = analyze_market_structure(df)
    last_ph = r0["pivot_highs"][-1]["price"]
    n = len(df)

    # Сценарий A: HIGH only at last candle (n-1) — NOT used for BOS
    df_a = df.copy()
    df_a.loc[n - 1, "close"] = last_ph + 20.0   # last candle (незакрытая)
    r_a = analyze_market_structure(df_a)
    check(r_a["bos"]["direction"] == "NONE",
          "T20.only_last_candle_high → BOS==NONE (не используется iloc[-1])",
          f"got direction={r_a['bos']['direction']}, "
          f"close[-1]={df_a['close'].iloc[-1]:.1f} > last_ph={last_ph:.1f}")

    # Сценарий B: HIGH at close[-2] — SHOULD trigger BOS
    df_b = df.copy()
    df_b.loc[n - 2, "close"] = last_ph + 10.0   # закрытая сигнальная свеча
    r_b = analyze_market_structure(df_b)
    check(r_b["bos"]["direction"] == "BULLISH",
          "T20.signal_candle_high → BOS==BULLISH",
          f"got direction={r_b['bos']['direction']}")

    # Индекс последней закрытой свечи
    check(r_b["last_closed_index"] == n - 2,
          "T20.last_closed_index == n-2",
          f"got {r_b['last_closed_index']}, expected {n-2}")

    print(f"     n={n}, last_ph={last_ph:.1f}, "
          f"A_bos={r_a['bos']['direction']}, B_bos={r_b['bos']['direction']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 21 — Списки пивотов ограничены lookback_pivots
# ═════════════════════════════════════════════════════════════════════════════

def test_lookback_pivots_limit() -> None:
    section("T21 — pivot_highs и pivot_lows ограничены lookback_pivots")

    # Создаём df с большим числом пивотов (много зигзагов)
    levels = [80, 110, 88, 120, 96, 130, 104, 140, 112, 150, 118, 160]
    df = _make_zigzag_df(levels, pts_between=10)

    for lb in [3, 4, 6]:
        r = analyze_market_structure(df, lookback_pivots=lb)
        check(len(r["pivot_highs"]) <= lb,
              f"T21.lookback={lb}: pivot_highs len ≤ {lb}",
              f"got {len(r['pivot_highs'])}")
        check(len(r["pivot_lows"]) <= lb,
              f"T21.lookback={lb}: pivot_lows len ≤ {lb}",
              f"got {len(r['pivot_lows'])}")

    # С lookback=1: только 1 пивот каждого типа → нет classified (все FIRST)
    r1 = analyze_market_structure(df, lookback_pivots=1)
    check(len(r1["pivot_highs"]) == 1, "T21.lookback=1: exactly 1 high")
    check(len(r1["pivot_lows"])  == 1, "T21.lookback=1: exactly 1 low")
    check(r1["pivot_highs"][0]["classification"] == "FIRST",
          "T21.lookback=1: high is FIRST")
    print(f"     df_pivots: highs_total≈6, lows_total≈5; lb=4 → {len(analyze_market_structure(df, lookback_pivots=4)['pivot_highs'])} highs")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 22 — latest_high_pattern и latest_low_pattern
# ═════════════════════════════════════════════════════════════════════════════

def test_pattern_strings() -> None:
    section("T22 — latest_high_pattern и latest_low_pattern")

    r = analyze_market_structure(_bullish_df())
    check(isinstance(r["latest_high_pattern"], str),
          "T22.latest_high_pattern is str")
    check(isinstance(r["latest_low_pattern"], str),
          "T22.latest_low_pattern is str")
    # Для BULLISH: последние 2 HH → паттерн содержит "HH"
    check("HH" in r["latest_high_pattern"],
          "T22.bullish_pattern contains HH", f"got {r['latest_high_pattern']!r}")
    check("HL" in r["latest_low_pattern"],
          "T22.bullish_pattern contains HL", f"got {r['latest_low_pattern']!r}")

    r2 = analyze_market_structure(_bearish_df())
    check("LH" in r2["latest_high_pattern"],
          "T22.bearish_pattern contains LH", f"got {r2['latest_high_pattern']!r}")
    check("LL" in r2["latest_low_pattern"],
          "T22.bearish_pattern contains LL", f"got {r2['latest_low_pattern']!r}")

    # UNKNOWN → паттерн = "—"
    r3 = analyze_market_structure(pd.DataFrame())
    check(r3["latest_high_pattern"] == "—",
          "T22.unknown → pattern == '—'", f"got {r3['latest_high_pattern']!r}")
    print(f"     bull: high={r['latest_high_pattern']}, low={r['latest_low_pattern']}")
    print(f"     bear: high={r2['latest_high_pattern']}, low={r2['latest_low_pattern']}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 23 — UNKNOWN не единственный HH или единственный LL
# ═════════════════════════════════════════════════════════════════════════════

def test_no_single_pivot_signal() -> None:
    section("T23 — Одиночный HH или одиночный LL не даёт BULLISH/BEARISH")

    # Только 2 pivot highs (FIRST + HH) и 1 pivot low (FIRST)
    # Создаём df, где pivot lows вырождены (один)
    n = 40
    closes_h = np.zeros(n)
    # Первая половина: плавный рост до пика
    closes_h[:20] = np.linspace(95, 100, 20)
    # Пик в середине
    closes_h[20] = 105.0
    # Затем пик2 выше
    closes_h[21:] = np.linspace(104, 108, 19)
    df = pd.DataFrame({
        "high":  closes_h + 0.5,
        "low":   closes_h - 0.5,
        "close": closes_h,
        "open":  np.roll(closes_h, 1),
    })
    df.loc[0, "open"] = closes_h[0]

    r = analyze_market_structure(df, pivot_window=3)
    # Если нет pivot lows или один — UNKNOWN; если есть — check structure is not only from HH
    if r["structure"] == "BULLISH":
        # Must have at least one HL in lows, not just HH in highs alone
        non_first_l = [c["classification"] for c in r["pivot_lows"] if c["classification"] != "FIRST"]
        check("HL" in non_first_l,
              "T23.BULLISH requires HL (not just HH)", f"lows={non_first_l}")
    else:
        check(r["structure"] in ("UNKNOWN", "RANGE", "TRANSITION_BULLISH"),
              "T23.without_clear_lows → not plain BULLISH",
              f"got {r['structure']!r}")
    print(f"     result: {r['structure']}, highs={[c['classification'] for c in r['pivot_highs']]}")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 24 — _determine_structure unit tests
# ═════════════════════════════════════════════════════════════════════════════

def test_determine_structure_units() -> None:
    section("T24 — _determine_structure unit-тесты")

    def make_cls(classes: list[str], pivot_type: str) -> list[dict]:
        result = [{"index": i*10, "price": 100.0 + i, "classification": c}
                  for i, c in enumerate(classes)]
        return result

    # HH + HL → BULLISH
    s, _ = _determine_structure(
        make_cls(["FIRST", "HH", "HH"], "HIGH"),
        make_cls(["FIRST", "HL", "HL"], "LOW"),
    )
    check(s == "BULLISH", "T24.HH+HL → BULLISH", f"got {s!r}")

    # LH + LL → BEARISH
    s2, _ = _determine_structure(
        make_cls(["FIRST", "LH", "LH"], "HIGH"),
        make_cls(["FIRST", "LL", "LL"], "LOW"),
    )
    check(s2 == "BEARISH", "T24.LH+LL → BEARISH", f"got {s2!r}")

    # EH + EL → RANGE
    s3, _ = _determine_structure(
        make_cls(["FIRST", "EH", "EH"], "HIGH"),
        make_cls(["FIRST", "EL", "EL"], "LOW"),
    )
    check(s3 == "RANGE", "T24.EH+EL → RANGE", f"got {s3!r}")

    # LH + HL → RANGE (противоречие)
    s4, _ = _determine_structure(
        make_cls(["FIRST", "LH"], "HIGH"),
        make_cls(["FIRST", "HL"], "LOW"),
    )
    check(s4 == "RANGE", "T24.LH+HL → RANGE", f"got {s4!r}")

    # HH + LL → TRANSITION (disambiguate by index)
    # HH index > LL index → TRANSITION_BULLISH
    h_cls = [{"index": 50, "price": 110.0, "classification": "FIRST"},
             {"index": 80, "price": 120.0, "classification": "HH"}]   # latest at 80
    l_cls = [{"index": 30, "price": 90.0,  "classification": "FIRST"},
             {"index": 60, "price": 80.0,  "classification": "LL"}]   # latest at 60
    s5, _ = _determine_structure(h_cls, l_cls)
    check(s5 == "TRANSITION_BULLISH",
          "T24.HH(80)+LL(60) → TRANSITION_BULLISH (HH more recent)", f"got {s5!r}")

    # LL index > HH index → TRANSITION_BEARISH
    h_cls2 = [{"index": 50, "price": 110.0, "classification": "FIRST"},
              {"index": 60, "price": 120.0, "classification": "HH"}]  # latest at 60
    l_cls2 = [{"index": 30, "price": 90.0,  "classification": "FIRST"},
              {"index": 80, "price": 80.0,  "classification": "LL"}]  # latest at 80
    s6, _ = _determine_structure(h_cls2, l_cls2)
    check(s6 == "TRANSITION_BEARISH",
          "T24.HH(60)+LL(80) → TRANSITION_BEARISH (LL more recent)", f"got {s6!r}")

    # Нет classified (только FIRST) → UNKNOWN
    s7, _ = _determine_structure(
        [{"index": 0, "price": 100.0, "classification": "FIRST"}],
        [{"index": 5, "price": 90.0,  "classification": "FIRST"}],
    )
    check(s7 == "UNKNOWN", "T24.only_FIRST → UNKNOWN", f"got {s7!r}")

    print("     All _determine_structure unit tests done")


# ═════════════════════════════════════════════════════════════════════════════
#  ТЕСТ 25 — _classify_pivot_sequence
# ═════════════════════════════════════════════════════════════════════════════

def test_classify_pivot_sequence() -> None:
    section("T25 — _classify_pivot_sequence unit-тесты")

    highs = [(5, 100.0), (15, 110.0), (25, 105.0), (35, 115.0)]
    result = _classify_pivot_sequence(highs, "HIGH")

    check(len(result) == 4, "T25.len == 4")
    check(result[0]["classification"] == "FIRST",  "T25.[0] == FIRST")
    check(result[1]["classification"] == "HH",     "T25.[1] == HH (110>100)")
    check(result[2]["classification"] == "LH",     "T25.[2] == LH (105<110)")
    check(result[3]["classification"] == "HH",     "T25.[3] == HH (115>105)")

    lows = [(10, 90.0), (20, 85.0), (30, 87.0)]
    result_l = _classify_pivot_sequence(lows, "LOW")
    check(result_l[0]["classification"] == "FIRST", "T25.low[0] == FIRST")
    check(result_l[1]["classification"] == "LL",    "T25.low[1] == LL (85<90)")
    check(result_l[2]["classification"] == "HL",    "T25.low[2] == HL (87>85)")

    print(f"     highs: {[r['classification'] for r in result]}")
    print(f"     lows:  {[r['classification'] for r in result_l]}")


# ═════════════════════════════════════════════════════════════════════════════
#  Итог
# ═════════════════════════════════════════════════════════════════════════════

def _summary() -> bool:
    total = len(_passed) + len(_failed)
    print(f"\n{'═'*60}")
    print(f"  Market structure tests: {len(_passed)}/{total}")
    print()

    bullish_tests  = [(n, s) for n, s in zip(
        ["T2","T11"], ["Bullish structure","BOS confirms"]
    )]

    groups = {
        "Bullish structure tests": [
            n for n in _passed + _failed
            if any(t in n for t in ["T2.", "T9."])
        ],
        "Bearish structure tests": [
            n for n in _passed + _failed
            if any(t in n for t in ["T3.", "T10."])
        ],
        "Range tests": [n for n in _passed + _failed if "T4." in n],
        "BOS tests": [n for n in _passed + _failed
                      if any(t in n for t in ["T7.", "T8.", "T11.", "T12.", "T20."])],
        "CHoCH tests": [n for n in _passed + _failed if any(t in n for t in ["T9.", "T10.", "T12."])],
        "Closed-candle tests": [n for n in _passed + _failed if "T20." in n],
    }

    # Determine PASS/FAIL per group
    for group, tests in groups.items():
        failed_in_group = [n for n in tests if n in _failed]
        status = "PASSED" if not failed_in_group else f"FAILED ({len(failed_in_group)})"
        print(f"  {group}: {status}")

    print()
    if not _failed:
        print("  MARKET STRUCTURE STATUS: PASSED")
    else:
        print(f"  MARKET STRUCTURE STATUS: FAILED")
        print(f"\n  Failed checks:")
        for n in _failed:
            print(f"    ✗  {n}")

    print(f"\n  Ready for structure integration: {'YES' if not _failed else 'NO'}")
    print(f"{'═'*60}\n")
    return len(_failed) == 0


def main() -> bool:
    print()
    print("═" * 60)
    print("  MARKET STRUCTURE TESTS (v0.5)")
    print("═" * 60)

    test_stable_format()
    test_bullish_structure()
    test_bearish_structure()
    test_range_structure()
    test_transition_bullish()
    test_transition_bearish()
    test_bullish_bos()
    test_bearish_bos()
    test_bullish_choch()
    test_bearish_choch()
    test_bos_confirms_structure()
    test_bos_contradicts_structure()
    test_insufficient_pivots()
    test_empty_df()
    test_missing_columns()
    test_nan_values()
    test_invalid_pivot_type()
    test_equality_tolerance()
    test_score_range()
    test_closed_candle_used()
    test_lookback_pivots_limit()
    test_pattern_strings()
    test_no_single_pivot_signal()
    test_determine_structure_units()
    test_classify_pivot_sequence()

    return _summary()


if __name__ == "__main__":
    ok_flag = main()
    sys.exit(0 if ok_flag else 1)
