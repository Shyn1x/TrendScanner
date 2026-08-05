"""
ready_take_replay.py
~~~~~~~~~~~~~~~~~~~~
Historical validation for READY -> TAKE conversion.

Diagnostic-only module:
- does not change production thresholds;
- does not mutate input replay data;
- reuses ready_engine.evaluate_ready_candidate directly.
"""

from __future__ import annotations

import math
from typing import Any

from ready_engine import evaluate_ready_candidate


_VALID_DIRECTIONS = ("LONG", "SHORT")
_STATUS_ENTRY = "ENTRY_CONFIRMED"
_STATUS_CANCEL = "CANCELLED"
_STATUS_EXPIRED = "EXPIRED"
_BREAKOUT_LOSS_CANCEL = "cancel"
_BREAKOUT_LOSS_OBSERVE = "observe"


class _State:
    def __init__(self) -> None:
        self.active_event: dict | None = None
        self.needs_false_gap: bool = False
        self.prev_ready: bool = False
        self.last_replay_index: int = -1
        self.last_timestamp: int | None = None


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _event_sort_key(event: dict) -> tuple:
    return (
        _safe_str(event.get("symbol")),
        _safe_str(event.get("timeframe")),
        _safe_str(event.get("direction")),
        _safe_int(event.get("ready_replay_index"), 10**9),
        _safe_int(event.get("resolved_replay_index"), 10**9),
        _safe_int(event.get("ready_timestamp"), 10**18),
    )


def _direction_take(entry: dict, direction: str) -> bool:
    decision = _safe_str(entry.get("decision", "")).upper()
    decision_direction = _safe_str(entry.get("decision_direction", "")).upper()
    if decision == "TAKE" and decision_direction == direction:
        return True

    compact_dir = entry.get(direction.lower(), {})
    if isinstance(compact_dir, dict):
        return _safe_str(compact_dir.get("decision", "")).upper() == "TAKE"

    return False


