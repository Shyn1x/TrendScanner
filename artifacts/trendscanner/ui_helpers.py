"""
ui_helpers.py
~~~~~~~~~~~~~
Чистые helper-функции для преобразования данных интерфейса.
НЕ импортирует Streamlit.

Публичный интерфейс:
    normalize_timeframe_result(value)               -> dict
    format_directional_score(value)                 -> str
    extract_quality_summary(timeframe_result, dir)  -> dict
"""

import math

# ─────────────────────────────────────────────────────────────────────────────
#  Внутренние утилиты
# ─────────────────────────────────────────────────────────────────────────────

_VALID_SIGNALS = {"LONG", "SHORT", "WAIT", "ERROR"}
_VALID_LABELS  = {"LOW", "MEDIUM", "HIGH", "VERY HIGH"}
_TREND_MAP     = {
    "LONG":  "BULLISH",
    "SHORT": "BEARISH",
    "WAIT":  "NEUTRAL",
    "ERROR": "ERROR",
}


def _safe_float(val):
    """float если конечное, иначе None."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _fmt_score(val) -> str:
    """Форматирует score 0–100 как целое или N/A."""
    f = _safe_float(val)
    return "N/A" if f is None else f"{f:.0f}"


def _fmt_float(val, decimals: int = 1) -> str:
    """Форматирует число с decimals знаками или N/A."""
    f = _safe_float(val)
    return "N/A" if f is None else f"{f:.{decimals}f}"


def _fmt_weight(val) -> str:
    """Форматирует вес 0–1 как процент или N/A."""
    f = _safe_float(val)
    return "N/A" if f is None else f"{f * 100:.0f}%"


def _clamp_confidence(val) -> float:
    f = _safe_float(val)
    if f is None:
        return 0.0
    return max(0.0, min(100.0, f))


# ─────────────────────────────────────────────────────────────────────────────
#  normalize_timeframe_result
# ─────────────────────────────────────────────────────────────────────────────

def normalize_timeframe_result(value) -> dict:
    """
    Нормализует результат таймфрейма к единому формату.

    Обрабатывает:
    - новый dict-формат (из analyze_timeframe);
    - старый строковый формат ("LONG" / "SHORT" / "WAIT" / "ERROR");
    - None / отсутствующее значение.

    Всегда возвращает dict с ключами:
        signal, trend, confidence, confidence_label, reason, quality
    Никогда не выбрасывает исключение.
    """
    if isinstance(value, dict):
        signal = str(value.get("signal", "WAIT")).upper().strip()
        if signal not in _VALID_SIGNALS:
            signal = "WAIT"

        conf  = _clamp_confidence(value.get("confidence", 0.0))
        label = str(value.get("confidence_label", ""))
        if label not in _VALID_LABELS:
            label = "LOW"

        quality = value.get("quality")
        return {
            "signal":           signal,
            "trend":            str(value.get("trend", "-")),
            "confidence":       conf,
            "confidence_label": label,
            "reason":           str(value.get("reason", "")),
            "quality":          quality if isinstance(quality, dict) else {},
        }

    if isinstance(value, str):
        sig = value.upper().strip()
        if sig not in _VALID_SIGNALS:
            sig = "WAIT"
        return {
            "signal":           sig,
            "trend":            _TREND_MAP.get(sig, "-"),
            "confidence":       0.0,
            "confidence_label": "LOW",
            "reason":           "",
            "quality":          {},
        }

    # None или неизвестный тип
    return {
        "signal":           "WAIT",
        "trend":            "-",
        "confidence":       0.0,
        "confidence_label": "LOW",
        "reason":           "No data",
        "quality":          {},
    }


# ─────────────────────────────────────────────────────────────────────────────
#  format_directional_score
# ─────────────────────────────────────────────────────────────────────────────

def format_directional_score(value) -> str:
    """
    Форматирует directional_score со знаком.

    +42.5  /  -61.2  /  0.0

    При NaN, inf, нечисловом — возвращает "0.0".
    """
    f = _safe_float(value)
    if f is None:
        return "0.0"
    if f > 0:
        return f"+{f:.1f}"
    if f < 0:
        return f"{f:.1f}"
    return "0.0"


# ─────────────────────────────────────────────────────────────────────────────
#  extract_quality_summary
# ─────────────────────────────────────────────────────────────────────────────

def _empty_comp() -> dict:
    return {"score": "N/A", "effective_weight": "N/A",
            "contribution": "N/A", "available": False}


def _empty_quality_summary(direction: str) -> dict:
    return {
        "direction":            direction,
        "signal":               "WAIT",
        "confirmed":            False,
        "confidence":           "N/A",
        "confidence_label":     "N/A",
        "reason":               "",
        "trend_quality_score":  "N/A",
        "volume_quality_score": "N/A",
        "breakout_quality_score": "N/A",
        "breakout_confirmed":   False,
        "components": {
            "trend_quality":    _empty_comp(),
            "volume_quality":   _empty_comp(),
            "breakout_quality": _empty_comp(),
        },
    }


def extract_quality_summary(timeframe_result, direction: str) -> dict:
    """
    Извлекает quality-сводку для одного направления (LONG или SHORT)
    из результата analyze_timeframe.

    Возвращает safe-dict с N/A для отсутствующих значений.
    Никогда не выбрасывает исключение.

    Parameters
    ----------
    timeframe_result : dict (из analyze_timeframe или normalize_timeframe_result)
    direction        : "LONG" или "SHORT"
    """
    direction = str(direction).upper().strip()
    if direction not in ("LONG", "SHORT"):
        direction = "LONG"

    empty = _empty_quality_summary(direction)

    if not isinstance(timeframe_result, dict):
        return empty

    # Поддержка прямой передачи результата normalize_timeframe_result
    quality = timeframe_result.get("quality", {})
    if not isinstance(quality, dict) or not quality:
        return empty

    dir_data = quality.get(direction, {})
    if not isinstance(dir_data, dict):
        return empty

    # ── confidence sub-dict ───────────────────────────────────────────────────
    conf_dict  = dir_data.get("confidence", {})
    conf_dict  = conf_dict if isinstance(conf_dict, dict) else {}
    components = conf_dict.get("components", {})
    components = components if isinstance(components, dict) else {}

    def _get_comp(key: str) -> dict:
        c = components.get(key, {})
        if not isinstance(c, dict):
            return _empty_comp()
        available = bool(c.get("available", False))
        score_raw = c.get("score") if available else None
        return {
            "score":            _fmt_score(score_raw) if available else "N/A",
            "effective_weight": _fmt_weight(c.get("effective_weight")),
            "contribution":     _fmt_float(c.get("contribution"), 1),
            "available":        available,
        }

    # ── top-level quality dicts ───────────────────────────────────────────────
    tq = dir_data.get("trend_quality",    {})
    vq = dir_data.get("volume_quality",   {})
    bq = dir_data.get("breakout_quality", {})

    tq = tq if isinstance(tq, dict) else {}
    vq = vq if isinstance(vq, dict) else {}
    bq = bq if isinstance(bq, dict) else {}

    tq_score = tq.get("trend_quality_score")
    vq_score = vq.get("volume_score")
    bq_score = bq.get("breakout_score")
    bq_confirmed = bool(bq.get("confirmed", False))

    conf_val   = conf_dict.get("confidence")
    conf_label = str(conf_dict.get("label", "")) or "N/A"

    return {
        "direction":              direction,
        "signal":                 str(dir_data.get("signal", "WAIT")),
        "confirmed":              bool(dir_data.get("confirmed", False)),
        "confidence":             _fmt_float(conf_val, 1),
        "confidence_label":       conf_label,
        "reason":                 str(dir_data.get("reason", "")),
        "trend_quality_score":    _fmt_score(tq_score),
        "volume_quality_score":   _fmt_score(vq_score),
        "breakout_quality_score": _fmt_score(bq_score),
        "breakout_confirmed":     bq_confirmed,
        "components": {
            "trend_quality":    _get_comp("trend_quality"),
            "volume_quality":   _get_comp("volume_quality"),
            "breakout_quality": _get_comp("breakout_quality"),
        },
    }
