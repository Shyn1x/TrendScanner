from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from explain_engine import build_timeframe_explanation
from strategy_analytics import build_analysis_record


TIMEFRAMES = ("1M", "1w", "1d", "4h", "1h")
DIRECTIONS = ("LONG", "SHORT")


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _first_blocker_code(direction_decision: Mapping[str, Any]) -> str | None:
    blockers = direction_decision.get("blockers")

    if not isinstance(blockers, list) or not blockers:
        return None

    first = blockers[0]

    if isinstance(first, dict):
        code = first.get("code")
        if code is not None:
            return str(code)

        message = first.get("message")
        if message is not None:
            return str(message)

    if first is not None:
        return str(first)

    return None


def _score_from_quality(
    direction_quality: Mapping[str, Any],
    component_name: str,
) -> Any:
    component = direction_quality.get(component_name)

    if not isinstance(component, dict):
        return None

    return component.get("score")


def _confidence_from_quality(
    direction_quality: Mapping[str, Any],
) -> Any:
    confidence = direction_quality.get("confidence")

    if isinstance(confidence, dict):
        return confidence.get("confidence")

    return confidence


def _entry_price_from_quality(
    direction_quality: Mapping[str, Any],
) -> Any:
    breakout_quality = direction_quality.get("breakout_quality")

    if not isinstance(breakout_quality, dict):
        return None

    return breakout_quality.get("signal_close")


def _breakout_components_from_quality(
    direction_quality: Mapping[str, Any],
) -> dict[str, Any]:
    breakout_quality = direction_quality.get("breakout_quality")
    if not isinstance(breakout_quality, dict):
        return {
            "cross_score": None,
            "distance_score": None,
            "body_score": None,
            "wick_score": None,
        }

    components = breakout_quality.get("components")
    if not isinstance(components, dict):
        return {
            "cross_score": None,
            "distance_score": None,
            "body_score": None,
            "wick_score": None,
        }

    return {
        "cross_score": components.get("cross"),
        "distance_score": components.get("close_distance"),
        "body_score": components.get("candle_body"),
        "wick_score": components.get("rejection_wick"),
    }


def _normalize_timestamp_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _trigger_candle_ts_from_result(
    timeframe_result: Mapping[str, Any],
    direction_quality: Mapping[str, Any],
    direction_decision: Mapping[str, Any],
) -> str | None:
    breakout_quality = direction_quality.get("breakout_quality")
    if not isinstance(breakout_quality, dict):
        breakout_quality = {}

    candidates = (
        direction_decision.get("trigger_candle_ts"),
        direction_decision.get("signal_timestamp"),
        direction_quality.get("trigger_candle_ts"),
        direction_quality.get("signal_timestamp"),
        breakout_quality.get("trigger_candle_ts"),
        breakout_quality.get("signal_timestamp"),
        breakout_quality.get("signal_time"),
        breakout_quality.get("candle_timestamp"),
        breakout_quality.get("timestamp"),
        timeframe_result.get("trigger_candle_ts"),
        timeframe_result.get("signal_timestamp"),
    )

    for candidate in candidates:
        normalized = _normalize_timestamp_value(candidate)
        if normalized is not None:
            return normalized

    return None


