"""
multi_tf.py
~~~~~~~~~~~
Мульти-таймфреймный анализ через analysis.analyze_timeframe().

Публичный интерфейс:
    multi_analysis(symbol) -> dict

Формат result["FINAL"]:
    {
        "signal":               "LONG" | "SHORT" | "WAIT",
        "score":                float,          # = confidence, 0–100
        "confidence":           float,          # 0–100
        "directional_score":    float,          # −100…+100
        "confidence_label":     str,
        "long_timeframes":      int,
        "short_timeframes":     int,
        "wait_timeframes":      int,
        "available_timeframes": int,
        "reason":               str,
        "timeframe_components": {
            "<tf>": {
                "signal":           str,
                "confidence":       float | None,
                "base_weight":      float,
                "effective_weight": float,
                "contribution":     float,
                "available":        bool,
                "reason":           str
            },
            ...
        }
    }

ИНВАРИАНТ (агрегация):
    Таймфрейм с signal=LONG/SHORT и confidence<50 не участвует как активный
    сигнал — он учитывается как WAIT при расчёте directional_score.
"""

import math

from scanner   import get_data
from analysis  import analyze_timeframe


# ─────────────────────────────────────────────────────────────────────────────
#  Конфигурация
# ─────────────────────────────────────────────────────────────────────────────

TIMEFRAMES = ["1M", "1w", "1d", "4h", "1h"]

TIMEFRAME_WEIGHTS = {
    "1M": 0.35,
    "1w": 0.25,
    "1d": 0.20,
    "4h": 0.12,
    "1h": 0.08,
}

