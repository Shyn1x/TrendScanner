"""
breakout_quality.py
~~~~~~~~~~~~~~~~~~~~
Оценка качества пробоя трендовой линии по шкале 0–100.

Компоненты оценки:
    cross            (0–30) — было ли настоящее пересечение линии
    close_distance   (0–30) — насколько далеко закрытие от линии (в ATR)
    candle_body      (0–20) — отношение тела свечи к её диапазону
    rejection_wick   (0–20) — штраф за тень против направления пробоя

Зависимости:
    • trendlines.line_value — вычисление значения линии на индексе

Нет зависимостей от scanner.py, Streamlit, multi_tf.py, confidence.py.
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from trendlines import line_value


# ─── константы ────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {"open", "high", "low", "close"}
MIN_ROWS_FOR_ATR = 3   # минимум строк для расчёта ATR


# ─── нулевой результат ────────────────────────────────────────────────────────

def _null_result(direction: str, reason: str) -> dict:
    """Возвращает пустой словарь результата с понятной причиной."""
    return {
        "breakout_score":       0.0,
        "confirmed":            False,
        "direction":            direction,
        "signal_index":         None,
        "line_price":           None,
        "signal_close":         None,
        "atr":                  None,
        "distance_atr":         0.0,
        "body_ratio":           0.0,
        "rejection_wick_ratio": 0.0,
        "components": {
            "cross":            0.0,
            "close_distance":   0.0,
            "candle_body":      0.0,
            "rejection_wick":   0.0,
        },
        "reason": reason,
    }


# ─── расчёт ATR ───────────────────────────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    Вычисляет Average True Range вручную по High, Low и предыдущему Close.

    True Range = max(
        high - low,
        abs(high - prev_close),
        abs(low  - prev_close)
    )
    ATR = среднее True Range за `period` баров.

    Параметры:
        df     — DataFrame с колонками high, low, close
        period — период сглаживания (по умолчанию 14)

    Возвращает:
        float или None если данных недостаточно
    """
    if len(df) < period + 1:
        return None

    trs: list[float] = []
    for i in range(1, len(df)):
        high       = float(df["high"].iloc[i])
        low        = float(df["low"].iloc[i])
        prev_close = float(df["close"].iloc[i - 1])
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low  - prev_close),
        )
        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


# ─── компонент A: пересечение линии ───────────────────────────────────────────

def score_cross(
    prev_close:  float,
    signal_close: float,
    prev_line:   float,
    signal_line: float,
    direction:   str,
) -> float:
    """
    Оценивает факт пересечения трендовой линии (0 или 30 баллов).

    LONG:  prev_close <= prev_line  И  signal_close > signal_line
    SHORT: prev_close >= prev_line  И  signal_close < signal_line

    Возвращает:
        30.0 если пересечение подтверждено, иначе 0.0
    """
    if direction == "LONG":
        crossed = (prev_close <= prev_line) and (signal_close > signal_line)
    else:
        crossed = (prev_close >= prev_line) and (signal_close < signal_line)

    return 30.0 if crossed else 0.0


# ─── компонент B: расстояние закрытия от линии ────────────────────────────────

def score_close_distance(
    signal_close: float,
    line_price:   float,
    atr:          float,
    direction:    str,
) -> float:
    """
    Оценивает, насколько далеко закрытие сигнальной свечи от линии (0–30).

    Нормализует дистанцию в единицах ATR и применяет линейное масштабирование:
        distance_atr <= 0          → 0
        0     – 0.15 ATR           → линейно 0–10
        0.15  – 0.35 ATR           → линейно 10–20
        0.35+ ATR                  → линейно 20–30, насыщение при 0.70 ATR

    Параметры:
        signal_close — цена закрытия сигнальной свечи
        line_price   — значение линии на индексе сигнальной свечи
        atr          — Average True Range
        direction    — "LONG" или "SHORT"

    Возвращает:
        float от 0.0 до 30.0
    """
    if atr <= 0:
        return 0.0

    if direction == "LONG":
        distance = signal_close - line_price
    else:
        distance = line_price - signal_close

    if distance <= 0:
        return 0.0

    d = distance / atr  # в единицах ATR

    if d < 0.15:
        return (d / 0.15) * 10.0
    elif d < 0.35:
        return 10.0 + ((d - 0.15) / 0.20) * 10.0
    else:
        # насыщение при 0.70 ATR
        return min(20.0 + ((d - 0.35) / 0.35) * 10.0, 30.0)


# ─── компонент C: тело свечи ──────────────────────────────────────────────────

