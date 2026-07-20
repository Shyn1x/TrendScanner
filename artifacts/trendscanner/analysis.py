"""
analysis.py
~~~~~~~~~~~
Анализ одного таймфрейма через quality_pipeline + decision_engine.

Публичный интерфейс:
    analyze_timeframe(df) -> dict

Формат результата:
    {
        "trend":            "BULLISH" | "BEARISH" | "NEUTRAL" | "ERROR",
        "signal":           "LONG"    | "SHORT"   | "WAIT",
        "score":            float,   # = confidence, диапазон 0–100
        "confidence":       float,   # 0–100
        "confidence_label": str,     # LOW / MEDIUM / HIGH / VERY HIGH
        "reason":           str,
        "quality": {
            "LONG":  {...},
            "SHORT": {...},
            "FINAL": {...}
        },
        "decision":           "TAKE" | "WATCH" | "SKIP",
        "decision_direction": "LONG" | "SHORT" | "NONE",
        "decision_score":     float,
        "decision_reason":    str,
        "decision_details": {
            "LONG":  {...},
            "SHORT": {...},
            "FINAL": {...}
        }
    }

ИНВАРИАНТ (pipeline):
    Если signal == "LONG" или signal == "SHORT", то одновременно:
    • confidence >= 50
    • качество соответствующего направления имеет confirmed=True
    • pipeline FINAL.signal совпадает с signal
    При нарушении инварианта — принудительно WAIT с причиной.

ИНВАРИАНТ (decision):
    Если decision == "TAKE", то одновременно:
    • decision_direction в (LONG, SHORT)
    • соответствующее directional decision == TAKE
    • breakout confirmed для этого направления
    • confidence >= 70
    • pipeline signal совпадает с decision_direction
    При нарушении — decision = "WATCH", decision_direction = "NONE",
    decision_reason начинается с "Decision invariant violation prevented TAKE:".

ПРИМЕЧАНИЕ:
    signal и decision независимы — допустимо:
        signal=LONG / decision=WATCH
        signal=WAIT / decision=SKIP
    Decision Engine никогда не изменяет поле signal.
"""

from quality_pipeline import analyze_both_directions
from decision_engine  import evaluate_both_directions


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательные
# ─────────────────────────────────────────────────────────────────────────────

def _empty_quality() -> dict:
    """Безопасная пустая структура quality для error-fallback."""
    empty = {"signal": "WAIT", "confirmed": False, "line": None,
             "trend_quality": {}, "volume_quality": {}, "breakout_quality": {},
             "confidence": {"confidence": 0.0, "label": "LOW"},
             "reason": ""}
    return {
        "LONG":  {**empty, "direction": "LONG"},
        "SHORT": {**empty, "direction": "SHORT"},
        "FINAL": {"signal": "WAIT", "confidence": 0.0,
                  "label": "LOW", "reason": ""},
    }


def _empty_decision(reason: str = "") -> dict:
    """Безопасная пустая структура decision для error-fallback."""
    return {
        "decision":           "SKIP",
        "decision_direction": "NONE",
        "decision_score":     0.0,
        "decision_reason":    reason,
        "decision_details": {
            "LONG":  {},
            "SHORT": {},
            "FINAL": {
                "decision":       "SKIP",
                "direction":      "NONE",
                "decision_score": 0.0,
                "confidence":     0.0,
                "reason":         reason,
            },
        },
    }


def _error_result(reason: str, error: bool = False) -> dict:
    """Контролируемый fallback-результат."""
    return {
        "trend":            "ERROR" if error else "NEUTRAL",
        "signal":           "WAIT",
        "score":            0.0,
        "confidence":       0.0,
        "confidence_label": "LOW",
        "reason":           reason,
        "quality":          _empty_quality(),
        **_empty_decision(reason),
    }


def _safe_float(val, default: float = 0.0) -> float:
    """Конвертирует val в float; возвращает default при NaN/inf/ошибке."""
    import math
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  Публичный интерфейс
# ─────────────────────────────────────────────────────────────────────────────

