"""
market_structure.py
~~~~~~~~~~~~~~~~~~~
Market Structure Engine — Trend Scanner v0.5

Определяет рыночную структуру (HH/HL/LH/LL) по подтверждённым пивотам.
Не дублирует поиск пивотов — использует find_pivots из trendlines.py.

Публичный API:
    analyze_market_structure(df, pivot_window=5, lookback_pivots=6) -> dict
    classify_pivot_change(prev, cur, pivot_type, tolerance=0.001) -> str

Типы структур:
    BULLISH | BEARISH | RANGE |
    TRANSITION_BULLISH | TRANSITION_BEARISH | UNKNOWN

Формат результата — см. docstring analyze_market_structure.

ИНВАРИАНТЫ:
    • Последняя незакрытая свеча (iloc[-1]) НЕ используется для BOS —
      используется iloc[-2] согласованно с breakout_quality и volume_quality.
    • Все ошибки данных перехватываются и возвращают UNKNOWN, не поднимают исключений.
    • Пивоты ищутся через find_pivots(trendlines) без дублирования логики.
"""

import copy
import math

import pandas as pd

from trendlines import find_pivots


# ─── константы ────────────────────────────────────────────────────────────────

DEFAULT_TOLERANCE    = 0.001   # 0.1% — порог равенства пивотов
_VALID_PIVOT_TYPES   = {"HIGH", "LOW"}
_VALID_STRUCTURES    = {
    "BULLISH", "BEARISH", "RANGE",
    "TRANSITION_BULLISH", "TRANSITION_BEARISH", "UNKNOWN",
}

_STRUCTURE_BASE_SCORE: dict[str, float] = {
    "BULLISH":             70.0,
    "BEARISH":             70.0,
    "TRANSITION_BULLISH":  50.0,
    "TRANSITION_BEARISH":  50.0,
    "RANGE":               30.0,
    "UNKNOWN":              0.0,
}


# ─────────────────────────────────────────────────────────────────────────────
#  1. Классификация одного пивота
# ─────────────────────────────────────────────────────────────────────────────

def classify_pivot_change(
    previous_value: float,
    current_value:  float,
    pivot_type:     str,
    tolerance:      float = DEFAULT_TOLERANCE,
) -> str:
    """
    Классифицирует изменение между двумя последовательными пивотами одного типа.

    Parameters
    ----------
    previous_value : float  Цена предыдущего пивота.
    current_value  : float  Цена текущего пивота.
    pivot_type     : str    "HIGH" или "LOW".
    tolerance      : float  Относительная погрешность равенства (default 0.001 = 0.1%).

    Returns
    -------
    str:
        "HH" — higher high     (pivot_type="HIGH", current > previous)
        "LH" — lower  high     (pivot_type="HIGH", current < previous)
        "EH" — equal  high     (pivot_type="HIGH", |diff| ≤ tolerance)
        "HL" — higher low      (pivot_type="LOW",  current > previous)
        "LL" — lower  low      (pivot_type="LOW",  current < previous)
        "EL" — equal  low      (pivot_type="LOW",  |diff| ≤ tolerance)

    Raises
    ------
    ValueError  если pivot_type не "HIGH" и не "LOW".
    """
    if pivot_type not in _VALID_PIVOT_TYPES:
        raise ValueError(
            f"classify_pivot_change: pivot_type должен быть 'HIGH' или 'LOW', "
            f"получено {pivot_type!r}"
        )

    # Относительная разница (защита от деления на 0)
    denom   = max(abs(previous_value), 1e-10)
    rel_diff = (current_value - previous_value) / denom

    # Равенство по допуску
    if abs(rel_diff) <= tolerance:
        return "EH" if pivot_type == "HIGH" else "EL"

    if pivot_type == "HIGH":
        return "HH" if current_value > previous_value else "LH"
    else:  # LOW
        return "HL" if current_value > previous_value else "LL"


# ─────────────────────────────────────────────────────────────────────────────
#  2. Классификация последовательности пивотов
# ─────────────────────────────────────────────────────────────────────────────

