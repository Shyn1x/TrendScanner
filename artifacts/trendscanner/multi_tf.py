"""
multi_tf.py
~~~~~~~~~~~
Мульти-таймфреймный анализ через analysis.analyze_timeframe().

Публичный интерфейс:
    multi_analysis(symbol) -> dict

Формат result["FINAL"]:
    {
        # ── сигнальная агрегация (без изменений) ──────────────────────────────
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
        "timeframe_components": { "<tf>": {...}, ... },

        # ── Decision Engine агрегация ──────────────────────────────────────────
        "decision":                   "TAKE" | "WATCH" | "SKIP",
        "decision_direction":         "LONG" | "SHORT" | "NONE",
        "decision_score":             float,   # abs(decision_directional_score), 0–100
        "decision_directional_score": float,   # −100…+100
        "decision_reason":            str,
        "decision_counts": {
            "take_long":   int,
            "take_short":  int,
            "watch_long":  int,
            "watch_short": int,
            "skip":        int,
        },
        "decision_timeframe_components": {
            "<tf>": {
                "decision":        str,
                "direction":       str,
                "decision_score":  float | None,
                "base_weight":     float,
                "effective_weight": float,
                "contribution":    float,
                "available":       bool,
                "reason":          str
            }, ...
        }
    }

ИНВАРИАНТ (сигнальная агрегация):
    Таймфрейм с signal=LONG/SHORT и confidence<50 не участвует как активный
    сигнал — он учитывается как WAIT при расчёте directional_score.

ИНВАРИАНТ (Decision-агрегация):
    WATCH получает коэффициент 0.5 к decision_score.
    Старшие TF (1M, 1w) с TAKE в противоположном направлении блокируют TAKE MTF.
    FINAL.signal == WAIT запрещает TAKE MTF.
"""

import math

from scanner  import get_data
from analysis import analyze_timeframe


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

# Порог decision_directional_score для MTF TAKE
_DECISION_TAKE_THRESHOLD = 55.0

# Порог decision_directional_score для MTF WATCH
_DECISION_WATCH_THRESHOLD = 25.0


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


def _empty_decision_counts() -> dict:
    return {"take_long": 0, "take_short": 0,
            "watch_long": 0, "watch_short": 0, "skip": 0}