def analyze_timeframe(df) -> dict:
    """
    Анализирует DataFrame одного таймфрейма.

    Использует quality_pipeline.analyze_both_directions(df)
    и decision_engine.evaluate_both_directions(quality_result).

    Parameters
    ----------
    df : pd.DataFrame или None

    Returns
    -------
    dict со стабильным форматом (никогда не выбрасывает исключение).
    """
    # ── базовая защита ────────────────────────────────────────────────────────
    if df is None:
        return _error_result("df is None")

    try:
        import pandas as pd
        if not isinstance(df, pd.DataFrame):
            return _error_result(f"df has unexpected type: {type(df).__name__}")
        if df.empty:
            return _error_result("Empty DataFrame")
    except Exception as exc:
        return _error_result(f"DataFrame check failed: {exc}", error=True)

    # ── вызов пайплайна ───────────────────────────────────────────────────────
    try:
        quality_result = analyze_both_directions(df)
    except Exception as exc:
        return _error_result(f"Pipeline exception: {exc}", error=True)

    # ── разбор результата pipeline ────────────────────────────────────────────
    try:
        final = quality_result.get("FINAL", {})
        if not isinstance(final, dict):
            raise ValueError(f"FINAL is not a dict: {type(final).__name__}")

        final_signal = final.get("signal", "WAIT")
        if final_signal not in ("LONG", "SHORT", "WAIT"):
            final_signal = "WAIT"

        confidence = _safe_float(final.get("confidence", 0.0))
        label      = final.get("label", "")
        if label not in ("LOW", "MEDIUM", "HIGH", "VERY HIGH"):
            label = _fallback_label(confidence)
        reason = str(final.get("reason", ""))

        # ── ИНВАРИАНТ pipeline: активный сигнал должен быть валидным ─────────
        if final_signal in ("LONG", "SHORT"):
            # Проверка 1: confidence >= 50
            if confidence < 50.0:
                return _error_result(
                    f"Invariant violation prevented active signal: "
                    f"signal={final_signal} but confidence={confidence:.1f} < 50. "
                    f"Forced WAIT."
                )

            # Проверка 2: соответствующее направление confirmed=True
            dir_quality   = quality_result.get(final_signal, {})
            dir_confirmed = (
                isinstance(dir_quality, dict)
                and bool(dir_quality.get("confirmed", False))
            )
            if not dir_confirmed:
                return _error_result(
                    f"Invariant violation prevented active signal: "
                    f"signal={final_signal} but {final_signal}.confirmed=False. "
                    f"Forced WAIT."
                )

            # Проверка 3: pipeline FINAL.signal совпадает с final_signal
            pipeline_fs = final.get("signal", "WAIT")
            if pipeline_fs != final_signal:
                return _error_result(
                    f"Invariant violation prevented active signal: "
                    f"final_signal={final_signal} != pipeline FINAL.signal={pipeline_fs}. "
                    f"Forced WAIT."
                )
        # ── конец инварианта pipeline ────────────────────────────────────────

        if final_signal == "LONG":
            trend = "BULLISH"
        elif final_signal == "SHORT":
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

    except Exception as exc:
        return _error_result(f"Result parsing failed: {exc}", error=True)

    # ── Decision Engine ───────────────────────────────────────────────────────
    try:
        decision_result = evaluate_both_directions(quality_result)

        de_final = decision_result.get("FINAL", {}) if isinstance(decision_result, dict) else {}

        decision = str(de_final.get("decision", "SKIP"))
        if decision not in ("TAKE", "WATCH", "SKIP"):
            decision = "SKIP"

        decision_direction = str(de_final.get("direction", "NONE"))
        if decision_direction not in ("LONG", "SHORT", "NONE"):
            decision_direction = "NONE"

        decision_score  = _safe_float(de_final.get("decision_score", 0.0))
        decision_reason = str(de_final.get("reason", ""))

        # ── ИНВАРИАНТ decision: TAKE должен соответствовать всем критериям ───
        if decision == "TAKE":
            violation_parts: list[str] = []

            if decision_direction not in ("LONG", "SHORT"):
                violation_parts.append(
                    f"decision_direction={decision_direction!r} is not LONG/SHORT"
                )
            else:
                dir_dec = decision_result.get(decision_direction, {})
                if not isinstance(dir_dec, dict):
                    violation_parts.append(
                        f"directional result for {decision_direction} is not a dict"
                    )
                else:
                    # Проверка 1: соответствующий directional == TAKE
                    if dir_dec.get("decision") != "TAKE":
                        violation_parts.append(
                            f"{decision_direction} directional decision="
                            f"{dir_dec.get('decision')!r}, expected TAKE"
                        )

                    # Проверка 2: breakout confirmed
                    if not dir_dec.get("breakout_confirmed", False):
                        violation_parts.append(
                            f"{decision_direction} breakout_confirmed=False"
                        )

                    # Проверка 3: confidence >= 70
                    dir_conf = _safe_float(dir_dec.get("confidence") or 0.0)
                    if dir_conf < 70.0:
                        violation_parts.append(
                            f"{decision_direction} confidence={dir_conf:.1f} < 70"
                        )

                    # Проверка 4: pipeline signal совпадает с decision_direction
                    if final_signal != decision_direction:
                        violation_parts.append(
                            f"pipeline signal={final_signal!r} != "
                            f"decision_direction={decision_direction!r}"
                        )

            if violation_parts:
                decision           = "WATCH"
                decision_direction = "NONE"
                decision_reason    = (
                    "Decision invariant violation prevented TAKE: "
                    + "; ".join(violation_parts)
                )
        # ── конец инварианта decision ────────────────────────────────────────

        dec_fields = {
            "decision":           decision,
            "decision_direction": decision_direction,
            "decision_score":     round(decision_score, 2),
            "decision_reason":    decision_reason,
            "decision_details":   decision_result if isinstance(decision_result, dict)
                                  else _empty_decision("invalid decision_result")["decision_details"],
        }

    except Exception as exc:
        dec_fields = _empty_decision(f"Decision Engine exception: {exc}")

    # ── сборка итогового результата ───────────────────────────────────────────
    return {
        "trend":            trend,
        "signal":           final_signal,     # ← pipeline signal, не изменяется DE
        "score":            confidence,
        "confidence":       confidence,
        "confidence_label": label,
        "reason":           reason,
        "quality":          quality_result,
        **dec_fields,
    }


def _fallback_label(confidence: float) -> str:
    """Вычисляет label из числового значения confidence."""
    if confidence >= 85:
        return "VERY HIGH"
    if confidence >= 70:
        return "HIGH"
    if confidence >= 40:
        return "MEDIUM"
    return "LOW"
