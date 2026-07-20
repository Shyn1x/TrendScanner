"""
analysis.py
~~~~~~~~~~~
Анализ одного таймфрейма через quality_pipeline.

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
        }
    }
"""

from quality_pipeline import analyze_both_directions


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

    Использует quality_pipeline.analyze_both_directions(df).

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

    # ── разбор результата ─────────────────────────────────────────────────────
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

        if final_signal == "LONG":
            trend = "BULLISH"
        elif final_signal == "SHORT":
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        return {
            "trend":            trend,
            "signal":           final_signal,
            "score":            confidence,
            "confidence":       confidence,
            "confidence_label": label,
            "reason":           reason,
            "quality":          quality_result,
        }

    except Exception as exc:
        return _error_result(f"Result parsing failed: {exc}", error=True)


def _fallback_label(confidence: float) -> str:
    """Вычисляет label из числового значения confidence."""
    if confidence >= 85:
        return "VERY HIGH"
    if confidence >= 70:
        return "HIGH"
    if confidence >= 40:
        return "MEDIUM"
    return "LOW"
