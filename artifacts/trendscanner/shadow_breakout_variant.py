"""
shadow_breakout_variant.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Isolated experimental shadow model for breakout confirmation.

This module is read-only with respect to pipeline state:
- does not modify production logic
- does not modify UI
- does not write to database

It evaluates one alternative confirmation rule side-by-side with the
production confirmation rule using already-computed breakout fields.
"""

from __future__ import annotations

import math
from typing import Any

_VALID_DIRECTIONS = ("LONG", "SHORT")

# Production confirmation rule (reference-only, unchanged).
PRODUCTION_BREAKOUT_SCORE_THRESHOLD = 50.0

# Experimental shadow confirmation rule thresholds.
SHADOW_BREAKOUT_SCORE_THRESHOLD = 70.0
SHADOW_DISTANCE_SCORE_THRESHOLD = 20.0
SHADOW_BODY_SCORE_THRESHOLD = 12.0


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _extract_breakout_fields(tf_result: dict, direction: str) -> dict[str, Any] | None:
    quality = tf_result.get("quality") if isinstance(tf_result, dict) else None
    if not isinstance(quality, dict):
        return None

    direction_quality = quality.get(direction)
    if not isinstance(direction_quality, dict):
        return None

    breakout_quality = direction_quality.get("breakout_quality")
    if not isinstance(breakout_quality, dict):
        return None

    components = breakout_quality.get("components")
    if not isinstance(components, dict):
        components = {}

    cross_component = _safe_float(components.get("cross"))
    crossed = bool(cross_component is not None and cross_component > 0.0)

    breakout_score = _safe_float(breakout_quality.get("breakout_score"))
    distance_score = _safe_float(components.get("close_distance"))
    body_score = _safe_float(components.get("candle_body"))

    production_payload_confirmed = breakout_quality.get("confirmed")
    if production_payload_confirmed is not None:
        production_payload_confirmed = bool(production_payload_confirmed)

    return {
        "crossed": crossed,
        "breakout_score": breakout_score,
        "distance_score": distance_score,
        "body_score": body_score,
        "production_payload_confirmed": production_payload_confirmed,
    }


def evaluate_confirmation_variant(
    crossed: bool,
    breakout_score: float | None,
    distance_score: float | None,
    body_score: float | None,
) -> dict[str, bool]:
    """
    Evaluate production and shadow confirmation side by side.

    Production:
        confirmed = crossed AND breakout_score >= 50

    Shadow variant:
        confirmed_shadow =
            crossed
            OR (
                breakout_score >= 70
                AND distance_score >= 20
                AND body_score >= 12
            )
    """
    production_confirmed = bool(
        crossed
        and breakout_score is not None
        and breakout_score >= PRODUCTION_BREAKOUT_SCORE_THRESHOLD
    )

    shadow_confirmed = bool(
        crossed
        or (
            breakout_score is not None
            and breakout_score >= SHADOW_BREAKOUT_SCORE_THRESHOLD
            and distance_score is not None
            and distance_score >= SHADOW_DISTANCE_SCORE_THRESHOLD
            and body_score is not None
            and body_score >= SHADOW_BODY_SCORE_THRESHOLD
        )
    )

    return {
        "production_confirmed": production_confirmed,
        "shadow_confirmed": shadow_confirmed,
    }


def build_shadow_breakout_report(all_results: dict[str, dict]) -> dict[str, Any]:
    """
    Build a compact shadow report from existing multi-timeframe results.

    Input: all_results from scan flow (symbol -> timeframe -> analysis dict).
    Output: side-by-side decisions + aggregate stats.
    """
    side_by_side: list[dict[str, Any]] = []
    added_candidates = 0
    removed_candidates = 0
    affected_symbols: set[str] = set()
    production_confirmed = 0
    shadow_confirmed = 0

    for symbol, symbol_results in (all_results or {}).items():
        if not isinstance(symbol_results, dict):
            continue

        for timeframe, tf_result in symbol_results.items():
            if timeframe == "FINAL" or not isinstance(tf_result, dict):
                continue

            for direction in _VALID_DIRECTIONS:
                fields = _extract_breakout_fields(tf_result, direction)
                if fields is None:
                    continue

                decision_pair = evaluate_confirmation_variant(
                    crossed=fields["crossed"],
                    breakout_score=fields["breakout_score"],
                    distance_score=fields["distance_score"],
                    body_score=fields["body_score"],
                )

                prod = bool(decision_pair["production_confirmed"])
                shadow = bool(decision_pair["shadow_confirmed"])

                if prod:
                    production_confirmed += 1
                if shadow:
                    shadow_confirmed += 1

                delta = "unchanged"
                if shadow and not prod:
                    delta = "added"
                    added_candidates += 1
                    affected_symbols.add(symbol)
                elif prod and not shadow:
                    delta = "removed"
                    removed_candidates += 1
                    affected_symbols.add(symbol)

                side_by_side.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "direction": direction,
                        "crossed": fields["crossed"],
                        "breakout_score": fields["breakout_score"],
                        "distance_score": fields["distance_score"],
                        "body_score": fields["body_score"],
                        "production_confirmed": prod,
                        "shadow_confirmed": shadow,
                        "delta": delta,
                    }
                )

    side_by_side.sort(
        key=lambda row: (
            str(row["symbol"]),
            str(row["timeframe"]),
            str(row["direction"]),
        )
    )

    return {
        "production_confirmed": production_confirmed,
        "shadow_confirmed": shadow_confirmed,
        "added_candidates": added_candidates,
        "removed_candidates": removed_candidates,
        "symbols_affected": sorted(affected_symbols),
        "total_rows_evaluated": len(side_by_side),
        "side_by_side": side_by_side,
    }
