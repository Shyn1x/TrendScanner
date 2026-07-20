"""
ui_helpers.py
~~~~~~~~~~~~~
Чистые helper-функции для преобразования данных интерфейса.
НЕ импортирует Streamlit.

Публичный интерфейс:
    normalize_timeframe_result(value)               -> dict
    format_directional_score(value)                 -> str
    extract_quality_summary(timeframe_result, dir)  -> dict
    normalize_decision_result(value)                -> dict
    format_score_percent(value)                     -> str
    decision_badge(decision, direction)             -> dict
    summarize_decision_factors(decision_details,
                               max_items=4)         -> dict
    paginate_items(items, page, page_size)          -> dict
"""

import math
from datetime import datetime, timezone
from settings import DEFAULT_RESULT_PAGE_SIZE, MAX_RESULT_PAGE_SIZE

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


# ─────────────────────────────────────────────────────────────────────────────
#  normalize_decision_result
# ─────────────────────────────────────────────────────────────────────────────

_VALID_DECISIONS  = {"TAKE", "WATCH", "SKIP"}
_VALID_DIRECTIONS = {"LONG", "SHORT", "NONE"}


def _safe_float_d(val, default: float = 0.0) -> float:
    """float если конечное, иначе default."""
    f = _safe_float(val)
    return default if f is None else f


