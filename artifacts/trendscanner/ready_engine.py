"""
ready_engine.py
~~~~~~~~~~~~~~~

Read-only READY candidate selector.

The module reads already-computed timeframe results and does not:
- recalculate indicators;
- change TAKE / WATCH / SKIP;
- mutate all_results;
- write to SQLite;
- place trades.
"""

from __future__ import annotations

import math
from typing import Any


READY_TIMEFRAMES = ("4h", "1h")
VALID_DIRECTIONS = ("LONG", "SHORT")

MIN_READY_CONFIDENCE = 60.0
MIN_READY_DECISION_SCORE = 40.0
MIN_READY_TREND_SCORE = 50.0
MIN_READY_VOLUME_SCORE = 40.0


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _blocker_codes(blockers: Any) -> list[str]:
    if not isinstance(blockers, list):
        return []

    codes: list[str] = []
    for blocker in blockers:
        if isinstance(blocker, dict):
            code = str(blocker.get("code", "")).strip()
            if code:
                codes.append(code)
        elif blocker:
            codes.append(str(blocker).strip())

    return codes


def evaluate_ready_candidate(
    symbol: str,
    timeframe: str,
    direction: str,
    timeframe_result: dict,
) -> dict:
    """
    Evaluate one already-computed symbol/timeframe/direction result.

    Returns a serializable diagnostic dictionary with ready=True/False.
    """
    direction = str(direction).upper().strip()
    timeframe = str(timeframe).strip()

    reasons: list[str] = []
    warnings: list[str] = []

    if direction not in VALID_DIRECTIONS:
        return {
            "ready": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "reasons": ["INVALID_DIRECTION"],
            "warnings": [],
        }

    if timeframe not in READY_TIMEFRAMES:
        return {
            "ready": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "reasons": ["UNSUPPORTED_READY_TIMEFRAME"],
            "warnings": [],
        }

    if not isinstance(timeframe_result, dict):
        return {
            "ready": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "reasons": ["INVALID_TIMEFRAME_RESULT"],
            "warnings": [],
        }

    quality_root = timeframe_result.get("quality", {})
    decision_root = timeframe_result.get("decision_details", {})

    quality = (
        quality_root.get(direction, {})
        if isinstance(quality_root, dict)
        else {}
    )
    decision = (
        decision_root.get(direction, {})
        if isinstance(decision_root, dict)
        else {}
    )

    if not isinstance(quality, dict):
        quality = {}
    if not isinstance(decision, dict):
        decision = {}

    breakout = quality.get("breakout_quality", {})
    trend = quality.get("trend_quality", {})
    volume = quality.get("volume_quality", {})

    if not isinstance(breakout, dict):
        breakout = {}
    if not isinstance(trend, dict):
        trend = {}
    if not isinstance(volume, dict):
        volume = {}

    component_scores = decision.get("component_scores", {})
    market_context = decision.get("market_context", {})

    if not isinstance(component_scores, dict):
        component_scores = {}
    if not isinstance(market_context, dict):
        market_context = {}

    confirmed = bool(
        decision.get("breakout_confirmed", False)
        or breakout.get("confirmed", False)
        or quality.get("confirmed", False)
    )

    signal = str(
        decision.get("signal")
        or quality.get("signal")
        or "WAIT"
    ).upper().strip()

    confidence = _safe_float(
        decision.get("confidence")
        if decision.get("confidence") is not None
        else (quality.get("confidence") or {}).get("confidence")
        if isinstance(quality.get("confidence"), dict)
        else None
    )

    decision_score = _safe_float(decision.get("decision_score"))

    trend_score = _safe_float(
        component_scores.get("trend_quality")
        if component_scores.get("trend_quality") is not None
        else trend.get("trend_quality_score")
    )

    volume_score = _safe_float(
        component_scores.get("volume_quality")
        if component_scores.get("volume_quality") is not None
        else volume.get("volume_score")
    )

    breakout_score = _safe_float(
        component_scores.get("breakout_quality")
        if component_scores.get("breakout_quality") is not None
        else breakout.get("breakout_score")
    )

    structure_score = _safe_float(
        component_scores.get("structure_quality")
    )

    alignment = str(
        market_context.get("alignment", "UNKNOWN")
    ).upper().strip()

    blockers = _blocker_codes(decision.get("blockers"))

    if not confirmed:
        reasons.append("BREAKOUT_NOT_CONFIRMED")

    if blockers:
        reasons.append("HARD_BLOCKERS_PRESENT")

    if signal != direction:
        reasons.append("SIGNAL_DIRECTION_MISMATCH")

    if confidence is None or confidence < MIN_READY_CONFIDENCE:
        reasons.append("CONFIDENCE_BELOW_READY")

    if decision_score is None or decision_score < MIN_READY_DECISION_SCORE:
        reasons.append("DECISION_SCORE_BELOW_READY")

    if trend_score is None or trend_score < MIN_READY_TREND_SCORE:
        reasons.append("TREND_QUALITY_BELOW_READY")

    if volume_score is None or volume_score < MIN_READY_VOLUME_SCORE:
        reasons.append("VOLUME_BELOW_READY")

    # OPPOSED is not a hard rejection for READY.
    # It remains a warning and must later be resolved by Entry Engine.
    if alignment == "OPPOSED":
        warnings.append("STRUCTURE_OPPOSED")
    elif alignment == "TRANSITION":
        warnings.append("STRUCTURE_TRANSITION")

    ready = not reasons

    return {
        "ready": ready,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "confidence": confidence,
        "decision_score": decision_score,
        "breakout_score": breakout_score,
        "trend_score": trend_score,
        "volume_score": volume_score,
        "structure_score": structure_score,
        "alignment": alignment,
        "production_decision": str(
            decision.get("decision", "SKIP")
        ).upper().strip(),
        "production_signal": signal,
        "blockers": blockers,
        "reasons": reasons,
        "warnings": warnings,
    }


