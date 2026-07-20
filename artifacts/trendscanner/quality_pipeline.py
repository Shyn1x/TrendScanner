"""
quality_pipeline.py
~~~~~~~~~~~~~~~~~~~
Orchestration module — собирает все Quality Engines для одного символа
и таймфрейма и возвращает единый структурированный анализ качества.

Поток данных:
    DataFrame
        ↓  find_pivots → create_trendline
        ↓  calc_trend_quality
        ↓  score_volume
        ↓  calculate_breakout_quality
        ↓  calculate_confidence
        →  {direction, signal, confirmed, line, tq, vq, bq, conf, reason}

Функции:
    analyze_quality(df, direction)        → dict (LONG или SHORT)
    analyze_both_directions(df)           → dict (LONG + SHORT + FINAL)

Нет зависимостей от scanner.py, Streamlit, analysis.py, multi_tf.py.
Подключение к Streamlit / analysis.py / multi_tf.py — следующий этап.
"""

from __future__ import annotations

import math

import pandas as pd

from trendlines       import find_pivots, create_trendline, check_break  # noqa: F401
from trend_quality    import calc_trend_quality
from volume_quality   import score_volume
from breakout_quality import calculate_breakout_quality
from confidence       import calculate_confidence, confidence_label


# ─── константы ────────────────────────────────────────────────────────────────

#: Минимальный Confidence Score для подтверждения сигнала.
#: Изменять только после тестов.
SIGNAL_THRESHOLD = 50.0

#: Размер окна для find_pivots.
PIVOT_WINDOW = 5

#: Минимальное количество свечей для работы find_pivots.
#: = 2 * PIVOT_WINDOW + 1 (нужно хотя бы одна внутренняя точка)
MIN_ROWS = 2 * PIVOT_WINDOW + 1   # 11

#: Обязательные OHLCV-колонки (volume — необязательна, обрабатывается gracefully).
REQUIRED_COLUMNS = {"open", "high", "low", "close"}


# ─── вспомогательные ─────────────────────────────────────────────────────────

def _wait_result(direction: str, reason: str) -> dict:
    """
    Возвращает пустой WAIT-результат с объяснением.
    Используется при любой невозможности рассчитать сигнал.
    """
    return {
        "direction":        direction,
        "signal":           "WAIT",
        "confirmed":        False,
        "line":             None,
        "trend_quality":    {"trend_quality_score": 0},
        "volume_quality":   {"volume_score": 0},
        "breakout_quality": {
            "breakout_score": 0.0,
            "confirmed":      False,
            "reason":         reason,
            "components": {
                "cross":          0.0,
                "close_distance": 0.0,
                "candle_body":    0.0,
                "rejection_wick": 0.0,
            },
        },
        "confidence": {
            "confidence":           0.0,
            "label":                "LOW",
            "available_components": 0,
            "reason":               reason,
            "components":           {},
        },
        "reason": reason,
    }


def _validate_df(df) -> tuple[bool, str]:
    """
    Проверяет DataFrame на соответствие минимальным требованиям.
    Возвращает (is_valid, reason_if_invalid).
    """
    if df is None:
        return False, "df is None"
    if not isinstance(df, pd.DataFrame):
        return False, f"df не является DataFrame (тип: {type(df).__name__})"
    if len(df) == 0:
        return False, "DataFrame пустой (0 строк)"
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return False, f"Отсутствуют обязательные колонки: {', '.join(sorted(missing))}"
    if len(df) < MIN_ROWS:
        return False, (
            f"Недостаточно свечей для анализа: {len(df)} < {MIN_ROWS} (минимум)"
        )
    return True, "ok"


# ─── основная функция одного направления ─────────────────────────────────────