def build_strategy_rows(
    all_results: Mapping[str, Any],
    *,
    scan_id: str,
    pipeline_version: str | None = None,
    scanner_version: str | None = None,
    decision_version: str | None = None,
    timestamp_utc: str | None = None,
) -> list[dict[str, Any]]:
    """
    Convert completed multi-timeframe scan results into SQLite-ready rows.

    Produces one row for LONG and one row for SHORT for each available
    symbol/timeframe result containing directional quality and decision data.

    This function:
    - does not mutate all_results;
    - does not access the network;
    - does not write to SQLite;
    - does not depend on Streamlit.
    """
    if not isinstance(all_results, Mapping):
        return []

    source_copy = deepcopy(dict(all_results))
    rows: list[dict[str, Any]] = []

    for symbol, symbol_result_raw in source_copy.items():
        symbol_result = _as_dict(symbol_result_raw)

        if not symbol_result or "_error" in symbol_result:
            continue

        for timeframe in TIMEFRAMES:
            timeframe_result = _as_dict(symbol_result.get(timeframe))

            if not timeframe_result:
                continue

            quality = _as_dict(timeframe_result.get("quality"))
            decision_details = _as_dict(
                timeframe_result.get("decision_details")
            )

            for direction in DIRECTIONS:
                direction_quality = _as_dict(quality.get(direction))
                direction_decision = _as_dict(
                    decision_details.get(direction)
                )

                # At least one real directional structure must exist.
                if not direction_quality and not direction_decision:
                    continue

                try:
                    explanation = build_timeframe_explanation(
                        timeframe_result,
                        direction,
                    )
                except Exception:
                    explanation = {}

                scores = _as_dict(explanation.get("scores"))

                breakout_score = scores.get("breakout")
                if breakout_score is None:
                    breakout_score = _score_from_quality(
                        direction_quality,
                        "breakout_quality",
                    )

                trend_quality_score = scores.get("trend_quality")
                if trend_quality_score is None:
                    trend_quality_score = _score_from_quality(
                        direction_quality,
                        "trend_quality",
                    )

                volume_score = scores.get("volume")
                if volume_score is None:
                    volume_score = _score_from_quality(
                        direction_quality,
                        "volume_quality",
                    )

                structure_score = scores.get("structure")
                if structure_score is None:
                    structure_score = _score_from_quality(
                        direction_quality,
                        "structure_quality",
                    )

                confidence = direction_decision.get("confidence")
                if confidence is None:
                    confidence = _confidence_from_quality(
                        direction_quality
                    )

                breakout_confirmed = direction_decision.get(
                    "breakout_confirmed"
                )
                if breakout_confirmed is None:
                    breakout_quality = _as_dict(
                        direction_quality.get("breakout_quality")
                    )
                    breakout_confirmed = breakout_quality.get(
                        "confirmed"
                    )

                structure_quality = _as_dict(
                    direction_quality.get("structure_quality")
                )
                structure_alignment = (
                    direction_decision.get("structure_alignment")
                    or direction_decision.get("sq_alignment")
                    or structure_quality.get("alignment")
                )

                primary_blocker = _first_blocker_code(
                    direction_decision
                )
                if primary_blocker is None:
                    blocker_text = explanation.get("primary_blocker")
                    if blocker_text is not None:
                        blocker_text = str(blocker_text).strip()
                        primary_blocker = blocker_text or None

                decision = direction_decision.get("decision")
                decision_score = direction_decision.get(
                    "decision_score"
                )

                pipeline_stage = explanation.get("pipeline_stage")

                entry_price = _entry_price_from_quality(
                    direction_quality
                )

                breakout_components = _breakout_components_from_quality(
                    direction_quality
                )

                trigger_candle_ts = _trigger_candle_ts_from_result(
                    timeframe_result,
                    direction_quality,
                    direction_decision,
                )

                row = build_analysis_record(
                    scan_id=scan_id,
                    timestamp_utc=timestamp_utc,
                    symbol=str(symbol),
                    timeframe=timeframe,
                    direction=direction,
                    decision=decision,
                    decision_score=decision_score,
                    confidence=confidence,
                    breakout_score=breakout_score,
                    trend_quality_score=trend_quality_score,
                    volume_score=volume_score,
                    structure_score=structure_score,
                    breakout_confirmed=breakout_confirmed,
                    structure_alignment=structure_alignment,
                    primary_blocker=primary_blocker,
                    pipeline_stage=pipeline_stage,
                    entry_price=entry_price,
                    trigger_candle_ts=trigger_candle_ts,
                    cross_score=breakout_components["cross_score"],
                    distance_score=breakout_components["distance_score"],
                    body_score=breakout_components["body_score"],
                    wick_score=breakout_components["wick_score"],
                    scanner_version=scanner_version,
                    pipeline_version=pipeline_version,
                    decision_version=decision_version,
                )

                rows.append(row)

    return rows