def build_ready_candidates(
    all_results: dict,
    *,
    timeframes: tuple[str, ...] = READY_TIMEFRAMES,
) -> list[dict]:
    """
    Build a ranked, deterministic READY list from all_results.

    Sorting:
    1. decision_score descending;
    2. confidence descending;
    3. 4h before 1h;
    4. symbol and direction for determinism.
    """
    candidates: list[dict] = []

    if not isinstance(all_results, dict):
        return candidates

    for symbol, symbol_result in all_results.items():
        if not isinstance(symbol_result, dict):
            continue

        for timeframe in timeframes:
            timeframe_result = symbol_result.get(timeframe, {})
            if not isinstance(timeframe_result, dict):
                continue

            for direction in VALID_DIRECTIONS:
                candidate = evaluate_ready_candidate(
                    symbol,
                    timeframe,
                    direction,
                    timeframe_result,
                )
                if candidate["ready"]:
                    candidates.append(candidate)

    timeframe_rank = {"4h": 0, "1h": 1}

    candidates.sort(
        key=lambda item: (
            -float(item.get("decision_score") or 0.0),
            -float(item.get("confidence") or 0.0),
            timeframe_rank.get(item.get("timeframe"), 99),
            str(item.get("symbol", "")),
            str(item.get("direction", "")),
        )
    )

    return candidates


def build_ready_report(all_results: dict) -> dict:
    candidates = build_ready_candidates(all_results)

    return {
        "ready_count": len(candidates),
        "candidates": candidates,
        "by_timeframe": {
            timeframe: sum(
                1 for candidate in candidates
                if candidate["timeframe"] == timeframe
            )
            for timeframe in READY_TIMEFRAMES
        },
        "by_direction": {
            direction: sum(
                1 for candidate in candidates
                if candidate["direction"] == direction
            )
            for direction in VALID_DIRECTIONS
        },
    }
