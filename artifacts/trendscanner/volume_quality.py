"""
volume_quality.py
~~~~~~~~~~~~~~~~~
Оценка качества объёма для подтверждения пробоя трендовой линии.

ВАЖНО: score_volume принимает параметр signal_index (default=-2).
Это гарантирует, что объём измеряется на той же ЗАКРЫТОЙ сигнальной свече,
что и breakout_quality (которая использует iloc[-2]).

Параметр signal_index:
    -2  (default) → предпоследняя свеча (закрытая сигнальная свеча)
    -1            → последняя свеча (может быть незакрытой, старое поведение)
"""

import math


# ─── вспомогательные функции ──────────────────────────────────────────────────

def _get_current_volume(df, signal_index: int = -2) -> float:
    """
    Возвращает объём сигнальной свечи.

    Параметры:
        df           — pandas DataFrame с колонкой 'volume'
        signal_index — индекс сигнальной свечи (по умолчанию -2 = предпоследняя)

    Возвращает:
        float — объём; 0.0 если DataFrame пуст или индекс за пределами
    """
    if len(df) == 0:
        return 0.0
    try:
        return float(df["volume"].iloc[signal_index])
    except (IndexError, KeyError, TypeError, ValueError):
        return 0.0


def _get_average_volume(df, period: int = 20, signal_index: int = -2) -> float:
    """
    Вычисляет средний объём за `period` свечей ДО сигнальной свечи
    (не включая её саму, чтобы не смешивать с измеряемой).

    Параметры:
        df           — pandas DataFrame с колонкой 'volume'
        period       — количество свечей для расчёта среднего
        signal_index — индекс сигнальной свечи (по умолчанию -2)

    Возвращает:
        float — средний объём; 0.0 если данных недостаточно
    """
    n = len(df)
    if n == 0:
        return 0.0

    # Приводим signal_index к абсолютному
    if signal_index < 0:
        abs_sig = n + signal_index
    else:
        abs_sig = signal_index

    start = abs_sig - period
    end   = abs_sig       # не включаем сигнальную свечу

    if start < 0 or end <= 0 or start >= end:
        return 0.0

    try:
        avg = df["volume"].iloc[start:end].mean()
    except Exception:
        return 0.0

    if avg is None:
        return 0.0
    try:
        f = float(avg)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _ratio_to_score(ratio: float) -> int:
    """
    Переводит соотношение current_volume / average_volume в оценку 0–100.

    Шкала:
        < 0.8         →  20  (объём слабее среднего)
        0.8  – 1.0    →  40  (чуть ниже среднего)
        1.0  – 1.2    →  60  (около среднего)
        1.2  – 1.5    →  80  (выше среднего — хороший пробой)
        ≥ 1.5         → 100  (сильный всплеск — подтверждённый пробой)
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

def score_volume(df, period: int = 20, signal_index: int = -2) -> dict:
    """
    Оценивает, подтверждает ли объём текущий пробой трендовой линии.

    Алгоритм:
        1. Берёт объём сигнальной свечи (signal_index, default=-2).
        2. Считает средний объём за `period` свечей ДО сигнальной свечи.
        3. Вычисляет ratio = current / average.
        4. Переводит ratio в оценку volume_score (0–100).

    Параметры:
        df           — pandas DataFrame с колонками ['open','high','low','close','volume']
        period       — окно для расчёта среднего объёма (по умолчанию 20)
        signal_index — индекс сигнальной свечи (по умолчанию -2 = предпоследняя,
                       совпадает с сигнальной свечой breakout_quality)

    Возвращает:
        dict:
            volume_score    — int,   итоговая оценка 0–100
            current_volume  — float, объём сигнальной свечи
            average_volume  — float, средний объём за period свечей до сигнала
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
    # Guard: missing 'volume' column
    if "volume" not in df.columns:
        return {
            "volume_score":   0,
            "current_volume": 0.0,
            "average_volume": 0.0,
            "volume_ratio":   0.0,
            "reason":         "Колонка 'volume' отсутствует в DataFrame",
        }

    current = _get_current_volume(df, signal_index)
    average = _get_average_volume(df, period, signal_index)

    if average == 0.0:
        # Недостаточно данных — возвращаем нейтральный результат
        return {
            "volume_score":   0,
            "current_volume": round(current, 2),
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
