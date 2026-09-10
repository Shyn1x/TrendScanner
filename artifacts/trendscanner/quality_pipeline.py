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
        ↓  analyze_market_structure          (один раз на df)
        ↓  score_structure_for_direction     (по одному на каждое direction)
        ↓  calculate_confidence
        →  {direction, signal, confirmed, line, tq, vq, bq, ms, sq, conf, reason}

Функции:
    analyze_quality(df, direction)        → dict (LONG или SHORT)
    analyze_both_directions(df)           → dict (LONG + SHORT + FINAL)

Нет зависимостей от scanner.py, Streamlit, analysis.py, multi_tf.py.
Подключение к Streamlit / analysis.py / multi_tf.py — следующий этап.
"""

from __future__ import annotations

import math

import pandas as pd

from trendlines        import find_pivots, create_trendline
from trend_quality     import calc_trend_quality
from volume_quality    import score_volume
from breakout_quality  import calculate_breakout_quality
from market_structure  import analyze_market_structure
from structure_quality import score_structure_for_direction
from confidence        import calculate_confidence, confidence_label


# ─── константы ────────────────────────────────────────────────────────────────

#: Версия пайплайна. Изменение версии инвалидирует кэш Streamlit.
PIPELINE_VERSION = "0.5-market-structure-1"

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

#: Порог сильного противодействия структуры.
#: Если alignment == OPPOSED и structure_score < этого значения → принудительный WAIT.
STRONG_OPPOSITION_THRESHOLD = 25.0


# ─── вспомогательные ─────────────────────────────────────────────────────────

def _wait_result(direction: str, reason: str, *, analysis_available=False) -> dict:
    """
    Возвращает пустой WAIT-результат с объяснением.
    Используется при любой невозможности рассчитать сигнал.
    """
    return {
        "analysis_available": analysis_available,
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
        "market_structure": None,
        "structure_quality": None,
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


# ─── приватная функция одного направления ────────────────────────────────────

def _analyze_direction(
    df,
    direction: str,
    market_structure_result: dict | None,
) -> dict:
    """
    Запускает все Quality Engines для одного направления.
    Принимает уже вычисленный market_structure_result.

    Внутренняя функция — не является частью публичного API.
    Публичный контракт: analyze_quality(df, direction).
    """
    direction = direction.upper()
    analysis_available = market_structure_result is not None

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
            return _wait_result(direction, reason, analysis_available=True)

        # ── качество трендовой линии ──────────────────────────────────────────
        try:
            tq = calc_trend_quality(df, line, end_index=len(df) - 2)
        except Exception as exc:
            analysis_available = False
            tq = {"trend_quality_score": 0, "reason": str(exc)}

        # ── качество объёма ───────────────────────────────────────────────────
        try:
            vq = score_volume(df)
        except Exception as exc:
            analysis_available = False
            vq = {"volume_score": 0, "reason": str(exc)}

        # ── качество пробоя ───────────────────────────────────────────────────
        try:
            bq = calculate_breakout_quality(df, line, direction)
        except ValueError:
            raise                                  # пробрасываем direction error
        except Exception as exc:
            analysis_available = False
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

        # ── Market Structure (directional adapter) ────────────────────────────
        try:
            sq = score_structure_for_direction(market_structure_result, direction)
        except Exception as exc:
            analysis_available = False
            sq = {
                "structure_score":     0.0,
                "raw_structure_score": 0.0,
                "structure":           "UNKNOWN",
                "direction":           direction,
                "alignment":           "UNKNOWN",
                "bos_direction":       "NONE",
                "choch":               "NONE",
                "available":           False,
                "reason":              str(exc),
            }

        # ── Confidence Engine ─────────────────────────────────────────────────
        try:
            conf = calculate_confidence(
                trend_quality=tq,
                volume_quality=vq,
                breakout_quality=bq,
                structure_quality=sq,
            )
        except Exception as exc:
            analysis_available = False
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

        # Защитный фильтр: сильное противодействие структуры → WAIT
        # Применяется только при alignment == OPPOSED и score < порога.
        # Не применяется при RANGE, UNKNOWN, TRANSITION.
        sq_alignment = sq.get("alignment", "UNKNOWN")
        sq_score_val = sq.get("structure_score", 100.0)
        strong_opposition = (
            sq_alignment == "OPPOSED"
            and sq_score_val < STRONG_OPPOSITION_THRESHOLD
        )

        if strong_opposition:
            signal    = "WAIT"
            confirmed = False
            reason    = (
                f"WAIT: Strong market-structure opposition "
                f"(structure={sq.get('structure', '?')}, "
                f"structure_score={sq_score_val:.1f} < {STRONG_OPPOSITION_THRESHOLD}, "
                f"alignment=OPPOSED)"
            )
        elif bq_confirmed and conf_score >= SIGNAL_THRESHOLD:
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
            "analysis_available": analysis_available,
            "direction":         direction,
            "signal":            signal,
            "confirmed":         confirmed,
            "line":              line,
            "trend_quality":     tq,
            "volume_quality":    vq,
            "breakout_quality":  bq,
            "market_structure":  market_structure_result,
            "structure_quality": sq,
            "confidence":        conf,
            "reason":            reason,
        }

    except ValueError:
        raise
    except Exception as exc:
        return _wait_result(direction, f"Внутренняя ошибка: {exc}")


# ─── публичная функция одного направления ────────────────────────────────────

def analyze_quality(df, direction: str) -> dict:
    """
    Запускает все Quality Engines для одного направления.

    Параметры:
        df        — DataFrame с колонками open, high, low, close [, volume]
        direction — "LONG" (нисходящая линия сопротивления, pivot highs)
                  или "SHORT" (восходящая линия поддержки, pivot lows)

    Возвращает:
        {
            "direction":         "LONG" | "SHORT",
            "signal":            "LONG" | "SHORT" | "WAIT",
            "confirmed":         bool,
            "line":              dict | None,
            "trend_quality":     dict,
            "volume_quality":    dict,
            "breakout_quality":  dict,
            "market_structure":  dict | None,
            "structure_quality": dict,
            "confidence":        dict,
            "reason":            str
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

    # ── вычисляем Market Structure один раз ───────────────────────────────────
    try:
        ms_result = analyze_market_structure(df)
    except Exception:
        ms_result = None

    return _analyze_direction(df, direction, ms_result)


# ─── анализ обоих направлений ─────────────────────────────────────────────────

def analyze_both_directions(df) -> dict:
    """
    Запускает analyze_quality для LONG и SHORT, выбирает итоговый сигнал.
    Market Structure вычисляется ОДИН РАЗ и передаётся в оба направления.

    Возвращает:
        {
            "LONG":  { результат _analyze_direction("LONG") },
            "SHORT": { результат _analyze_direction("SHORT") },
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
    # ── валидация df (ранний выход) ───────────────────────────────────────────
    is_valid, df_reason = _validate_df(df)
    if not is_valid:
        long_result  = _wait_result("LONG",  df_reason)
        short_result = _wait_result("SHORT", df_reason)
        return {
            "LONG":  long_result,
            "SHORT": short_result,
            "FINAL": {
                "signal":     "WAIT",
                "confidence": 0.0,
                "label":      "LOW",
                "reason":     df_reason,
            },
        }

    # ── Market Structure вычисляется один раз ─────────────────────────────────
    try:
        ms_result = analyze_market_structure(df)
    except Exception:
        ms_result = None

    # ── LONG ──────────────────────────────────────────────────────────────────
    try:
        long_result = _analyze_direction(df, "LONG", ms_result)
    except Exception as exc:
        long_result = _wait_result("LONG", f"Ошибка LONG-анализа: {exc}")

    # ── SHORT ─────────────────────────────────────────────────────────────────
    try:
        short_result = _analyze_direction(df, "SHORT", ms_result)
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