def _classify_pivot_sequence(
    pivots:     list[tuple[int, float]],
    pivot_type: str,
    tolerance:  float = DEFAULT_TOLERANCE,
) -> list[dict]:
    """
    Принимает список (index, price) от find_pivots,
    возвращает классифицированный список словарей.

    Первый пивот получает classification="FIRST".
    """
    result: list[dict] = []
    for i, (idx, price) in enumerate(pivots):
        if i == 0:
            classification = "FIRST"
        else:
            prev_price     = pivots[i - 1][1]
            classification = classify_pivot_change(prev_price, price, pivot_type, tolerance)
        result.append({
            "index":          idx,
            "price":          price,
            "classification": classification,
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  3. Определение рыночной структуры
# ─────────────────────────────────────────────────────────────────────────────

def _determine_structure(
    classified_highs: list[dict],
    classified_lows:  list[dict],
) -> tuple[str, str]:
    """
    Определяет рыночную структуру по классифицированным пивотам.

    Возвращает (structure: str, reason: str).

    Правила (по приоритету):
        1. HH + HL → BULLISH
        2. LH + LL → BEARISH
        3. HH + LL → TRANSITION (disambiguate by time of last pivot)
        4. LH + HL → RANGE (contradictory)
        5. EH + EL → RANGE
        6. Partial transitions (HH+EL, LL+EH) → TRANSITION
        7. Иначе → RANGE или UNKNOWN
    """
    high_classes = [
        c["classification"]
        for c in classified_highs
        if c["classification"] != "FIRST"
    ]
    low_classes = [
        c["classification"]
        for c in classified_lows
        if c["classification"] != "FIRST"
    ]

    if not high_classes and not low_classes:
        return "UNKNOWN", "Недостаточно классифицированных пивотов"
    if not high_classes or not low_classes:
        return "UNKNOWN", "Необходимы пивоты обоих типов (highs и lows)"

    latest_high = high_classes[-1]
    latest_low  = low_classes[-1]

    # ── Чистые структуры ──────────────────────────────────────────────────────
    if latest_high == "HH" and latest_low == "HL":
        return "BULLISH", "HH + HL: последовательные higher highs и higher lows"

    if latest_high == "LH" and latest_low == "LL":
        return "BEARISH", "LH + LL: последовательные lower highs и lower lows"

    # ── Двусмысленный HH + LL (оба «пробоя» присутствуют) ────────────────────
    if latest_high == "HH" and latest_low == "LL":
        # Disambiguate by chronological order of most recent pivots
        last_high_idx = classified_highs[-1]["index"]
        last_low_idx  = classified_lows[-1]["index"]
        if last_high_idx >= last_low_idx:
            return (
                "TRANSITION_BULLISH",
                "HH появился после LL — возможная смена структуры на бычью"
            )
        else:
            return (
                "TRANSITION_BEARISH",
                "LL появился после HH — возможная смена структуры на медвежью"
            )

    # ── Противоречие: LH + HL ─────────────────────────────────────────────────
    if latest_high == "LH" and latest_low == "HL":
        return "RANGE", "LH + HL: противоречивые сигналы — боковик"

    # ── Равные пивоты ─────────────────────────────────────────────────────────
    if latest_high == "EH" and latest_low == "EL":
        return "RANGE", "EH + EL: равные пивоты — диапазон"

    # ── Частичные Transition ─────────────────────────────────────────────────
    if latest_high == "HH" and latest_low == "EL":
        return (
            "TRANSITION_BULLISH",
            "HH с нейтральными lows (EL) — возможный переход к бычьей структуре"
        )
    if latest_low == "LL" and latest_high == "EH":
        return (
            "TRANSITION_BEARISH",
            "LL с нейтральными highs (EH) — возможный переход к медвежьей структуре"
        )

    # ── Смешанные с равными ───────────────────────────────────────────────────
    if latest_high == "LH" and latest_low == "EL":
        return "RANGE", "LH с нейтральными lows (EL) — боковик с медвежьим уклоном"
    if latest_low == "HL" and latest_high == "EH":
        return "RANGE", "HL с нейтральными highs (EH) — боковик с бычьим уклоном"

    return "RANGE", f"Смешанные сигналы: high={latest_high}, low={latest_low}"


# ─────────────────────────────────────────────────────────────────────────────
#  4. Break of Structure
# ─────────────────────────────────────────────────────────────────────────────

def _detect_bos(
    df,
    highs:           list[tuple[int, float]],
    lows:            list[tuple[int, float]],
    last_closed_idx: int,
) -> dict:
    """
    Определяет Break of Structure на основе последней ЗАКРЫТОЙ свечи.

    Использует df.close.iloc[last_closed_idx] (обычно n-2).
    Пивотные уровни берутся из `highs` и `lows` (уже обрезанных до lookback).

    Returns
    -------
    {"direction": "BULLISH"|"BEARISH"|"NONE", "level": float|None, "confirmed": bool}
    """
    _none = {"direction": "NONE", "level": None, "confirmed": False}

    if last_closed_idx < 0 or last_closed_idx >= len(df):
        return _none

    try:
        closed_close = float(df["close"].iloc[last_closed_idx])
    except (IndexError, KeyError, TypeError, ValueError):
        return _none

    if not math.isfinite(closed_close):
        return _none

    bos_direction: str        = "NONE"
    bos_level:     float|None = None

    # Bullish BOS: close выше последнего подтверждённого pivot high
    if highs:
        last_ph = highs[-1][1]
        if math.isfinite(last_ph) and closed_close > last_ph:
            bos_direction = "BULLISH"
            bos_level     = last_ph

    # Bearish BOS: close ниже последнего подтверждённого pivot low
    if lows:
        last_pl = lows[-1][1]
        if math.isfinite(last_pl) and closed_close < last_pl:
            # Bearish BOS перекрывает bullish (более сильный сигнал)
            bos_direction = "BEARISH"
            bos_level     = last_pl

    return {
        "direction": bos_direction,
        "level":     bos_level,
        "confirmed": bos_direction != "NONE",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  5. Change of Character
# ─────────────────────────────────────────────────────────────────────────────

def _determine_choch(structure: str, bos_direction: str) -> str:
    """
    CHoCH возникает, когда BOS противоречит подтверждённой структуре.

    BEARISH | TRANSITION_BEARISH + bullish BOS  → "BULLISH"
    BULLISH | TRANSITION_BULLISH + bearish BOS  → "BEARISH"
    Иначе → "NONE"
    """
    if bos_direction == "BULLISH" and structure in ("BEARISH", "TRANSITION_BEARISH"):
        return "BULLISH"
    if bos_direction == "BEARISH" and structure in ("BULLISH", "TRANSITION_BULLISH"):
        return "BEARISH"
    return "NONE"


# ─────────────────────────────────────────────────────────────────────────────
#  6. Structure Score
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_structure_score(
    structure:        str,
    classified_highs: list[dict],
    classified_lows:  list[dict],
    bos_direction:    str,
    choch:            str,
) -> float:
    """
    Рассчитывает structure_score от 0 до 100.

    Базовые очки по структуре + бонусы/штрафы:
    • Согласованные последние 2 пары HH+HL или LH+LL: +15
    • BOS по направлению структуры:                  +15
    • BOS против направления структуры:               -25
    • CHoCH: score ограничивается до 60

    Результат ограничен диапазоном [0, 100].
    """
    score = _STRUCTURE_BASE_SCORE.get(structure, 0.0)

    # Фильтруем FIRST
    high_classes = [
        c["classification"] for c in classified_highs if c["classification"] != "FIRST"
    ]
    low_classes = [
        c["classification"] for c in classified_lows if c["classification"] != "FIRST"
    ]

    # ── Бонус за согласованные последние 2 пары ───────────────────────────────
    if len(high_classes) >= 2 and len(low_classes) >= 2:
        last2h = high_classes[-2:]
        last2l = low_classes[-2:]
        bullish_pair = all(c == "HH" for c in last2h) and all(c == "HL" for c in last2l)
        bearish_pair = all(c == "LH" for c in last2h) and all(c == "LL" for c in last2l)
        if bullish_pair or bearish_pair:
            score += 15.0

    # ── Корректировка по BOS ──────────────────────────────────────────────────
    if bos_direction != "NONE":
        struct_dir: str | None = None
        if structure in ("BULLISH", "TRANSITION_BULLISH"):
            struct_dir = "BULLISH"
        elif structure in ("BEARISH", "TRANSITION_BEARISH"):
            struct_dir = "BEARISH"

        if struct_dir is not None:
            if bos_direction == struct_dir:
                score += 15.0   # BOS подтверждает структуру
            else:
                score -= 25.0   # BOS противоречит структуре

    # ── CHoCH: ограничиваем score ─────────────────────────────────────────────
    if choch != "NONE":
        score = min(score, 60.0)

    return max(0.0, min(100.0, score))


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательные
# ─────────────────────────────────────────────────────────────────────────────

def _get_latest_pattern(classified: list[dict]) -> str:
    """Возвращает строку из последних 3 классификаций (не FIRST), напр. 'HH→HH'."""
    classes = [c["classification"] for c in classified if c["classification"] != "FIRST"]
    return "→".join(classes[-3:]) if classes else "—"


_EMPTY_BOS: dict = {"direction": "NONE", "level": None, "confirmed": False}


def _unknown_result(reason: str) -> dict:
    """Возвращает UNKNOWN-результат с заданной причиной."""
    return {
        "structure":           "UNKNOWN",
        "structure_score":     0.0,
        "bos":                 copy.copy(_EMPTY_BOS),
        "choch":               "NONE",
        "last_closed_index":   None,
        "pivot_highs":         [],
        "pivot_lows":          [],
        "latest_high_pattern": "—",
        "latest_low_pattern":  "—",
        "reason":              reason,
    }


def _validate_df(df) -> str | None:
    """
    Проверяет DataFrame на корректность.
    Возвращает строку с причиной ошибки или None, если df валиден.
    """
    if df is None:
        return "df is None"
    try:
        if not isinstance(df, pd.DataFrame):
            return f"df has unexpected type: {type(df).__name__}"
        if df.empty:
            return "Empty DataFrame"
    except Exception as exc:
        return f"DataFrame validation error: {exc}"

    for col in ("high", "low", "close"):
        if col not in df.columns:
            return f"Missing required column: '{col}'"

    if len(df) < 3:
        return f"DataFrame too short: {len(df)} rows (need ≥ 3)"

    # Проверяем на нулевые и отрицательные цены (после удаления NaN)
    for col in ("high", "low", "close"):
        try:
            vals = df[col].dropna()
            if len(vals) > 0 and (vals <= 0).any():
                return f"Non-positive values in column '{col}'"
        except Exception as exc:
            return f"Error checking column '{col}': {exc}"

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Публичный интерфейс
# ─────────────────────────────────────────────────────────────────────────────

def analyze_market_structure(
    df,
    pivot_window:    int = 5,
    lookback_pivots: int = 6,
) -> dict:
    """
    Анализирует рыночную структуру по последовательности pivot highs и pivot lows.

    Использует find_pivots из trendlines.py для обнаружения пивотов.
    Последняя незакрытая свеча (iloc[-1]) НЕ используется — BOS вычисляется
    по iloc[-2], согласованно с остальным pipeline.

    Parameters
    ----------
    df              : pd.DataFrame  OHLCV с колонками high, low, close (open/volume опционально).
    pivot_window    : int           Окно find_pivots (default 5).
    lookback_pivots : int           Максимальное число пивотов в возвращаемых списках.

    Returns
    -------
    dict:
        {
            "structure":           str,    # BULLISH|BEARISH|RANGE|TRANSITION_*|UNKNOWN
            "structure_score":     float,  # 0–100
            "bos": {
                "direction":  str,         # BULLISH|BEARISH|NONE
                "level":      float|None,  # цена пробитого пивота
                "confirmed":  bool
            },
            "choch":               str,    # BULLISH|BEARISH|NONE
            "last_closed_index":   int|None,
            "pivot_highs": [
                {"index": int, "price": float, "classification": str}
            ],
            "pivot_lows": [
                {"index": int, "price": float, "classification": str}
            ],
            "latest_high_pattern": str,    # напр. "HH→HH"
            "latest_low_pattern":  str,    # напр. "HL→HL"
            "reason":              str
        }

    Никогда не выбрасывает исключение; при ошибках — UNKNOWN с reason.
    """
    # ── Валидация ─────────────────────────────────────────────────────────────
    err = _validate_df(df)
    if err:
        return _unknown_result(err)

    n         = len(df)
    min_rows  = 2 * pivot_window + 1
    if n < min_rows:
        return _unknown_result(
            f"Недостаточно строк: {n} < {min_rows} "
            f"(pivot_window={pivot_window}, нужно 2×window+1)"
        )

    # ── Индекс последней закрытой свечи ──────────────────────────────────────
    last_closed_idx = n - 2   # ИНВАРИАНТ: согласовано с breakout_quality

    # ── Поиск пивотов (без дублирования логики) ───────────────────────────────
    try:
        raw_highs, raw_lows = find_pivots(df, window=pivot_window)
    except Exception as exc:
        return _unknown_result(f"find_pivots failed: {exc}")

    if not raw_highs:
        return _unknown_result("Не найдено pivot highs (недостаточно данных или нет локальных максимумов)")
    if not raw_lows:
        return _unknown_result("Не найдено pivot lows (недостаточно данных или нет локальных минимумов)")

    # ── Ограничение lookback ──────────────────────────────────────────────────
    raw_highs_lb = raw_highs[-lookback_pivots:]
    raw_lows_lb  = raw_lows[-lookback_pivots:]

    # ── Классификация пивотов ────────────────────────────────────────────────
    try:
        classified_highs = _classify_pivot_sequence(raw_highs_lb, "HIGH")
        classified_lows  = _classify_pivot_sequence(raw_lows_lb,  "LOW")
    except Exception as exc:
        return _unknown_result(f"Pivot classification failed: {exc}")

    # ── Проверка NaN/inf в ценах пивотов ─────────────────────────────────────
    for entry in classified_highs + classified_lows:
        if not math.isfinite(entry["price"]):
            return _unknown_result(
                f"NaN/inf в цене пивота: index={entry['index']}, price={entry['price']}"
            )

    # ── Определение структуры ────────────────────────────────────────────────
    structure, reason = _determine_structure(classified_highs, classified_lows)

    # ── Break of Structure ────────────────────────────────────────────────────
    bos = _detect_bos(df, raw_highs_lb, raw_lows_lb, last_closed_idx)

    # ── Change of Character ───────────────────────────────────────────────────
    choch = _determine_choch(structure, bos["direction"])

    # ── Structure Score ───────────────────────────────────────────────────────
    structure_score = _calculate_structure_score(
        structure, classified_highs, classified_lows,
        bos["direction"], choch,
    )

    # ── Паттерн-строки ────────────────────────────────────────────────────────
    latest_high_pattern = _get_latest_pattern(classified_highs)
    latest_low_pattern  = _get_latest_pattern(classified_lows)

    return {
        "structure":           structure,
        "structure_score":     round(structure_score, 2),
        "bos":                 bos,
        "choch":               choch,
        "last_closed_index":   last_closed_idx,
        "pivot_highs":         classified_highs,
        "pivot_lows":          classified_lows,
        "latest_high_pattern": latest_high_pattern,
        "latest_low_pattern":  latest_low_pattern,
        "reason":              reason,
    }