def score_candle_body(
    open_:  float,
    high:   float,
    low:    float,
    close:  float,
) -> float:
    """
    Оценивает отношение тела свечи к её полному диапазону (0–20).

    body_ratio = abs(close - open) / (high - low)

    Шкала (линейное масштабирование):
        < 0.25              → 0
        0.25 – 0.50         → линейно 0–10
        0.50 – 0.75         → линейно 10–20
        > 0.75              → 20

    Параметры:
        open_, high, low, close — OHLC сигнальной свечи

    Возвращает:
        float от 0.0 до 20.0
    """
    candle_range = high - low
    if candle_range <= 0:
        return 0.0

    body       = abs(close - open_)
    body_ratio = body / candle_range

    if body_ratio < 0.25:
        return 0.0
    elif body_ratio < 0.50:
        return ((body_ratio - 0.25) / 0.25) * 10.0
    elif body_ratio < 0.75:
        return 10.0 + ((body_ratio - 0.50) / 0.25) * 10.0
    else:
        return 20.0


# ─── компонент D: тень отторжения ─────────────────────────────────────────────

def score_rejection_wick(
    open_:     float,
    high:      float,
    low:       float,
    close:     float,
    direction: str,
) -> float:
    """
    Штрафует за тень против направления пробоя (0–20, больше = лучше).

    LONG:  анализируем верхнюю тень (upper_wick = high - max(open, close))
    SHORT: анализируем нижнюю тень (lower_wick = min(open, close) - low)

    wick_ratio = wick / (high - low)

    Шкала:
        <= 0.10  → 20 (почти нет тени — хороший пробой)
        0.10–0.30 → линейно 20–10
        0.30–0.50 → линейно 10–0
        > 0.50   → 0 (длинная тень — слабый пробой)

    Параметры:
        open_, high, low, close — OHLC сигнальной свечи
        direction               — "LONG" или "SHORT"

    Возвращает:
        float от 0.0 до 20.0
    """
    candle_range = high - low
    if candle_range <= 0:
        return 20.0  # дожи без диапазона — нейтрально

    if direction == "LONG":
        wick = high - max(open_, close)
    else:
        wick = min(open_, close) - low

    wick = max(wick, 0.0)
    wick_ratio = wick / candle_range

    if wick_ratio <= 0.10:
        return 20.0
    elif wick_ratio <= 0.30:
        return 20.0 - ((wick_ratio - 0.10) / 0.20) * 10.0
    elif wick_ratio <= 0.50:
        return 10.0 - ((wick_ratio - 0.30) / 0.20) * 10.0
    else:
        return 0.0


# ─── основная функция ─────────────────────────────────────────────────────────

