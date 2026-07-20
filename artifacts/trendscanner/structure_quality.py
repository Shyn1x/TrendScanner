"""
structure_quality.py
~~~~~~~~~~~~~~~~~~~~
Адаптер между analyze_market_structure() и Confidence Engine.

Преобразует сырой результат Market Structure Engine в directional score:
один и тот же BULLISH structure поддерживает LONG и штрафует SHORT.

Публичный API:
    score_structure_for_direction(structure_result, direction) -> dict

Возвращаемые поля:
    structure_score     — итоговый score с учётом направления, BOS и CHoCH
    raw_structure_score — исходный score из analyze_market_structure
    structure           — строковое состояние (BULLISH, BEARISH, ...)
    direction           — "LONG" | "SHORT"
    alignment           — "ALIGNED" | "OPPOSED" | "NEUTRAL" | "TRANSITION" | "UNKNOWN"
    bos_direction       — направление BOS из market_structure
    choch               — CHoCH-состояние из market_structure
    available           — False только если structure == UNKNOWN или данные недоступны
    reason              — человекочитаемое описание

Нет зависимостей от scanner.py, Streamlit, analysis.py, multi_tf.py,
confidence.py, quality_pipeline.py.
"""

from __future__ import annotations
import math


# ─── вспомогательные константы ───────────────────────────────────────────────

_VALID_DIRECTIONS = {"LONG", "SHORT"}
_VALID_STRUCTURES = {
    "BULLISH",
    "BEARISH",
    "RANGE",
    "TRANSITION_BULLISH",
    "TRANSITION_BEARISH",
    "UNKNOWN",
}

# Максимальный score при наличии CHoCH против тренда
_CHOCH_CAP = 70.0

# BOS-бонус/штраф
_BOS_BONUS   = 15.0
_BOS_PENALTY = 20.0

# CHoCH-штраф если против направления
_CHOCH_PENALTY = 20.0


# ─── недоступный результат ───────────────────────────────────────────────────

def _unknown_result(direction: str, reason: str) -> dict:
    return {
        "structure_score":     0.0,
        "raw_structure_score": 0.0,
        "structure":           "UNKNOWN",
        "direction":           direction,
        "alignment":           "UNKNOWN",
        "bos_direction":       "NONE",
        "choch":               "NONE",
        "available":           False,
        "reason":              reason,
    }


# ─── основная функция ─────────────────────────────────────────────────────────