def _empty_final(reason: str) -> dict:
    return {
        # сигнальная агрегация
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
        # decision агрегация
        "decision":                   "SKIP",
        "decision_direction":         "NONE",
        "decision_score":             0.0,
        "decision_directional_score": 0.0,
        "decision_reason":            "No available decision data",
        "decision_counts":            _empty_decision_counts(),
        "decision_timeframe_components": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Decision-агрегация
# ─────────────────────────────────────────────────────────────────────────────

def _build_decision_component(
    tf: str,
    result_tf: dict,
    comp: dict,   # уже заполненный signal-компонент с effective_weight
) -> dict:
    """Строит decision-компонент для одного таймфрейма."""
    is_avail  = comp["available"]
    eff_w     = comp["effective_weight"]
    base_w    = comp["base_weight"]

    if not is_avail:
        return {
            "decision":         "SKIP",
            "direction":        "NONE",
            "decision_score":   None,
            "base_weight":      base_w,
            "effective_weight": eff_w,
            "contribution":     0.0,
            "available":        False,
            "reason":           result_tf.get("reason", "Timeframe unavailable"),
        }

    # Извлекаем decision-поля из analyze_timeframe (они всегда присутствуют)
    tf_dec   = str(result_tf.get("decision",           "SKIP"))
    tf_dir   = str(result_tf.get("decision_direction", "NONE"))
    tf_dscore = _safe_float(result_tf.get("decision_score", 0.0))
    tf_dreason = str(result_tf.get("decision_reason",  ""))

    if tf_dec not in ("TAKE", "WATCH", "SKIP"):
        tf_dec = "SKIP"
    if tf_dir not in ("LONG", "SHORT", "NONE"):
        tf_dir = "NONE"

    # Вклад по формуле спецификации
    if   tf_dec == "TAKE"  and tf_dir == "LONG":
        contrib = +tf_dscore * eff_w
    elif tf_dec == "TAKE"  and tf_dir == "SHORT":
        contrib = -tf_dscore * eff_w
    elif tf_dec == "WATCH" and tf_dir == "LONG":
        contrib = +tf_dscore * eff_w * 0.5
    elif tf_dec == "WATCH" and tf_dir == "SHORT":
        contrib = -tf_dscore * eff_w * 0.5
    else:
        contrib = 0.0   # SKIP или WATCH/NONE

    return {
        "decision":         tf_dec,
        "direction":        tf_dir,
        "decision_score":   round(tf_dscore, 2),
        "base_weight":      base_w,
        "effective_weight": round(eff_w, 6),
        "contribution":     round(contrib, 4),
        "available":        True,
        "reason":           tf_dreason,
    }


def _aggregate_decision(
    result:     dict,
    components: dict,   # signal-компоненты с effective_weight
    final_signal: str,
) -> dict:
    """
    Агрегирует decision-данные всех таймфреймов и возвращает dict
    со всеми decision-полями для result["FINAL"].
    """
    # ── строим decision-компоненты ────────────────────────────────────────────
    d_comps: dict[str, dict] = {}
    for tf in TIMEFRAMES:
        d_comps[tf] = _build_decision_component(
            tf, result.get(tf, {}), components.get(tf, {
                "available": False, "effective_weight": 0.0,
                "base_weight": TIMEFRAME_WEIGHTS.get(tf, 0.0),
            })
        )

    # ── нет данных вовсе ──────────────────────────────────────────────────────
    any_available = any(d_comps[tf]["available"] for tf in TIMEFRAMES)
    if not any_available:
        return {
            "decision":                   "SKIP",
            "decision_direction":         "NONE",
            "decision_score":             0.0,
            "decision_directional_score": 0.0,
            "decision_reason":            "No available decision data",
            "decision_counts":            _empty_decision_counts(),
            "decision_timeframe_components": d_comps,
        }

    # ── decision_directional_score ────────────────────────────────────────────
    d_score = sum(d_comps[tf]["contribution"] for tf in TIMEFRAMES)
    d_score = max(-100.0, min(100.0, d_score))

    # ── счётчики ──────────────────────────────────────────────────────────────
    counts = _empty_decision_counts()
    for tf in TIMEFRAMES:
        dc = d_comps[tf]
        if not dc["available"]:
            counts["skip"] += 1
            continue
        if   dc["decision"] == "TAKE"  and dc["direction"] == "LONG":
            counts["take_long"]  += 1
        elif dc["decision"] == "TAKE"  and dc["direction"] == "SHORT":
            counts["take_short"] += 1
        elif dc["decision"] == "WATCH" and dc["direction"] == "LONG":
            counts["watch_long"] += 1
        elif dc["decision"] == "WATCH" and dc["direction"] == "SHORT":
            counts["watch_short"] += 1
        else:
            counts["skip"] += 1

    # ── проверяем TAKE на старших таймфреймах (1M, 1w) ───────────────────────
    #   HTF TAKE SHORT → блокирует MTF TAKE LONG
    #   HTF TAKE LONG  → блокирует MTF TAKE SHORT
    htf_take_long = any(
        d_comps.get(tf, {}).get("decision") == "TAKE"
        and d_comps.get(tf, {}).get("direction") == "LONG"
        and d_comps.get(tf, {}).get("available", False)
        for tf in ("1M", "1w")
    )
    htf_take_short = any(
        d_comps.get(tf, {}).get("decision") == "TAKE"
        and d_comps.get(tf, {}).get("direction") == "SHORT"
        and d_comps.get(tf, {}).get("available", False)
        for tf in ("1M", "1w")
    )

    # ── определяем MTF-решение ────────────────────────────────────────────────
    mtf_decision   = "SKIP"
    mtf_direction  = "NONE"
    reason_parts: list[str] = []

    # TAKE LONG
    if (
        d_score >= _DECISION_TAKE_THRESHOLD
        and counts["take_long"] >= 1
        and not htf_take_short
        and final_signal not in ("SHORT", "WAIT")
    ):
        mtf_decision  = "TAKE"
        mtf_direction = "LONG"
        reason_parts.append(
            f"TAKE LONG: score={d_score:.1f}, {counts['take_long']} TF(s) TAKE LONG"
        )

    # TAKE SHORT
    elif (
        d_score <= -_DECISION_TAKE_THRESHOLD
        and counts["take_short"] >= 1
        and not htf_take_long
        and final_signal not in ("LONG", "WAIT")
    ):
        mtf_decision  = "TAKE"
        mtf_direction = "SHORT"
        reason_parts.append(
            f"TAKE SHORT: score={d_score:.1f}, {counts['take_short']} TF(s) TAKE SHORT"
        )

    # WATCH LONG: score in [+25, +55) OR score>=+55 but TAKE blocked
    elif d_score >= _DECISION_WATCH_THRESHOLD:
        mtf_decision  = "WATCH"
        mtf_direction = "LONG"
        if d_score >= _DECISION_TAKE_THRESHOLD:
            blocked: list[str] = []
            if htf_take_short:
                blocked.append("HTF conflict (TAKE SHORT on 1M/1w)")
            if final_signal == "SHORT":
                blocked.append("FINAL.signal=SHORT")
            if final_signal == "WAIT":
                blocked.append("FINAL.signal=WAIT prohibits TAKE")
            if counts["take_long"] == 0:
                blocked.append("no TAKE LONG timeframe")
            reason_parts.append(
                f"WATCH LONG (score={d_score:.1f} >= {_DECISION_TAKE_THRESHOLD}, "
                f"TAKE blocked: {'; '.join(blocked) or 'unknown'})"
            )
        else:
            reason_parts.append(
                f"WATCH LONG: score={d_score:.1f} "
                f"in [{_DECISION_WATCH_THRESHOLD}, {_DECISION_TAKE_THRESHOLD})"
            )

    # WATCH SHORT: score in (-55, -25] OR score<=-55 but TAKE blocked
    elif d_score <= -_DECISION_WATCH_THRESHOLD:
        mtf_decision  = "WATCH"
        mtf_direction = "SHORT"
        if d_score <= -_DECISION_TAKE_THRESHOLD:
            blocked = []
            if htf_take_long:
                blocked.append("HTF conflict (TAKE LONG on 1M/1w)")
            if final_signal == "LONG":
                blocked.append("FINAL.signal=LONG")
            if final_signal == "WAIT":
                blocked.append("FINAL.signal=WAIT prohibits TAKE")
            if counts["take_short"] == 0:
                blocked.append("no TAKE SHORT timeframe")
            reason_parts.append(
                f"WATCH SHORT (score={d_score:.1f} <= -{_DECISION_TAKE_THRESHOLD}, "
                f"TAKE blocked: {'; '.join(blocked) or 'unknown'})"
            )
        else:
            reason_parts.append(
                f"WATCH SHORT: score={d_score:.1f} "
                f"in (-{_DECISION_TAKE_THRESHOLD}, -{_DECISION_WATCH_THRESHOLD}]"
            )

    else:
        reason_parts.append(
            f"SKIP: score={d_score:.1f} within ±{_DECISION_WATCH_THRESHOLD}"
        )

    mtf_decision_score = round(min(100.0, max(0.0, abs(d_score))), 2)

    return {
        "decision":                   mtf_decision,
        "decision_direction":         mtf_direction,
        "decision_score":             mtf_decision_score,
        "decision_directional_score": round(d_score, 4),
        "decision_reason":            "; ".join(reason_parts),
        "decision_counts":            counts,
        "decision_timeframe_components": d_comps,
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
                # decision fallback
                "decision":           "SKIP",
                "decision_direction": "NONE",
                "decision_score":     0.0,
                "decision_reason":    f"Timeframe exception: {exc}",
                "decision_details":   {},
            }
        result[tf] = analysis

    # ── шаг 2: агрегация сигналов ─────────────────────────────────────────────
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

    available_tfs = [tf for tf in TIMEFRAMES if components[tf]["available"]]
    n_available   = len(available_tfs)

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
            if sig in ("LONG", "SHORT") and conf < 50.0:
                sig = "WAIT"
                components[tf]["signal"] = "WAIT"
            # ── конец инварианта ─────────────────────────────────────────────

            if sig == "LONG":
                contrib = +conf * eff_w
            elif sig == "SHORT":
                contrib = -conf * eff_w
            else:
                contrib = 0.0

            components[tf]["contribution"] = contrib

    # ── шаг 3: сводные счётчики (сигнальные) ─────────────────────────────────
    long_tfs  = sum(1 for tf in available_tfs if components[tf]["signal"] == "LONG")
    short_tfs = sum(1 for tf in available_tfs if components[tf]["signal"] == "SHORT")
    wait_tfs  = sum(1 for tf in available_tfs if components[tf]["signal"] == "WAIT")

    directional_score = sum(components[tf]["contribution"] for tf in TIMEFRAMES)
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

    # ── шаг 5: фильтр конфликта старших таймфреймов ───────────────────────────
    sig_1m = (
        components.get("1M", {}).get("signal", "WAIT")
        if components.get("1M", {}).get("available") else "WAIT"
    )
    sig_1w = (
        components.get("1w", {}).get("signal", "WAIT")
        if components.get("1w", {}).get("available") else "WAIT"
    )

    htf_signal_conflict = (
        sig_1m != "WAIT" and sig_1w != "WAIT" and sig_1m != sig_1w
    )

    if htf_signal_conflict:
        final_signal = "WAIT"
        reason = (
            f"Конфликт старших таймфреймов: 1M={sig_1m}, 1w={sig_1w}. "
            f"directional_score={directional_score:.1f}"
        )

    label = _confidence_label(agreement_confidence)

    # ── шаг 6: Decision-агрегация ─────────────────────────────────────────────
    try:
        dec_agg = _aggregate_decision(result, components, final_signal)
    except Exception as exc:
        dec_agg = {
            "decision":                   "SKIP",
            "decision_direction":         "NONE",
            "decision_score":             0.0,
            "decision_directional_score": 0.0,
            "decision_reason":            f"Decision aggregation exception: {exc}",
            "decision_counts":            _empty_decision_counts(),
            "decision_timeframe_components": {},
        }

    # ── шаг 7: сборка FINAL ───────────────────────────────────────────────────
    result["FINAL"] = {
        # сигнальная агрегация (без изменений)
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
        # decision агрегация
        **dec_agg,
    }

    return result
