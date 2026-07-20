"""
top_setups.py
~~~~~~~~~~~~~
Top Setups ranking engine.

НЕ импортирует Streamlit.
НЕ изменяет торговую логику.

Публичный интерфейс:
    build_top_setups(symbol_results, limit, min_decision_score,
                     include_watch, include_skip) -> list[dict]
"""

import math
from settings import (
    TOP_SETUPS_LIMIT,
    TOP_SETUPS_MIN_DECISION_SCORE,
    SHOW_WATCH_IN_TOP_SETUPS,
    SHOW_SKIP_IN_TOP_SETUPS,
    MAX_RESULT_PAGE_SIZE,
)

# ── Внутренние утилиты ─────────────────────────────────────────────────────────

_VALID_DECISIONS  = {"TAKE", "WATCH", "SKIP"}
_VALID_DIRECTIONS = {"LONG", "SHORT", "NONE"}
_VALID_SIGNALS    = {"LONG", "SHORT", "WAIT", "ERROR"}
_VALID_LABELS     = {"LOW", "MEDIUM", "HIGH", "VERY HIGH"}

# Порядок сортировки категорий (меньше — выше)
_DECISION_ORDER = {"TAKE": 0, "WATCH": 1, "SKIP": 2}


def _safe_float(val, default: float = 0.0) -> float:
    """float если конечное, иначе default."""
    if val is None:
        return default
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _safe_str(val, valid: set, default: str) -> str:
    if not isinstance(val, str):
        return default
    v = val.upper().strip()
    return v if v in valid else default


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ── Основная функция ───────────────────────────────────────────────────────────

def build_top_setups(
    symbol_results: dict,
    limit: int = TOP_SETUPS_LIMIT,
    min_decision_score: float = TOP_SETUPS_MIN_DECISION_SCORE,
    include_watch: bool = SHOW_WATCH_IN_TOP_SETUPS,
    include_skip: bool = SHOW_SKIP_IN_TOP_SETUPS,
) -> list[dict]:
    """
    Строит ранжированный список Top Setups из результатов multi_analysis.

    Параметры
    ----------
    symbol_results : dict
        {"BTC/USDT": multi_analysis_result, ...}
        Входной словарь НЕ изменяется.
    limit : int
        Количество записей в результате. Ограничивается [1, MAX_RESULT_PAGE_SIZE].
    min_decision_score : float
        Минимальный decision_score для WATCH-записей.
    include_watch : bool
        Включать ли WATCH в результат.
    include_skip : bool
        Включать ли SKIP в результат.

    Возвращает
    ----------
    list[dict]  — каждая запись:
        {
            "rank": int,
            "symbol": str,
            "decision": "TAKE" | "WATCH" | "SKIP",
            "direction": "LONG" | "SHORT" | "NONE",
            "decision_score": float,
            "confidence": float,
            "confidence_label": str,
            "signal": "LONG" | "SHORT" | "WAIT",
            "directional_score": float,
            "decision_reason": str,
            "available_timeframes": int,
            "take_count": int,
            "watch_count": int,
            "raw_result": dict,  # для перехода к детальному отображению
        }
    """
    # Нормализуем limit
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = TOP_SETUPS_LIMIT
    limit = max(1, min(limit, MAX_RESULT_PAGE_SIZE))

    candidates: list[dict] = []

    for symbol, result in symbol_results.items():
        symbol = str(symbol)

        # Защита от None / не-dict
        if not isinstance(result, dict):
            continue

        # Сигнал ошибки
        if "_error" in result:
            continue

        final = result.get("FINAL")
        if not isinstance(final, dict):
            continue

        # ── Извлекаем поля ─────────────────────────────────────────────────────
        decision  = _safe_str(final.get("decision"),           _VALID_DECISIONS,  "SKIP")
        direction = _safe_str(final.get("decision_direction"), _VALID_DIRECTIONS, "NONE")
        d_score   = _safe_float(final.get("decision_score"),   0.0)
        conf      = _safe_float(final.get("confidence"),       0.0)
        conf      = max(0.0, min(100.0, conf))
        conf_lbl  = _safe_str(final.get("confidence_label"),   _VALID_LABELS,     "LOW")
        signal    = _safe_str(final.get("signal"),             _VALID_SIGNALS,    "WAIT")
        ds        = _safe_float(final.get("directional_score"), 0.0)
        reason    = str(final.get("decision_reason") or "")
        avail_tfs = _safe_int(final.get("available_timeframes"), 0)

        counts      = final.get("decision_counts") or {}
        take_count  = _safe_int(counts.get("take_long", 0) + counts.get("take_short", 0), 0)
        watch_count = _safe_int(counts.get("watch_long", 0) + counts.get("watch_short", 0), 0)

        # ── Фильтрация ─────────────────────────────────────────────────────────

        if decision == "TAKE":
            # TAKE: всегда включаем если направление задано
            if direction == "NONE":
                continue

        elif decision == "WATCH":
            if not include_watch:
                continue
            if d_score < min_decision_score:
                continue
            # WATCH с direction=NONE допускается только при include_skip
            if direction == "NONE" and not include_skip:
                continue

        elif decision == "SKIP":
            if not include_skip:
                continue

        else:
            # Неизвестное decision — пропускаем
            continue

        candidates.append({
            "symbol":              symbol,
            "decision":           decision,
            "direction":          direction,
            "decision_score":     d_score,
            "confidence":         conf,
            "confidence_label":   conf_lbl,
            "signal":             signal,
            "directional_score":  ds,
            "decision_reason":    reason,
            "available_timeframes": avail_tfs,
            "take_count":         take_count,
            "watch_count":        watch_count,
            "raw_result":         result,  # ссылка, не копия — для навигации
        })

    # ── Сортировка ─────────────────────────────────────────────────────────────
    # 1. Категория: TAKE < WATCH < SKIP
    # 2. decision_score по убыванию
    # 3. confidence по убыванию
    # 4. available_timeframes по убыванию
    # 5. symbol по алфавиту (стабильность)
    candidates.sort(key=lambda e: (
        _DECISION_ORDER.get(e["decision"], 99),
        -e["decision_score"],
        -e["confidence"],
        -e["available_timeframes"],
        e["symbol"],
    ))

    # ── Лимит и ранжирование ───────────────────────────────────────────────────
    top = candidates[:limit]
    for rank, entry in enumerate(top, start=1):
        entry["rank"] = rank

    return top