def calculate_breakout_quality(
    df:         pd.DataFrame,
    line:       Optional[dict],
    direction:  str,
    atr_period: int = 14,
) -> dict:
    """
    Оценивает качество пробоя трендовой линии (0–100).

    Использует предпоследнюю свечу как сигнальную (последняя может быть
    незакрытой), а ещё предыдущую — для проверки факта пересечения.

    Параметры:
        df         — DataFrame с колонками open, high, low, close [, volume]
        line       — dict из create_trendline() или None
        direction  — "LONG" (пробой вверх) или "SHORT" (пробой вниз)
        atr_period — период для расчёта ATR (по умолчанию 14)

    Возвращает:
        dict:
            breakout_score       — float, итоговая оценка 0–100
            confirmed            — bool, пересечение + score >= 50
            direction            — str
            signal_index         — int, индекс сигнальной свечи
            line_price           — float, значение линии на signal_index
            signal_close         — float, close сигнальной свечи
            atr                  — float или None
            distance_atr         — float, дистанция в ATR
            body_ratio           — float
            rejection_wick_ratio — float
            components           — dict с оценкой каждого компонента
            reason               — str, описание результата

    Исключения:
        ValueError — если direction не "LONG" и не "SHORT"
    """
    # ── валидация direction ────────────────────────────────────────────────────
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        raise ValueError(
            f"direction должен быть 'LONG' или 'SHORT', получено: '{direction}'"
        )

    # ── валидация line ─────────────────────────────────────────────────────────
    if line is None:
        return _null_result(direction, "line is None — трендовая линия не построена")

    # ── валидация DataFrame ────────────────────────────────────────────────────
    if df is None or len(df) == 0:
        return _null_result(direction, "DataFrame пустой")

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return _null_result(
            direction,
            f"Отсутствуют колонки: {', '.join(sorted(missing))}"
        )

    # нужны минимум 3 строки: prev, signal, (последняя незакрытая)
    if len(df) < 3:
        return _null_result(direction, "Недостаточно данных (нужно минимум 3 свечи)")

    # ── индексы свечей ────────────────────────────────────────────────────────
    # последняя строка может быть незакрытой → берём предпоследнюю как сигнальную
    signal_idx = len(df) - 2
    prev_idx   = signal_idx - 1

    # ── OHLC сигнальной и предыдущей свечей ───────────────────────────────────
    sig  = df.iloc[signal_idx]
    prev = df.iloc[prev_idx]

    sig_open  = float(sig["open"])
    sig_high  = float(sig["high"])
    sig_low   = float(sig["low"])
    sig_close = float(sig["close"])
    prev_close = float(prev["close"])

    # ── значения линии на индексах ────────────────────────────────────────────
    signal_line = line_value(line, signal_idx)
    prev_line   = line_value(line, prev_idx)

    # ── ATR ───────────────────────────────────────────────────────────────────
    atr = calculate_atr(df, atr_period)

    # ── компонент A: пересечение ──────────────────────────────────────────────
    c_cross = score_cross(
        prev_close,
        sig_close,
        prev_line,
        signal_line,
        direction,
    )
    crossed = c_cross > 0.0

    # ── компонент B: расстояние закрытия ──────────────────────────────────────
    if atr and atr > 0:
        c_distance = score_close_distance(sig_close, signal_line, atr, direction)
        distance_atr = (
            (sig_close - signal_line) / atr if direction == "LONG"
            else (signal_line - sig_close) / atr
        )
    else:
        c_distance   = 0.0
        distance_atr = 0.0

    # ── компонент C: тело свечи ───────────────────────────────────────────────
    c_body = score_candle_body(sig_open, sig_high, sig_low, sig_close)
    candle_range = sig_high - sig_low
    body_ratio = (
        abs(sig_close - sig_open) / candle_range
        if candle_range > 0 else 0.0
    )

    # ── компонент D: тень отторжения ──────────────────────────────────────────
    c_wick = score_rejection_wick(sig_open, sig_high, sig_low, sig_close, direction)
    if candle_range > 0:
        if direction == "LONG":
            raw_wick = sig_high - max(sig_open, sig_close)
        else:
            raw_wick = min(sig_open, sig_close) - sig_low
        wick_ratio = max(raw_wick, 0.0) / candle_range
    else:
        wick_ratio = 0.0

    # ── итог ──────────────────────────────────────────────────────────────────
    raw_score = c_cross + c_distance + c_body + c_wick
    # защита от NaN/Inf
    if not math.isfinite(raw_score):
        raw_score = 0.0
    breakout_score = float(max(0.0, min(100.0, raw_score)))

    confirmed = crossed and (breakout_score >= 50.0)

    # ── формируем reason ──────────────────────────────────────────────────────
    if confirmed:
        reason = f"Пробой подтверждён: score={breakout_score:.0f}, direction={direction}"
    elif crossed:
        reason = f"Пересечение есть, но score слабый ({breakout_score:.0f} < 50)"
    else:
        reason = "Пересечения не было — сигнальная свеча не пробила линию"

    return {
        "breakout_score":       round(breakout_score, 1),
        "confirmed":            confirmed,
        "direction":            direction,
        "signal_index":         signal_idx,
        "line_price":           round(signal_line, 6),
        "signal_close":         round(sig_close, 6),
        "atr":                  round(atr, 6) if atr is not None else None,
        "distance_atr":         round(distance_atr, 4),
        "body_ratio":           round(body_ratio, 4),
        "rejection_wick_ratio": round(wick_ratio, 4),
        "components": {
            "cross":          round(c_cross, 1),
            "close_distance": round(c_distance, 1),
            "candle_body":    round(c_body, 1),
            "rejection_wick": round(c_wick, 1),
        },
        "reason": reason,
    }


# ─── ручная проверка ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import numpy as np

    # Создаём синтетический DataFrame: восходящий рынок + пробой вверх
    n = 50
    np.random.seed(42)
    closes = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    opens  = closes - np.random.uniform(0.1, 0.5, n)
    highs  = closes + np.random.uniform(0.1, 0.8, n)
    lows   = opens  - np.random.uniform(0.1, 0.8, n)

    df_test = pd.DataFrame({
        "open":  opens,
        "high":  highs,
        "low":   lows,
        "close": closes,
    })

    # Простая трендовая линия: сопротивление слегка выше цены
    fake_line = {
        "x1":    10,
        "y1":    closes[10] + 1.5,
        "x2":    40,
        "y2":    closes[40] + 0.3,
        "slope": ((closes[40] + 0.3) - (closes[10] + 1.5)) / (40 - 10),
    }

    print("=" * 50)
    print("ТЕСТ LONG (нисходящая линия сопротивления)")
    print("=" * 50)
    result_long = calculate_breakout_quality(df_test, fake_line, "LONG")
    for k, v in result_long.items():
        print(f"  {k}: {v}")

    print()
    print("=" * 50)
    print("ТЕСТ SHORT (восходящая линия поддержки)")
    print("=" * 50)
    result_short = calculate_breakout_quality(df_test, fake_line, "SHORT")
    for k, v in result_short.items():
        print(f"  {k}: {v}")

    print()
    print("=" * 50)
    print("ТЕСТ: line=None")
    print("=" * 50)
    r = calculate_breakout_quality(df_test, None, "LONG")
    print(f"  breakout_score: {r['breakout_score']}, reason: {r['reason']}")

    print()
    print("=" * 50)
    print("ТЕСТ: пустой DataFrame")
    print("=" * 50)
    r = calculate_breakout_quality(pd.DataFrame(), fake_line, "LONG")
    print(f"  breakout_score: {r['breakout_score']}, reason: {r['reason']}")
