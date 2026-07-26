"""
context_trigger_shadow.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Read-only experimental Shadow Context + Trigger model for Strategy A.

This module only reads already-computed production fields from all_results.
It does not recalculate indicators and it does not mutate input data.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from quality_pipeline import SIGNAL_THRESHOLD


_TRIGGER_TIMEFRAMES = ("4h", "1h")
_CONTEXT_TIMEFRAMES = ("1w", "1d")
_VALID_DIRECTIONS = ("LONG", "SHORT")
_VALID_CONTEXTS = ("SUPPORTS", "OPPOSES", "MIXED", "NEUTRAL", "UNAVAILABLE")
_HARD_OPPOSITION_CODES = {"STRONG_STRUCTURE_OPPOSITION"}


def _safe_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if value else default


def _safe_upper(value: Any, default: str = "") -> str:
    return _safe_str(value, default=default).upper()


def _get_tf_result(symbol_result: dict, timeframe: str) -> dict | None:
    if not isinstance(symbol_result, dict):
        return None
    tf_result = symbol_result.get(timeframe)
    return tf_result if isinstance(tf_result, dict) else None


def _extract_direction_block(tf_result: dict | None, direction: str) -> tuple[dict, dict]:
    if not isinstance(tf_result, dict):
        return {}, {}

    quality = tf_result.get("quality")
    if not isinstance(quality, dict):
        return {}, {}

    dir_quality = quality.get(direction)
    if not isinstance(dir_quality, dict):
        return {}, {}

    decision_details = tf_result.get("decision_details")
    if not isinstance(decision_details, dict):
        return dir_quality, {}

    detail = decision_details.get(direction)
    return dir_quality, detail if isinstance(detail, dict) else {}


def _list_codes(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    codes: list[str] = []
    for item in items:
        if isinstance(item, dict):
            code = item.get("code")
            if isinstance(code, str) and code:
                codes.append(code)
    return codes


def _list_strings(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        if isinstance(item, str) and item:
            values.append(item)
    return values


def _context_label_for_block(
    dir_quality: dict,
    detail: dict,
    direction: str,
) -> tuple[str, str]:
    if not isinstance(dir_quality, dict) or not dir_quality:
        return "UNAVAILABLE", "directional quality is unavailable"

    signal = _safe_upper(dir_quality.get("signal"), default="WAIT")
    confirmed = bool(dir_quality.get("confirmed", False))
    confidence_block = dir_quality.get("confidence")
    confidence_value = _safe_float(confidence_block.get("confidence") if isinstance(confidence_block, dict) else None)
    confidence_label = _safe_upper(confidence_block.get("label") if isinstance(confidence_block, dict) else None, default="LOW")
    structure_block = dir_quality.get("structure_quality")
    structure_alignment = _safe_upper(structure_block.get("alignment") if isinstance(structure_block, dict) else None, default="UNKNOWN")
    structure_score = _safe_float(structure_block.get("structure_score") if isinstance(structure_block, dict) else None)
    trend_score = _safe_float((dir_quality.get("trend_quality") or {}).get("trend_quality_score"))

    blockers = _list_codes(detail.get("blockers"))
    warnings = _list_strings(detail.get("warning_factors") or detail.get("warnings"))

    if not direction or direction not in _VALID_DIRECTIONS:
        return "UNAVAILABLE", "no trigger direction available"

    if structure_alignment == "OPPOSED" or any(code in _HARD_OPPOSITION_CODES for code in blockers):
        return (
            "OPPOSES",
            f"structure alignment is OPPOSED (structure_score={structure_score if structure_score is not None else 'N/A'})",
        )

    trend_supports = signal == direction
    trend_opposes = signal in _VALID_DIRECTIONS and signal != direction
    strong_confidence = confidence_label in {"HIGH", "VERY HIGH"}

    if trend_supports and structure_alignment == "ALIGNED" and (strong_confidence or confirmed):
        return (
            "SUPPORTS",
            f"trend={signal}, alignment={structure_alignment}, confidence={confidence_value if confidence_value is not None else 'N/A'}",
        )

    if trend_supports and structure_alignment in {"ALIGNED", "NEUTRAL", "TRANSITION"} and strong_confidence:
        return (
            "SUPPORTS",
            f"trend={signal}, alignment={structure_alignment}, confidence={confidence_label}",
        )

    if trend_opposes:
        return (
            "MIXED",
            f"trend={signal} conflicts with directional context {direction}",
        )

    if signal == "WAIT":
        if structure_alignment == "NEUTRAL":
            return ("NEUTRAL", "signal=WAIT and structure is neutral")
        if structure_alignment == "TRANSITION":
            return ("MIXED", "signal=WAIT with transitional structure")
        if structure_alignment == "ALIGNED" and strong_confidence:
            return ("MIXED", f"signal=WAIT but confidence is {confidence_label}")
        return ("NEUTRAL", "signal=WAIT with no directional confirmation")

    if structure_alignment == "ALIGNED" and confidence_label in {"LOW", "MEDIUM"}:
        return (
            "MIXED",
            f"directional trend exists but confidence is only {confidence_label.lower()}",
        )

    if structure_alignment == "NEUTRAL":
        return ("NEUTRAL", "structure is neutral")

    if structure_alignment == "TRANSITION":
        return ("MIXED", "structure is transitional")

    if strong_confidence:
        return (
            "MIXED",
            f"confidence={confidence_label} but directional evidence is incomplete",
        )

    if warnings:
        return ("MIXED", "; ".join(warnings))

    if trend_score is None and confidence_value is None:
        return ("UNAVAILABLE", "no usable trend or confidence data")

    return ("NEUTRAL", "insufficient directional evidence")


def _combine_contexts(weekly_context: str, daily_context: str) -> str:
    if weekly_context == "OPPOSES" or daily_context == "OPPOSES":
        return "OPPOSES"
    if weekly_context == "SUPPORTS" and daily_context in {"SUPPORTS", "NEUTRAL", "UNAVAILABLE"}:
        return "SUPPORTS"
    if daily_context == "SUPPORTS" and weekly_context in {"SUPPORTS", "NEUTRAL", "UNAVAILABLE"}:
        return "SUPPORTS"
    if weekly_context == "MIXED" or daily_context == "MIXED":
        return "MIXED"
    if weekly_context == "NEUTRAL" and daily_context == "NEUTRAL":
        return "NEUTRAL"
    if weekly_context == "UNAVAILABLE" and daily_context == "UNAVAILABLE":
        return "UNAVAILABLE"
    if weekly_context == "SUPPORTS" or daily_context == "SUPPORTS":
        return "SUPPORTS"
    if weekly_context == "NEUTRAL" or daily_context == "NEUTRAL":
        return "NEUTRAL"
    return "UNAVAILABLE"


def _build_trigger_candidate(
    timeframe: str,
    tf_result: dict | None,
) -> dict | None:
    if not isinstance(tf_result, dict):
        return None

    quality = tf_result.get("quality")
    details = tf_result.get("decision_details")
    if not isinstance(quality, dict) or not isinstance(details, dict):
        return None

    for direction in _VALID_DIRECTIONS:
        dir_quality = quality.get(direction)
        if not isinstance(dir_quality, dict):
            continue

        signal = _safe_upper(dir_quality.get("signal"), default="WAIT")
        if signal not in _VALID_DIRECTIONS:
            continue

        if not bool(dir_quality.get("confirmed", False)):
            continue

        confidence_block = dir_quality.get("confidence")
        confidence_value = _safe_float(confidence_block.get("confidence") if isinstance(confidence_block, dict) else None)
        confidence_label = _safe_upper(confidence_block.get("label") if isinstance(confidence_block, dict) else None, default="LOW")
        if confidence_value is None or confidence_value < SIGNAL_THRESHOLD:
            continue

        detail = details.get(direction)
        detail = detail if isinstance(detail, dict) else {}
        blockers = _list_codes(detail.get("blockers"))
        if blockers:
            continue

        warnings = _list_strings(detail.get("warning_factors") or detail.get("warnings"))

        breakout_block = dir_quality.get("breakout_quality")
        volume_block = dir_quality.get("volume_quality")
        structure_block = dir_quality.get("structure_quality")

        return {
            "trigger_timeframe": timeframe,
            "direction": direction,
            "signal": signal,
            "decision": _safe_upper(detail.get("decision"), default="NONE"),
            "confidence": confidence_value,
            "confidence_label": confidence_label,
            "breakout_score": _safe_float(breakout_block.get("breakout_score") if isinstance(breakout_block, dict) else None),
            "volume_score": _safe_float(volume_block.get("volume_score") if isinstance(volume_block, dict) else None),
            "structure_score": _safe_float(structure_block.get("structure_score") if isinstance(structure_block, dict) else None),
            "hard_blockers": blockers,
            "warnings": warnings,
            "reason": _safe_str(detail.get("reason") or detail.get("decision_reason") or dir_quality.get("reason") or "Trigger candidate"),
        }

    return None


def build_shadow_context_trigger(symbol_result: dict) -> dict:
    """
    Build the experimental shadow object for one symbol.

    The result is read-only and does not alter production fields.
    """
    if not isinstance(symbol_result, dict):
        return {
            "candidate": False,
            "direction": "NONE",
            "trigger_timeframe": None,
            "trigger_signal": "NONE",
            "trigger_decision": "NONE",
            "trigger_confidence": None,
            "trigger_breakout_score": None,
            "trigger_volume_score": None,
            "trigger_structure_score": None,
            "weekly_context": "UNAVAILABLE",
            "daily_context": "UNAVAILABLE",
            "combined_context": "UNAVAILABLE",
            "context_supports": False,
            "context_opposes": False,
            "hard_blockers": [],
            "warnings": [],
            "reason": "symbol result is not available",
            "trigger_confluence": False,
            "trigger_conflict": False,
        }

    trigger_candidates: dict[str, dict] = {}
    for timeframe in _TRIGGER_TIMEFRAMES:
        tf_result = _get_tf_result(symbol_result, timeframe)
        candidate = _build_trigger_candidate(timeframe, tf_result)
        if candidate is not None:
            trigger_candidates[timeframe] = candidate

    if len(trigger_candidates) == 2:
        same_direction = trigger_candidates["4h"]["direction"] == trigger_candidates["1h"]["direction"]
        if not same_direction:
            return {
                "candidate": False,
                "direction": "NONE",
                "trigger_timeframe": None,
                "trigger_signal": "NONE",
                "trigger_decision": "NONE",
                "trigger_confidence": None,
                "trigger_breakout_score": None,
                "trigger_volume_score": None,
                "trigger_structure_score": None,
                "weekly_context": "UNAVAILABLE",
                "daily_context": "UNAVAILABLE",
                "combined_context": "UNAVAILABLE",
                "context_supports": False,
                "context_opposes": False,
                "hard_blockers": [],
                "warnings": [],
                "reason": (
                    "TRIGGER_CONFLICT: "
                    f"4h={trigger_candidates['4h']['direction']} vs 1h={trigger_candidates['1h']['direction']}"
                ),
                "trigger_confluence": False,
                "trigger_conflict": True,
            }

    trigger_confluence = len(trigger_candidates) == 2
    chosen = None
    if trigger_confluence:
        chosen = trigger_candidates["4h"]
    elif trigger_candidates:
        if "4h" in trigger_candidates:
            chosen = trigger_candidates["4h"]
        else:
            chosen = trigger_candidates["1h"]

    if chosen is None:
        return {
            "candidate": False,
            "direction": "NONE",
            "trigger_timeframe": None,
            "trigger_signal": "NONE",
            "trigger_decision": "NONE",
            "trigger_confidence": None,
            "trigger_breakout_score": None,
            "trigger_volume_score": None,
            "trigger_structure_score": None,
            "weekly_context": "UNAVAILABLE",
            "daily_context": "UNAVAILABLE",
            "combined_context": "UNAVAILABLE",
            "context_supports": False,
            "context_opposes": False,
            "hard_blockers": [],
            "warnings": [],
            "reason": "No 4h/1h trigger satisfied production signal, confirmation, confidence, and blocker checks",
            "trigger_confluence": False,
            "trigger_conflict": False,
        }

    direction = chosen["direction"]
    weekly_result = _get_tf_result(symbol_result, "1w")
    daily_result = _get_tf_result(symbol_result, "1d")

    weekly_block, weekly_detail = _extract_direction_block(weekly_result, direction)
    daily_block, daily_detail = _extract_direction_block(daily_result, direction)
    weekly_context, weekly_reason = _context_label_for_block(weekly_block, weekly_detail, direction)
    daily_context, daily_reason = _context_label_for_block(daily_block, daily_detail, direction)
    combined_context = _combine_contexts(weekly_context, daily_context)

    reason_parts = [
        f"trigger={chosen['trigger_timeframe']}:{direction}",
        f"confidence={chosen['confidence']:.1f} >= {SIGNAL_THRESHOLD:.1f}",
    ]
    if chosen["hard_blockers"]:
        reason_parts.append(f"blockers={','.join(chosen['hard_blockers'])}")
    else:
        reason_parts.append("no hard blockers")
    reason_parts.append(f"weekly={weekly_context} ({weekly_reason})")
    reason_parts.append(f"daily={daily_context} ({daily_reason})")
    reason_parts.append(f"combined_context={combined_context}")
    if trigger_confluence:
        reason_parts.append("confluence=4h+1h same direction")

    return {
        "candidate": True,
        "direction": direction,
        "trigger_timeframe": chosen["trigger_timeframe"],
        "trigger_signal": chosen["signal"],
        "trigger_decision": chosen["decision"],
        "trigger_confidence": chosen["confidence"],
        "trigger_breakout_score": chosen["breakout_score"],
        "trigger_volume_score": chosen["volume_score"],
        "trigger_structure_score": chosen["structure_score"],
        "weekly_context": weekly_context,
        "daily_context": daily_context,
        "combined_context": combined_context,
        "context_supports": combined_context == "SUPPORTS",
        "context_opposes": combined_context == "OPPOSES",
        "hard_blockers": chosen["hard_blockers"],
        "warnings": chosen["warnings"],
        "reason": "; ".join(reason_parts),
        "trigger_confluence": trigger_confluence,
        "trigger_conflict": False,
    }


def build_shadow_context_trigger_report(all_results: dict[str, dict]) -> dict:
    """
    Apply the shadow model to a full all_results snapshot.

    Returns the per-symbol shadow objects plus summary counts.
    """
    shadow_results: dict[str, dict] = {}
    for symbol in sorted((all_results or {}).keys()):
        shadow_results[symbol] = build_shadow_context_trigger(all_results.get(symbol, {}))

    total_symbols = len(shadow_results)
    candidate_count = sum(1 for item in shadow_results.values() if item.get("candidate"))
    confluence_count = sum(1 for item in shadow_results.values() if item.get("trigger_confluence"))
    conflict_count = sum(1 for item in shadow_results.values() if item.get("trigger_conflict"))
    trigger_tf_counts = Counter()
    combined_context_counts = Counter()
    weekly_context_counts = Counter()
    daily_context_counts = Counter()

    for item in shadow_results.values():
        if item.get("candidate"):
            tf = item.get("trigger_timeframe")
            if isinstance(tf, str):
                trigger_tf_counts[tf] += 1
            combined_context_counts[item.get("combined_context", "UNAVAILABLE")] += 1
            weekly_context_counts[item.get("weekly_context", "UNAVAILABLE")] += 1
            daily_context_counts[item.get("daily_context", "UNAVAILABLE")] += 1

    def _pct(count: int, total: int) -> float:
        return round((count / total) * 100.0, 2) if total else 0.0

    context_counts = {
        label: {"count": combined_context_counts.get(label, 0), "pct": _pct(combined_context_counts.get(label, 0), candidate_count)}
        for label in _VALID_CONTEXTS
    }
    weekly_counts = {
        label: {"count": weekly_context_counts.get(label, 0), "pct": _pct(weekly_context_counts.get(label, 0), candidate_count)}
        for label in _VALID_CONTEXTS
    }
    daily_counts = {
        label: {"count": daily_context_counts.get(label, 0), "pct": _pct(daily_context_counts.get(label, 0), candidate_count)}
        for label in _VALID_CONTEXTS
    }
    trigger_timeframe_counts = {
        timeframe: {"count": trigger_tf_counts.get(timeframe, 0), "pct": _pct(trigger_tf_counts.get(timeframe, 0), candidate_count)}
        for timeframe in _TRIGGER_TIMEFRAMES
    }

    examples: list[dict] = []
    for symbol in sorted(shadow_results.keys()):
        item = shadow_results[symbol]
        if item.get("candidate") or item.get("trigger_conflict"):
            examples.append(
                {
                    "symbol": symbol,
                    "candidate": item.get("candidate", False),
                    "direction": item.get("direction", "NONE"),
                    "trigger_timeframe": item.get("trigger_timeframe"),
                    "combined_context": item.get("combined_context"),
                    "reason": item.get("reason"),
                }
            )
        if len(examples) >= 10:
            break

    return {
        "summary": {
            "symbols_total": total_symbols,
            "trigger_candidates": {"count": candidate_count, "pct": _pct(candidate_count, total_symbols)},
            "aligned_trigger_confluence": {"count": confluence_count, "pct": _pct(confluence_count, total_symbols)},
            "trigger_conflicts": {"count": conflict_count, "pct": _pct(conflict_count, total_symbols)},
        },
        "context_counts": context_counts,
        "weekly_context_counts": weekly_counts,
        "daily_context_counts": daily_counts,
        "trigger_timeframe_counts": trigger_timeframe_counts,
        "candidate_examples": examples,
        "shadow_results": shadow_results,
    }