def score_structure_for_direction(
    structure_result: dict | None,
    direction: str,
) -> dict:
    """
    Преобразует сырой результат analyze_market_structure() в directional score.

    Параметры:
        structure_result — возвращаемый dict от analyze_market_structure(),
                           или None.
        direction        — "LONG" или "SHORT" (регистр игнорируется).

    Возвращает:
        {
            "structure_score":     float,   # итоговый directional score 0–100
            "raw_structure_score": float,   # score из market_structure
            "structure":           str,     # BULLISH / BEARISH / ...
            "direction":           str,     # "LONG" | "SHORT"
            "alignment":           str,     # ALIGNED / OPPOSED / ...
            "bos_direction":       str,     # BULLISH / BEARISH / NONE
            "choch":               str,     # BULLISH / BEARISH / NONE
            "available":           bool,
            "reason":              str,
        }

    Исключения:
        ValueError — если direction не "LONG" и не "SHORT".
    """

    # ── валидация direction ───────────────────────────────────────────────────
    if not isinstance(direction, str) or direction.upper() not in _VALID_DIRECTIONS:
        raise ValueError(
            f"direction должен быть 'LONG' или 'SHORT', получено: {direction!r}"
        )
    direction = direction.upper()

    # ── валидация входных данных ──────────────────────────────────────────────
    if structure_result is None:
        return _unknown_result(direction, "structure_result is None")

    if not isinstance(structure_result, dict) or not structure_result:
        return _unknown_result(
            direction,
            f"structure_result: ожидается непустой dict, "
            f"получен {type(structure_result).__name__}",
        )

    # ── извлечение структуры и raw score ─────────────────────────────────────
    structure: str = str(structure_result.get("structure", "UNKNOWN")).upper()
    if structure not in _VALID_STRUCTURES:
        structure = "UNKNOWN"

    raw_raw = structure_result.get("structure_score", 0)
    try:
        raw_score = float(raw_raw)
    except (TypeError, ValueError):
        raw_score = 0.0
    if not math.isfinite(raw_score):
        raw_score = 0.0
    raw_score = max(0.0, min(100.0, raw_score))

    # ── UNKNOWN недоступен ────────────────────────────────────────────────────
    if structure == "UNKNOWN":
        return _unknown_result(
            direction,
            f"Market structure UNKNOWN (raw_score={raw_score:.1f})",
        )

    # ── BOS ───────────────────────────────────────────────────────────────────
    bos_info     = structure_result.get("bos", {}) or {}
    bos_dir_raw  = str(bos_info.get("direction", "NONE")).upper()
    bos_confirmed = bool(bos_info.get("confirmed", False))
    bos_direction = bos_dir_raw if bos_confirmed else "NONE"

    # ── CHoCH ─────────────────────────────────────────────────────────────────
    choch_raw = str(structure_result.get("choch", "NONE")).upper()
    choch = choch_raw if choch_raw in ("BULLISH", "BEARISH") else "NONE"

    # ── базовый directional score ──────────────────────────────────────────────
    # Зеркальная логика: для LONG — таблица ниже; для SHORT — инвертируем роли.
    aligned_structure    = "BULLISH" if direction == "LONG" else "BEARISH"
    opposed_structure    = "BEARISH" if direction == "LONG" else "BULLISH"
    support_transition   = "TRANSITION_BULLISH" if direction == "LONG" else "TRANSITION_BEARISH"
    oppose_transition    = "TRANSITION_BEARISH" if direction == "LONG" else "TRANSITION_BULLISH"

    if structure == aligned_structure:
        base_score = raw_score
        alignment  = "ALIGNED"
        reason_base = f"{structure} совпадает с {direction}"

    elif structure == opposed_structure:
        base_score = max(0.0, 100.0 - raw_score)
        alignment  = "OPPOSED"
        reason_base = f"{structure} противоположен {direction}"

    elif structure == support_transition:
        base_score = min(raw_score, 65.0)
        alignment  = "TRANSITION"
        reason_base = f"{structure} слабо поддерживает {direction}"

    elif structure == oppose_transition:
        base_score = min(35.0, 100.0 - raw_score)
        alignment  = "TRANSITION"
        reason_base = f"{structure} ослабляет {direction}"

    elif structure == "RANGE":
        base_score = 40.0
        alignment  = "NEUTRAL"
        reason_base = "Боковик — нейтральная оценка"

    else:
        # Не должно достигаться, но защита
        return _unknown_result(direction, f"Неизвестная структура: {structure}")

    score = base_score
    reason_parts = [reason_base]

    # ── BOS-поправка ──────────────────────────────────────────────────────────
    bos_supports_long  = (bos_direction == "BULLISH")
    bos_supports_short = (bos_direction == "BEARISH")

    if bos_confirmed:
        if (direction == "LONG"  and bos_supports_long) or \
           (direction == "SHORT" and bos_supports_short):
            score = min(100.0, score + _BOS_BONUS)
            reason_parts.append(f"BOS {bos_direction} совпадает (+{_BOS_BONUS:.0f})")
        elif bos_direction not in ("NONE", ""):
            score = max(0.0, score - _BOS_PENALTY)
            reason_parts.append(f"BOS {bos_direction} против (−{_BOS_PENALTY:.0f})")

    # ── CHoCH-поправка ────────────────────────────────────────────────────────
    if choch != "NONE":
        choch_aligned = (
            (direction == "LONG"  and choch == "BULLISH") or
            (direction == "SHORT" and choch == "BEARISH")
        )
        if choch_aligned:
            # CHoCH совпадает — ограничиваем максимум
            if score > _CHOCH_CAP:
                score = _CHOCH_CAP
            reason_parts.append(
                f"CHoCH {choch} совпадает — cap {_CHOCH_CAP:.0f}"
            )
        else:
            score = max(0.0, score - _CHOCH_PENALTY)
            reason_parts.append(
                f"CHoCH {choch} против (−{_CHOCH_PENALTY:.0f})"
            )

    # ── финальный clamp ───────────────────────────────────────────────────────
    score = float(max(0.0, min(100.0, score)))

    return {
        "structure_score":     round(score, 2),
        "raw_structure_score": round(raw_score, 2),
        "structure":           structure,
        "direction":           direction,
        "alignment":           alignment,
        "bos_direction":       bos_direction,
        "choch":               choch,
        "available":           True,
        "reason":              "; ".join(reason_parts),
    }