def analyze_quality(df, direction: str) -> dict:
    """
    Запускает все Quality Engines для одного направления.

    Параметры:
        df        — DataFrame с колонками open, high, low, close [, volume]
        direction — "LONG" (нисходящая линия сопротивления, pivot highs)
                  или "SHORT" (восходящая линия поддержки, pivot lows)

    Возвращает:
        {
            "direction":        "LONG" | "SHORT",
            "signal":           "LONG" | "SHORT" | "WAIT",
            "confirmed":        bool,
            "line":             dict | None,
            "trend_quality":    dict,
            "volume_quality":   dict,
            "breakout_quality": dict,
            "confidence":       dict,
            "reason":           str
        }

    Исключения:
        ValueError — если direction не "LONG" и не "SHORT".
        Во всех остальных случаях функция не падает.
    """
    # ── валидация direction ────────────────────────────────────────────────────
    if not isinstance(direction, str) or direction.upper() not in ("LONG", "SHORT"):
        raise ValueError(
            f"direction должен быть 'LONG' или 'SHORT', получено: {direction!r}"
        )
    direction = direction.upper()

    # ── валидация DataFrame ────────────────────────────────────────────────────
    is_valid, df_reason = _validate_df(df)
    if not is_valid:
        return _wait_result(direction, df_reason)

    try:
        # ── построение линии ──────────────────────────────────────────────────
        highs, lows = find_pivots(df, window=PIVOT_WINDOW)

        if direction == "LONG":
            # Нисходящая линия сопротивления (pivot highs)
            points      = highs
            line_label  = "нисходящая линия сопротивления"
        else:
            # Восходящая линия поддержки (pivot lows)
            points      = lows
            line_label  = "восходящая линия поддержки"

        line = create_trendline(points)

        if line is None:
            n_pivots = len(points)
            reason   = (
                f"Недостаточно пивотов для {line_label} "
                f"(найдено: {n_pivots}, нужно минимум 2)"
            )
            return _wait_result(direction, reason)

        # ── качество трендовой линии ──────────────────────────────────────────
        try:
            tq = calc_trend_quality(df, line)
        except Exception as exc:
            tq = {"trend_quality_score": 0, "reason": str(exc)}

        # ── качество объёма ───────────────────────────────────────────────────
        try:
            vq = score_volume(df)
        except Exception as exc:
            vq = {"volume_score": 0, "reason": str(exc)}

        # ── качество пробоя ───────────────────────────────────────────────────
        try:
            bq = calculate_breakout_quality(df, line, direction)
        except ValueError:
            raise                                  # пробрасываем direction error
        except Exception as exc:
            bq = {
                "breakout_score": 0.0,
                "confirmed":      False,
                "reason":         str(exc),
                "components": {
                    "cross":          0.0,
                    "close_distance": 0.0,
                    "candle_body":    0.0,
                    "rejection_wick": 0.0,
                },
            }

        # ── Confidence Engine ─────────────────────────────────────────────────
        try:
            conf = calculate_confidence(
                trend_quality=tq,
                volume_quality=vq,
                breakout_quality=bq,
            )
        except Exception as exc:
            conf = {
                "confidence":           0.0,
                "label":                "LOW",
                "available_components": 0,
                "reason":               str(exc),
                "components":           {},
            }

        # ── определение сигнала ───────────────────────────────────────────────
        bq_confirmed = bool(bq.get("confirmed", False))
        conf_score   = float(conf.get("confidence", 0.0))

        if not math.isfinite(conf_score):
            conf_score = 0.0

        if bq_confirmed and conf_score >= SIGNAL_THRESHOLD:
            signal    = direction
            confirmed = True
            reason    = (
                f"Сигнал {direction}: пробой подтверждён, "
                f"confidence={conf_score:.1f} ≥ {SIGNAL_THRESHOLD}"
            )
        else:
            signal    = "WAIT"
            confirmed = False
            if not bq_confirmed:
                bq_score = bq.get("breakout_score", 0)
                reason   = (
                    f"WAIT: пробой не подтверждён "
                    f"(breakout_score={bq_score:.1f})"
                )
            else:
                reason = (
                    f"WAIT: пробой есть, но confidence недостаточен "
                    f"({conf_score:.1f} < {SIGNAL_THRESHOLD})"
                )

        return {
            "direction":        direction,
            "signal":           signal,
            "confirmed":        confirmed,
            "line":             line,
            "trend_quality":    tq,
            "volume_quality":   vq,
            "breakout_quality": bq,
            "confidence":       conf,
            "reason":           reason,
        }

    except ValueError:
        raise
    except Exception as exc:
        return _wait_result(direction, f"Внутренняя ошибка: {exc}")