def _opposite(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


def _normalize_breakout_loss_policy(value: Any) -> str:
    policy = _safe_str(value, _BREAKOUT_LOSS_CANCEL).lower()
    if policy in (_BREAKOUT_LOSS_CANCEL, _BREAKOUT_LOSS_OBSERVE):
        return policy
    return _BREAKOUT_LOSS_CANCEL


def _resolution_reason_cancel(
    entry: dict,
    direction: str,
    ready_eval: dict,
    breakout_loss_policy: str,
) -> str | None:
    opposite = _opposite(direction)

    if _direction_take(entry, opposite):
        return "OPPOSITE_TAKE"

    reasons = ready_eval.get("reasons", [])
    if (
        breakout_loss_policy == _BREAKOUT_LOSS_CANCEL
        and isinstance(reasons, list)
        and "BREAKOUT_NOT_CONFIRMED" in reasons
    ):
        return "BREAKOUT_NOT_CONFIRMED"

    signal = _safe_str(entry.get("signal", "WAIT")).upper()
    if signal == opposite:
        return "OPPOSITE_SIGNAL"

    return None


def _finalize_event(
    event: dict,
    *,
    status: str,
    resolved_replay_index: int,
    resolved_timestamp: int | None,
    resolution_reason: str,
) -> dict:
    ready_index = _safe_int(event.get("ready_replay_index"), resolved_replay_index)
    wait_steps = max(0, resolved_replay_index - ready_index)

    finalized = dict(event)
    finalized["status"] = status
    finalized["resolved_replay_index"] = resolved_replay_index
    finalized["resolved_timestamp"] = resolved_timestamp
    finalized["wait_steps"] = wait_steps
    finalized["resolution_reason"] = resolution_reason
    return finalized


def _empty_bucket() -> dict:
    return {
        "ready_events": 0,
        "entry_confirmed": 0,
        "cancelled": 0,
        "expired": 0,
        "conversion_rate": 0.0,
        "average_wait_steps": None,
    }


def _fill_bucket_stats(bucket: dict, wait_values: list[int]) -> None:
    ready_events = bucket["ready_events"]
    entry_confirmed = bucket["entry_confirmed"]
    bucket["conversion_rate"] = (entry_confirmed / ready_events) if ready_events else 0.0
    if wait_values:
        bucket["average_wait_steps"] = sum(wait_values) / len(wait_values)
    else:
        bucket["average_wait_steps"] = None


def _build_summary(events: list[dict]) -> dict:
    summary = {
        "ready_events": len(events),
        "entry_confirmed": 0,
        "cancelled": 0,
        "expired": 0,
        "conversion_rate": 0.0,
        "average_wait_steps": None,
        "by_direction": {},
        "by_timeframe": {},
    }

    waits_all: list[int] = []
    direction_waits: dict[str, list[int]] = {}
    timeframe_waits: dict[str, list[int]] = {}

    for event in events:
        direction = _safe_str(event.get("direction", "UNKNOWN")).upper()
        timeframe = _safe_str(event.get("timeframe", "UNKNOWN"))
        status = _safe_str(event.get("status", ""))
        wait_steps = _safe_int(event.get("wait_steps"), 0)

        if status == _STATUS_ENTRY:
            summary["entry_confirmed"] += 1
        elif status == _STATUS_CANCEL:
            summary["cancelled"] += 1
        elif status == _STATUS_EXPIRED:
            summary["expired"] += 1

        waits_all.append(wait_steps)

        if direction not in summary["by_direction"]:
            summary["by_direction"][direction] = _empty_bucket()
            direction_waits[direction] = []
        if timeframe not in summary["by_timeframe"]:
            summary["by_timeframe"][timeframe] = _empty_bucket()
            timeframe_waits[timeframe] = []

        summary["by_direction"][direction]["ready_events"] += 1
        summary["by_timeframe"][timeframe]["ready_events"] += 1

        if status == _STATUS_ENTRY:
            summary["by_direction"][direction]["entry_confirmed"] += 1
            summary["by_timeframe"][timeframe]["entry_confirmed"] += 1
        elif status == _STATUS_CANCEL:
            summary["by_direction"][direction]["cancelled"] += 1
            summary["by_timeframe"][timeframe]["cancelled"] += 1
        elif status == _STATUS_EXPIRED:
            summary["by_direction"][direction]["expired"] += 1
            summary["by_timeframe"][timeframe]["expired"] += 1

        direction_waits[direction].append(wait_steps)
        timeframe_waits[timeframe].append(wait_steps)

    summary["conversion_rate"] = (
        summary["entry_confirmed"] / summary["ready_events"]
        if summary["ready_events"]
        else 0.0
    )
    summary["average_wait_steps"] = (sum(waits_all) / len(waits_all)) if waits_all else None

    for direction in sorted(summary["by_direction"].keys()):
        bucket = summary["by_direction"][direction]
        _fill_bucket_stats(bucket, direction_waits.get(direction, []))
    summary["by_direction"] = {
        direction: summary["by_direction"][direction]
        for direction in sorted(summary["by_direction"].keys())
    }

    for timeframe in sorted(summary["by_timeframe"].keys()):
        bucket = summary["by_timeframe"][timeframe]
        _fill_bucket_stats(bucket, timeframe_waits.get(timeframe, []))
    summary["by_timeframe"] = {
        timeframe: summary["by_timeframe"][timeframe]
        for timeframe in sorted(summary["by_timeframe"].keys())
    }

    return summary


def validate_ready_to_take(
    replay_results: list,
    max_wait_steps: int = 3,
    breakout_loss_policy: str = _BREAKOUT_LOSS_CANCEL,
) -> dict:
    """
    Validate READY -> TAKE conversion on historical replay snapshots.

    READY is evaluated strictly with ready_engine.evaluate_ready_candidate.
    """
    if not isinstance(replay_results, list) or not replay_results:
        return {
            "events": [],
            "summary": _build_summary([]),
        }

    max_wait = max(1, _safe_int(max_wait_steps, 3))
    breakout_policy = _normalize_breakout_loss_policy(breakout_loss_policy)

    states: dict[tuple[str, str, str], _State] = {}
    events: list[dict] = []

    for ordinal, raw_entry in enumerate(replay_results):
        if not isinstance(raw_entry, dict):
            continue

        symbol = _safe_str(raw_entry.get("symbol", ""))
        timeframe = _safe_str(raw_entry.get("timeframe", ""))
        replay_index = _safe_int(raw_entry.get("replay_index"), ordinal)
        timestamp_raw = raw_entry.get("signal_timestamp")
        timestamp = _safe_int(timestamp_raw, 0) if timestamp_raw is not None else None

        timeframe_result = raw_entry.get("timeframe_result", {})
        if not isinstance(timeframe_result, dict):
            timeframe_result = {}

        for direction in _VALID_DIRECTIONS:
            key = (symbol, timeframe, direction)
            state = states.get(key)
            if state is None:
                state = _State()
                states[key] = state

            state.last_replay_index = replay_index
            state.last_timestamp = timestamp

            ready_eval = evaluate_ready_candidate(
                symbol,
                timeframe,
                direction,
                timeframe_result,
            )
            ready = bool(ready_eval.get("ready", False))

            resolved_this_step = False
            if state.active_event is not None:
                ready_start_index = _safe_int(state.active_event.get("ready_replay_index"), replay_index)
                steps_since_ready = replay_index - ready_start_index

                if steps_since_ready >= 1:
                    if _direction_take(raw_entry, direction):
                        events.append(
                            _finalize_event(
                                state.active_event,
                                status=_STATUS_ENTRY,
                                resolved_replay_index=replay_index,
                                resolved_timestamp=timestamp,
                                resolution_reason="SAME_DIRECTION_TAKE",
                            )
                        )
                        state.active_event = None
                        state.needs_false_gap = True
                        resolved_this_step = True
                    else:
                        cancel_reason = _resolution_reason_cancel(
                            raw_entry,
                            direction,
                            ready_eval,
                            breakout_policy,
                        )
                        if cancel_reason is not None:
                            events.append(
                                _finalize_event(
                                    state.active_event,
                                    status=_STATUS_CANCEL,
                                    resolved_replay_index=replay_index,
                                    resolved_timestamp=timestamp,
                                    resolution_reason=cancel_reason,
                                )
                            )
                            state.active_event = None
                            state.needs_false_gap = True
                            resolved_this_step = True
                        elif steps_since_ready >= max_wait:
                            events.append(
                                _finalize_event(
                                    state.active_event,
                                    status=_STATUS_EXPIRED,
                                    resolved_replay_index=replay_index,
                                    resolved_timestamp=timestamp,
                                    resolution_reason="MAX_WAIT_EXCEEDED",
                                )
                            )
                            state.active_event = None
                            state.needs_false_gap = True
                            resolved_this_step = True

            if state.active_event is None:
                if not ready:
                    state.needs_false_gap = False

                if (not resolved_this_step) and ready and (not state.prev_ready) and (not state.needs_false_gap):
                    state.active_event = {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "direction": direction,
                        "ready_replay_index": replay_index,
                        "ready_timestamp": timestamp,
                        "ready_confidence": _safe_float(ready_eval.get("confidence")),
                        "ready_decision_score": _safe_float(ready_eval.get("decision_score")),
                        "ready_warnings": list(ready_eval.get("warnings", []))
                        if isinstance(ready_eval.get("warnings"), list)
                        else [],
                    }

            state.prev_ready = ready

    for state in states.values():
        if state.active_event is None:
            continue

        resolved_index = state.last_replay_index
        resolved_timestamp = state.last_timestamp
        events.append(
            _finalize_event(
                state.active_event,
                status=_STATUS_EXPIRED,
                resolved_replay_index=resolved_index,
                resolved_timestamp=resolved_timestamp,
                resolution_reason="REPLAY_ENDED",
            )
        )

    events.sort(key=_event_sort_key)

    return {
        "events": events,
        "summary": _build_summary(events),
    }