# Порог directional_score для финального сигнала
_SIGNAL_THRESHOLD = 40.0


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательные
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _confidence_label(score: float) -> str:
    if score >= 85:
        return "VERY HIGH"
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def _empty_final(reason: str) -> dict:
    return {
        "signal":               "WAIT",
        "score":                0.0,
        "confidence":           0.0,
        "directional_score":    0.0,
        "confidence_label":     "LOW",
        "long_timeframes":      0,
        "short_timeframes":     0,
        "wait_timeframes":      0,
        "available_timeframes": 0,
        "reason":               reason,
        "timeframe_components": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Публичный интерфейс
# ─────────────────────────────────────────────────────────────────────────────

def multi_analysis(symbol: str) -> dict:
    """
    Выполняет анализ symbol на всех таймфреймах TIMEFRAMES.

    Параметры
    ---------
    symbol : str   (например "BTC/USDT")

    Возвращает
    ----------
    dict  { tf: analyze_timeframe_result, ..., "FINAL": {...} }

    Исключение одного таймфрейма не останавливает анализ остальных.
    """
    result: dict = {}

    # ── шаг 1: собрать анализ каждого таймфрейма ─────────────────────────────
    for tf in TIMEFRAMES:
        try:
            df       = get_data(symbol, tf)
            analysis = analyze_timeframe(df)
        except Exception as exc:
            analysis = {
                "trend":            "ERROR",
                "signal":           "WAIT",
                "score":            0.0,
                "confidence":       0.0,
                "confidence_label": "LOW",
                "reason":           str(exc),
                "quality":          {},
            }
        result[tf] = analysis

    # ── шаг 2: агрегация ─────────────────────────────────────────────────────
    components: dict[str, dict] = {}

    # Первый проход: пометить available / извлечь данные
    for tf in TIMEFRAMES:
        a         = result[tf]
        signal    = a.get("signal", "WAIT")
        trend     = a.get("trend", "NEUTRAL")
        base_w    = TIMEFRAME_WEIGHTS.get(tf, 0.0)
        conf_raw  = a.get("confidence")
        reason_tf = a.get("reason", "")

        # Таймфрейм считается недоступным, если trend == "ERROR"
        available = (trend != "ERROR") and (conf_raw is not None)
        conf      = _safe_float(conf_raw) if available else None

        components[tf] = {
            "signal":           signal if available else "WAIT",
            "confidence":       conf,
            "base_weight":      base_w,
            "effective_weight": 0.0,   # будет заполнено ниже
            "contribution":     0.0,   # будет заполнено ниже
            "available":        available,
            "reason":           reason_tf,
        }

    # Второй проход: перенормировка весов по доступным таймфреймам
    total_base = sum(
        components[tf]["base_weight"]
        for tf in TIMEFRAMES
        if components[tf]["available"]
    )

    available_tfs  = [tf for tf in TIMEFRAMES if components[tf]["available"]]
    n_available    = len(available_tfs)

    if n_available == 0:
        result["FINAL"] = _empty_final("No available timeframes")
        for tf in TIMEFRAMES:
            components[tf]["effective_weight"] = 0.0
            components[tf]["contribution"]     = 0.0
        result["FINAL"]["timeframe_components"] = components
        return result

    for tf in TIMEFRAMES:
        if not components[tf]["available"] or total_base == 0.0:
            components[tf]["effective_weight"] = 0.0
            components[tf]["contribution"]     = 0.0
        else:
            eff_w = components[tf]["base_weight"] / total_base
            components[tf]["effective_weight"] = eff_w

            sig  = components[tf]["signal"]
            conf = components[tf]["confidence"]
            if conf is None:
                conf = 0.0

            # ── ИНВАРИАНТ агрегации ───────────────────────────────────────────
            # Активный сигнал с confidence < 50 не участвует как LONG/SHORT —
            # он счи`тается WAIT для расчёта directional_score.
            if sig in ("LONG", "SHORT") and conf < 50.0:
                sig = "WAIT"
                components[tf]["signal"] = "WAIT"
            # ── конец инварианта ─────────────────────────────────────────────

            if sig == "LONG":
                contrib = +conf * eff_w
            elif sig == "SHORT":
                contrib = -conf * eff_w
            else:
                contrib = 0.0   # WAIT вклад = 0

            components[tf]["contribution"] = contrib

    # ── шаг 3: сводные счётчики ───────────────────────────────────────────────
    long_tfs  = sum(1 for tf in available_tfs if components[tf]["signal"] == "LONG")
    short_tfs = sum(1 for tf in available_tfs if components[tf]["signal"] == "SHORT")
    wait_tfs  = sum(1 for tf in available_tfs if components[tf]["signal"] == "WAIT")

    directional_score = sum(components[tf]["contribution"] for tf in TIMEFRAMES)
    # ограничить диапазоном −100…+100 (страховка при крайних значениях)
    directional_score = max(-100.0, min(100.0, directional_score))

    agreement_confidence = abs(directional_score)
    agreement_confidence = max(0.0, min(100.0, agreement_confidence))

    # ── шаг 4: финальный сигнал ───────────────────────────────────────────────
    if directional_score >= _SIGNAL_THRESHOLD:
        final_signal = "LONG"
        reason = f"LONG: directional_score={directional_score:.1f} >= {_SIGNAL_THRESHOLD}"
    elif directional_score <= -_SIGNAL_THRESHOLD:
        final_signal = "SHORT"
        reason = f"SHORT: directional_score={directional_score:.1f} <= -{_SIGNAL_THRESHOLD}"
    else:
        final_signal = "WAIT"
        reason = f"WAIT: directional_score={directional_score:.1f} (порог ±{_SIGNAL_THRESHOLD})"

    # ── шаг 5: фильтр конфликта старших таймфреймов (1M ↔ 1w) ────────────────
    sig_1m = components.get("1M", {}).get("signal", "WAIT") if components.get("1M", {}).get("available") else "WAIT"
    sig_1w = components.get("1w", {}).get("signal", "WAIT") if components.get("1w", {}).get("available") else "WAIT"

    htf_conflict = (
        sig_1m != "WAIT" and sig_1w != "WAIT" and sig_1m != sig_1w
    )

    if htf_conflict:
        final_signal = "WAIT"
        reason = (
            f"Конфликт старших таймфреймов: 1M={sig_1m}, 1w={sig_1w}. "
            f"directional_score={directional_score:.1f}"
        )

    label = _confidence_label(agreement_confidence)

    result["FINAL"] = {
        "signal":               final_signal,
        "score":                round(agreement_confidence, 2),
        "confidence":           round(agreement_confidence, 2),
        "directional_score":    round(directional_score, 4),
        "confidence_label":     label,
        "long_timeframes":      long_tfs,
        "short_timeframes":     short_tfs,
        "wait_timeframes":      wait_tfs,
        "available_timeframes": n_available,
        "reason":               reason,
        "timeframe_components": components,
    }

    return result
