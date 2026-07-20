"""
volume_quality.py
~~~~~~~~~~~~~~~~~
Оценка качества объёма для подтверждения пробоя трендовой линии.

Архитектура модульная — модуль не зависит от trend_quality.py.
В будущем можно добавить:
  • score_obv(df)           — On-Balance Volume
  • score_volume_profile(df) — Volume Profile
  • score_delta_volume(df)  — Delta Volume (ask - bid)
  • score_cumulative(df)    — Cumulative Volume

Использование:
    from volume_quality import score_volume

    result = score_volume(df)
    # → {
    #       "volume_score":   80,
    #       "current_volume": 12500.0,
    #       "average_volume": 8900.0,
    #       "volume_ratio":   1.40
    #   }
"""


# ─── вспомогательные функции ──────────────────────────────────────────────────

def _get_average_volume(df, period: int = 20) -> float:
    """
    Вычисляет средний объём за последние `period` свечей
    (не включая последнюю, чтобы не смешивать с текущей).

    Параметры:
        df     — pandas DataFrame с колонкой 'volume'
        period — количество свечей для расчёта среднего (по умолчанию 20)

    Возвращает:
        float — средний объём; 0.0 если данных недостаточно
    """
    if len(df) < period + 1:
        return 0.0

    avg = df["volume"].iloc[-(period + 1):-1].mean()
    return float(avg)


def _get_current_volume(df) -> float:
    """
    Возвращает объём последней (закрытой) свечи.

    Параметры:
        df — pandas DataFrame с колонкой 'volume'

    Возвращает:
        float — объём последней свечи; 0.0 если DataFrame пуст
    """
    if len(df) == 0:
        return 0.0

    return float(df["volume"].iloc[-1])


def _ratio_to_score(ratio: float) -> int:
    """
    Переводит соотношение current_volume / average_volume в оценку 0–100.

    Шкала:
        < 0.8         →  20  (объём слабее среднего)
        0.8  – 1.0    →  40  (чуть ниже среднего)
        1.0  – 1.2    →  60  (около среднего)
        1.2  – 1.5    →  80  (выше среднего — хороший пробой)
        ≥ 1.5         → 100  (сильный всплеск — подтверждённый пробой)

    Параметры:
        ratio — float, отношение текущего объёма к среднему

    Возвращает:
        int — оценка от 0 до 100
    """
    if ratio < 0.8:
        return 20
    elif ratio < 1.0:
        return 40
    elif ratio < 1.2:
        return 60
    elif ratio < 1.5:
        return 80
    else:
        return 100


# ─── основная функция ─────────────────────────────────────────────────────────

def score_volume(df, period: int = 20) -> dict:
    """
    Оценивает, подтверждает ли объём текущий пробой трендовой линии.

    Алгоритм:
        1. Берёт объём последней свечи (current_volume).
        2. Считает средний объём за последние `period` свечей (average_volume).
        3. Вычисляет ratio = current / average.
        4. Переводит ratio в оценку volume_score (0–100).

    Параметры:
        df     — pandas DataFrame с колонками ['open','high','low','close','volume']
        period — окно для расчёта среднего объёма (по умолчанию 20)

    Возвращает:
        dict:
            volume_score    — int,   итоговая оценка 0–100
            current_volume  — float, объём последней свечи
            average_volume  — float, средний объём за period свечей
            volume_ratio    — float, current / average (округлено до 2 знаков)

    Пример:
        >>> result = score_volume(df)
        >>> result
        {
            "volume_score":   80,
            "current_volume": 12500.0,
            "average_volume": 8900.0,
            "volume_ratio":   1.40
        }
    """
    current = _get_current_volume(df)
    average = _get_average_volume(df, period)

    if average == 0.0:
        # Недостаточно данных — возвращаем нейтральный результат
        return {
            "volume_score":   0,
            "current_volume": current,
            "average_volume": 0.0,
            "volume_ratio":   0.0,
        }

    ratio = current / average
    score = _ratio_to_score(ratio)

    return {
        "volume_score":   score,
        "current_volume": round(current, 2),
        "average_volume": round(average, 2),
        "volume_ratio":   round(ratio, 2),
    }
