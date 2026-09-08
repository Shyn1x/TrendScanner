"""
trend_quality.py
~~~~~~~~~~~~~~~~
Оценка качества трендовой линии (0–100).

Критерии:
  • touches   — количество касаний линии свечами
  • length    — длина линии в барах
  • angle     — угол наклона (не слишком плоский и не слишком крутой)
  • freshness — насколько свежа линия (последняя точка близко к правому краю)

Архитектура модульная: каждый критерий — отдельная функция.
Чтобы добавить новый критерий (объём, ATR и т.д.) достаточно добавить
функцию score_<name> и внести её в CRITERIA внутри calc_trend_quality.
"""

import math
from trendlines import line_value


# ─── отдельные критерии ──────────────────────────────────────────────────────

def score_touches(df, line, tolerance: float = 0.003, end_index=None) -> int:
    """
    Считает, сколько свечей «коснулись» линии (high или low в пределах
    tolerance от значения линии в этой точке).
    2 касания = базовый уровень, 6+ = максимум.
    """
    touches = 0
    effective_end = len(df) - 1 if end_index is None else max(
        0, min(int(end_index), len(df) - 1)
    )
    for i in range(line["x1"], effective_end + 1):
        val = line_value(line, i)
        if val <= 0:
            continue
        high_dist = abs(df.high.iloc[i] - val) / val
        low_dist  = abs(df.low.iloc[i]  - val) / val
        if high_dist < tolerance or low_dist < tolerance:
            touches += 1

    # 2 = минимум значимой линии, 6 = насыщение
    raw = max(0, touches - 1)          # первое касание = основание
    score = min(int(raw / 5 * 100), 100)
    return score


def score_length(line) -> int:
    """
    Длина линии в барах.
    10 баров = 0, 100+ баров = 100.
    """
    length = line["x2"] - line["x1"]
    score = min(int((length - 10) / 90 * 100), 100)
    return max(score, 0)


def score_angle(line) -> int:
    """
    Угол наклона линии.
    Идеальный диапазон — 15–55 градусов (процентное изменение за бар).
    Слишком плоская или слишком крутая линия снижает оценку.
    """
    if line["y1"] == 0:
        return 0

    # нормализуем наклон: изменение цены за бар / базовая цена (в %)
    pct_per_bar = abs(line["slope"]) / line["y1"] * 100
    angle_deg   = math.degrees(math.atan(pct_per_bar * 10))

    # 15–55° → 100; вне этого диапазона линейно падает до 0
    if 15 <= angle_deg <= 55:
        score = 100
    elif angle_deg < 15:
        score = int(angle_deg / 15 * 100)
    else:
        score = max(0, int((1 - (angle_deg - 55) / 35) * 100))

    return score


def score_freshness(line, df_len: int) -> int:
    """
    Насколько свежа линия: расстояние от последней опорной точки (x2) до
    конца DataFrame.
    0 баров назад = 100, 50+ баров назад = 0.
    """
    bars_ago = (df_len - 1) - line["x2"]
    score = max(0, int((1 - bars_ago / 50) * 100))
    return score


# ─── главная функция ──────────────────────────────────────────────────────────

def calc_trend_quality(df, line, end_index=None) -> dict:
    """
    Принимает DataFrame и трендовую линию (dict из create_trendline).
    Возвращает словарь с оценкой каждого критерия и итоговым
    trend_quality_score (0–100).

    Параметры:
        df   — pandas DataFrame с колонками high, low, close
        line — dict: {x1, y1, x2, y2, slope}  или None

    Пример вывода:
        {
            "touches_score":   80,
            "length_score":    60,
            "angle_score":     90,
            "freshness_score": 100,
            "trend_quality_score": 82
        }
    """
    if line is None:
        return {
            "touches_score":         0,
            "length_score":          0,
            "angle_score":           0,
            "freshness_score":       0,
            "trend_quality_score":   0,
        }

    effective_end = len(df) - 1 if end_index is None else max(
        0, min(int(end_index), len(df) - 1)
    )
    touches   = score_touches(df, line, end_index=effective_end)
    length    = score_length(line)
    angle     = score_angle(line)
    freshness = score_freshness(line, effective_end + 1)

    # Веса критериев (в сумме 100)
    WEIGHTS = {
        "touches":   35,
        "length":    25,
        "angle":     20,
        "freshness": 20,
    }

    total = (
        touches   * WEIGHTS["touches"]   +
        length    * WEIGHTS["length"]    +
        angle     * WEIGHTS["angle"]     +
        freshness * WEIGHTS["freshness"]
    ) // 100

    return {
        "touches_score":         touches,
        "length_score":          length,
        "angle_score":           angle,
        "freshness_score":       freshness,
        "trend_quality_score":   int(total),
    }