# ─── анализ обоих направлений ─────────────────────────────────────────────────

def analyze_both_directions(df) -> dict:
    """
    Запускает analyze_quality для LONG и SHORT, выбирает итоговый сигнал.

    Возвращает:
        {
            "LONG":  { результат analyze_quality("LONG") },
            "SHORT": { результат analyze_quality("SHORT") },
            "FINAL": {
                "signal":     "LONG" | "SHORT" | "WAIT",
                "confidence": float,
                "label":      str,
                "reason":     str
            }
        }

    Логика FINAL:
        • только LONG подтверждён           → LONG
        • только SHORT подтверждён          → SHORT
        • ни один не подтверждён            → WAIT
        • оба подтверждены, LONG conf > SHORT → LONG
        • оба подтверждены, SHORT conf > LONG → SHORT
        • оба подтверждены, conf равны      → WAIT + "Conflicting equal-confidence signals"
    """
    try:
        long_result = analyze_quality(df, "LONG")
    except Exception as exc:
        long_result = _wait_result("LONG", f"Ошибка LONG-анализа: {exc}")

    try:
        short_result = analyze_quality(df, "SHORT")
    except Exception as exc:
        short_result = _wait_result("SHORT", f"Ошибка SHORT-анализа: {exc}")

    # ── FINAL логика ──────────────────────────────────────────────────────────
    long_confirmed  = bool(long_result.get("confirmed",  False))
    short_confirmed = bool(short_result.get("confirmed", False))

    long_conf_val  = float(long_result.get("confidence",  {}).get("confidence",  0.0))
    short_conf_val = float(short_result.get("confidence", {}).get("confidence", 0.0))

    if not math.isfinite(long_conf_val):
        long_conf_val  = 0.0
    if not math.isfinite(short_conf_val):
        short_conf_val = 0.0

    if long_confirmed and not short_confirmed:
        final_signal = "LONG"
        final_conf   = long_conf_val
        final_reason = (
            f"LONG подтверждён (conf={long_conf_val:.1f}), "
            f"SHORT не подтверждён"
        )

    elif short_confirmed and not long_confirmed:
        final_signal = "SHORT"
        final_conf   = short_conf_val
        final_reason = (
            f"SHORT подтверждён (conf={short_conf_val:.1f}), "
            f"LONG не подтверждён"
        )

    elif not long_confirmed and not short_confirmed:
        final_signal = "WAIT"
        final_conf   = max(long_conf_val, short_conf_val)
        final_reason = "Ни одно направление не подтверждено"

    else:
        # оба подтверждены — выбираем по confidence
        if long_conf_val > short_conf_val:
            final_signal = "LONG"
            final_conf   = long_conf_val
            final_reason = (
                f"Оба подтверждены; выбран LONG "
                f"(conf={long_conf_val:.1f} > SHORT={short_conf_val:.1f})"
            )
        elif short_conf_val > long_conf_val:
            final_signal = "SHORT"
            final_conf   = short_conf_val
            final_reason = (
                f"Оба подтверждены; выбран SHORT "
                f"(conf={short_conf_val:.1f} > LONG={long_conf_val:.1f})"
            )
        else:
            final_signal = "WAIT"
            final_conf   = long_conf_val
            final_reason = "Conflicting equal-confidence signals"

    return {
        "LONG":  long_result,
        "SHORT": short_result,
        "FINAL": {
            "signal":     final_signal,
            "confidence": round(final_conf, 2),
            "label":      confidence_label(final_conf),
            "reason":     final_reason,
        },
    }