def normalize_decision_result(value) -> dict:
    """
    Нормализует любой вариант decision-результата к единому словарю.

    Обрабатывает:
    - полный новый dict со всеми decision-полями;
    - dict без decision-полей (старый формат);
    - None / NaN / inf / неверный тип.

    Всегда возвращает dict:
        {
            "decision":                   "TAKE" | "WATCH" | "SKIP",
            "decision_direction":         "LONG" | "SHORT" | "NONE",
            "decision_score":             float,
            "decision_directional_score": float,
            "decision_reason":            str,
            "decision_details":           dict,
            "decision_counts":            dict,
        }
    Никогда не выбрасывает исключение.
    """
    _empty = {
        "decision":                   "SKIP",
        "decision_direction":         "NONE",
        "decision_score":             0.0,
        "decision_directional_score": 0.0,
        "decision_reason":            "",
        "decision_details":           {},
        "decision_counts":            {},
    }

    if not isinstance(value, dict):
        return _empty.copy()

    def _str_field(key, valid, default):
        v = value.get(key)
        if not isinstance(v, str):
            return default
        v = v.upper().strip()
        return v if v in valid else default

    decision  = _str_field("decision",           _VALID_DECISIONS,  "SKIP")
    direction = _str_field("decision_direction", _VALID_DIRECTIONS, "NONE")
    d_score   = _safe_float_d(value.get("decision_score"), 0.0)
    dd_score  = _safe_float_d(value.get("decision_directional_score"), 0.0)
    reason    = str(value.get("decision_reason") or "")

    details = value.get("decision_details")
    if not isinstance(details, dict):
        details = {}

    counts = value.get("decision_counts")
    if not isinstance(counts, dict):
        counts = {}

    return {
        "decision":                   decision,
        "decision_direction":         direction,
        "decision_score":             d_score,
        "decision_directional_score": dd_score,
        "decision_reason":            reason,
        "decision_details":           details,
        "decision_counts":            counts,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  format_score_percent
# ─────────────────────────────────────────────────────────────────────────────

def format_score_percent(value) -> str:
    """
    Форматирует числовое значение 0–100 как "42%" или "N/A".

    При NaN, inf, None, нечисловом — возвращает "N/A".
    """
    f = _safe_float(value)
    if f is None:
        # _safe_float returns None if non-finite or non-parseable;
        # but our _safe_float returns default=0.0 — let's check directly.
        return "N/A"
    return f"{f:.0f}%"


def _format_score_percent_strict(value) -> str:
    """Строгая версия: отличает 0.0-от-ошибки от реального нуля."""
    if value is None:
        return "N/A"
    try:
        f = float(value)
        if not math.isfinite(f):
            return "N/A"
        return f"{f:.0f}%"
    except (TypeError, ValueError):
        return "N/A"


# Публичная функция использует строгую проверку
def format_score_percent(value) -> str:  # noqa: F811
    """
    Форматирует числовое значение 0–100 как "42%" или "N/A".

    NaN, inf, None, нечисловое → "N/A".
    """
    return _format_score_percent_strict(value)


# ─────────────────────────────────────────────────────────────────────────────
#  decision_badge
# ─────────────────────────────────────────────────────────────────────────────

def decision_badge(decision, direction) -> dict:
    """
    Возвращает данные бейджа для решения и направления.
    НЕ возвращает HTML или Streamlit-объекты.

    Возвращает:
        {
            "label":     str,          # "TAKE LONG" / "WATCH SHORT" / "SKIP"
            "css_class": str,          # "decision-take" / "decision-watch" / "decision-skip"
            "icon":      str,          # эмодзи-иконка
        }
    """
    if not isinstance(decision, str):
        decision = "SKIP"
    decision = decision.upper().strip()
    if decision not in _VALID_DECISIONS:
        decision = "SKIP"

    if not isinstance(direction, str):
        direction = "NONE"
    direction = direction.upper().strip()
    if direction not in _VALID_DIRECTIONS:
        direction = "NONE"

    if decision == "TAKE":
        css = "decision-take"
        if direction == "LONG":
            icon  = "🟢"
            label = "TAKE LONG"
        elif direction == "SHORT":
            icon  = "🔴"
            label = "TAKE SHORT"
        else:
            icon  = "🟡"
            label = "TAKE"

    elif decision == "WATCH":
        css = "decision-watch"
        if direction == "LONG":
            icon  = "🟢"
            label = "WATCH LONG"
        elif direction == "SHORT":
            icon  = "🔴"
            label = "WATCH SHORT"
        else:
            icon  = "🟡"
            label = "WATCH"

    else:  # SKIP
        css   = "decision-skip"
        icon  = "⚪"
        label = "SKIP"

    return {"label": label, "css_class": css, "icon": icon}


# ─────────────────────────────────────────────────────────────────────────────
#  summarize_decision_factors
# ─────────────────────────────────────────────────────────────────────────────

def summarize_decision_factors(decision_details, max_items: int = 4) -> dict:
    """
    Извлекает positive_factors, warning_factors, blockers из decision_details.

    Принимает:
    - результат evaluate_trade_decision (dict с positive_factors / warning_factors / blockers);
    - dict с ключами "LONG" / "SHORT" (decision_details из analyze_timeframe);
    - None / некорректный тип.

    Возвращает:
        {
            "positive": list[str],   # не более max_items
            "warnings": list[str],   # не более max_items
            "blockers": list[str],   # не более max_items (только сообщения)
        }
    """
    try:
        max_items = max(1, int(max_items))
    except (TypeError, ValueError):
        max_items = 4

    _empty = {"positive": [], "warnings": [], "blockers": []}

    if not isinstance(decision_details, dict) or not decision_details:
        return _empty

    # Определяем источник факторов
    if "positive_factors" in decision_details or \
       "warning_factors"  in decision_details or \
       "blockers"         in decision_details:
        # Уже одно направление — используем напрямую
        source = decision_details
    else:
        # Ищем лучшее направление по decision_score
        best = None
        best_score = -1.0
        for key in ("LONG", "SHORT"):
            candidate = decision_details.get(key)
            if isinstance(candidate, dict):
                s = _safe_float_d(candidate.get("decision_score"), -1.0)
                if s > best_score:
                    best_score = s
                    best = candidate
        if best is None:
            return _empty
        source = best

    def _extract_list(key) -> list:
        raw = source.get(key)
        if not isinstance(raw, list):
            return []
        return raw

    pos_raw  = _extract_list("positive_factors")
    warn_raw = _extract_list("warning_factors")
    blk_raw  = _extract_list("blockers")

    pos  = [str(x) for x in pos_raw  if x][:max_items]
    warn = [str(x) for x in warn_raw if x][:max_items]

    # blockers — список dict с "message", либо строки
    blk: list[str] = []
    for b in blk_raw:
        if isinstance(b, dict):
            msg = b.get("message") or b.get("code") or ""
            if msg:
                blk.append(str(msg))
        elif isinstance(b, str) and b:
            blk.append(b)
        if len(blk) >= max_items:
            break

    return {"positive": pos, "warnings": warn, "blockers": blk}


# ─────────────────────────────────────────────────────────────────────────────
#  paginate_items
# ─────────────────────────────────────────────────────────────────────────────

def paginate_items(items, page: int, page_size: int) -> dict:
    """
    Разбивает список на страницы.

    Параметры
    ----------
    items     : list-подобный объект (не изменяется)
    page      : номер страницы (1-based); корректируется если вне диапазона
    page_size : размер страницы; ограничивается [1, MAX_RESULT_PAGE_SIZE]

    Возвращает
    ----------
    {
        "items":       list,
        "page":        int,       # фактическая страница после корректировки
        "page_size":   int,       # фактический размер
        "total_items": int,
        "total_pages": int,
        "has_previous": bool,
        "has_next":    bool,
    }
    """
    # Нормализуем items
    if items is None:
        items = []
    try:
        items = list(items)
    except TypeError:
        items = []

    total_items = len(items)

    # Нормализуем page_size
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = DEFAULT_RESULT_PAGE_SIZE
    page_size = max(1, min(page_size, MAX_RESULT_PAGE_SIZE))

    # Вычисляем total_pages
    total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1

    # Нормализуем page
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, total_pages))

    # Вырезаем нужный срез
    start = (page - 1) * page_size
    end   = start + page_size
    page_items = items[start:end]

    return {
        "items":        page_items,
        "page":         page,
        "page_size":    page_size,
        "total_items":  total_items,
        "total_pages":  total_pages,
        "has_previous": page > 1,
        "has_next":     page < total_pages,
    }
