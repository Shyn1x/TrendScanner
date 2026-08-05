from __future__ import annotations

from copy import deepcopy

from ready_take_replay import validate_ready_to_take


def _dir_quality(
    direction: str,
    *,
    confirmed: bool = True,
    signal: str | None = None,
    confidence: float = 70.0,
    trend: float = 60.0,
    volume: float = 60.0,
    breakout: float = 70.0,
) -> dict:
    signal_value = signal or direction
    return {
        "confirmed": confirmed,
        "signal": signal_value,
        "confidence": {"confidence": confidence},
        "trend_quality": {"trend_quality_score": trend},
        "volume_quality": {"volume_score": volume},
        "breakout_quality": {
            "confirmed": confirmed,
            "breakout_score": breakout,
        },
    }


def _dir_decision(
    direction: str,
    *,
    decision: str = "SKIP",
    decision_score: float = 50.0,
    confirmed: bool = True,
    signal: str | None = None,
    confidence: float = 70.0,
    trend: float = 60.0,
    volume: float = 60.0,
    breakout: float = 70.0,
    structure: float = 40.0,
    blockers: list | None = None,
    alignment: str = "ALIGNED",
) -> dict:
    signal_value = signal or direction
    return {
        "decision": decision,
        "decision_score": decision_score,
        "breakout_confirmed": confirmed,
        "blockers": blockers or [],
        "component_scores": {
            "trend_quality": trend,
            "volume_quality": volume,
            "breakout_quality": breakout,
            "structure_quality": structure,
        },
        "market_context": {
            "alignment": alignment,
        },
        "signal": signal_value,
        "confidence": confidence,
    }


def _tf_result(
    *,
    long_ready: bool = True,
    short_ready: bool = False,
    long_signal: str = "LONG",
    short_signal: str = "SHORT",
) -> dict:
    long_confirmed = long_ready
    short_confirmed = short_ready

    long_conf = 70.0 if long_ready else 50.0
    short_conf = 70.0 if short_ready else 50.0

    return {
        "quality": {
            "LONG": _dir_quality(
                "LONG",
                confirmed=long_confirmed,
                signal=long_signal,
                confidence=long_conf,
            ),
            "SHORT": _dir_quality(
                "SHORT",
                confirmed=short_confirmed,
                signal=short_signal,
                confidence=short_conf,
            ),
        },
        "decision_details": {
            "LONG": _dir_decision(
                "LONG",
                decision="SKIP",
                confirmed=long_confirmed,
                signal=long_signal,
                confidence=long_conf,
                decision_score=50.0 if long_ready else 30.0,
            ),
            "SHORT": _dir_decision(
                "SHORT",
                decision="SKIP",
                confirmed=short_confirmed,
                signal=short_signal,
                confidence=short_conf,
                decision_score=50.0 if short_ready else 30.0,
            ),
        },
    }


def _entry(
    replay_index: int,
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    signal: str = "WAIT",
    decision: str = "SKIP",
    decision_direction: str = "NONE",
    timeframe_result: dict | None = None,
    timestamp: int | None = None,
    long_decision: str = "SKIP",
    short_decision: str = "SKIP",
) -> dict:
    ts = timestamp if timestamp is not None else 1_700_000_000_000 + replay_index * 3_600_000
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "replay_index": replay_index,
        "signal_timestamp": ts,
        "signal": signal,
        "confidence": 0.0,
        "decision": decision,
        "decision_direction": decision_direction,
        "decision_score": 0.0,
        "decision_reason": "",
        "long": {
            "decision": long_decision,
            "breakout_confirmed": False,
            "breakout_detected": False,
            "breakout_score": 0.0,
            "trend_quality": None,
            "volume_quality": None,
            "structure_quality": None,
            "structure_state": "",
            "confidence": None,
            "blocker_codes": [],
        },
        "short": {
            "decision": short_decision,
            "breakout_confirmed": False,
            "breakout_detected": False,
            "breakout_score": 0.0,
            "trend_quality": None,
            "volume_quality": None,
            "structure_quality": None,
            "structure_state": "",
            "confidence": None,
            "blocker_codes": [],
        },
        "timeframe_result": timeframe_result if isinstance(timeframe_result, dict) else _tf_result(),
    }


def test_ready_to_take_next_step() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="LONG", decision="TAKE", decision_direction="LONG", timeframe_result=_tf_result(long_ready=True), long_decision="TAKE"),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "ENTRY_CONFIRMED"
    assert event["wait_steps"] == 1


def test_ready_to_take_third_allowed_step() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
        _entry(2, signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
        _entry(3, signal="LONG", decision="TAKE", decision_direction="LONG", timeframe_result=_tf_result(long_ready=True), long_decision="TAKE"),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "ENTRY_CONFIRMED"
    assert event["wait_steps"] == 3


def test_ready_to_expired() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
        _entry(2, signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
        _entry(3, signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "EXPIRED"
    assert event["resolution_reason"] == "MAX_WAIT_EXCEEDED"


def test_ready_cancelled_by_opposite_take() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="SHORT", decision="TAKE", decision_direction="SHORT", timeframe_result=_tf_result(long_ready=True), short_decision="TAKE"),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "CANCELLED"
    assert event["resolution_reason"] == "OPPOSITE_TAKE"


def test_ready_cancelled_by_breakout_loss() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="LONG", timeframe_result=_tf_result(long_ready=False)),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "CANCELLED"
    assert event["resolution_reason"] == "BREAKOUT_NOT_CONFIRMED"


def test_breakout_loss_observe_confirms_take_within_window() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="WAIT", timeframe_result=_tf_result(long_ready=False)),
        _entry(
            2,
            signal="LONG",
            decision="TAKE",
            decision_direction="LONG",
            timeframe_result=_tf_result(long_ready=True),
            long_decision="TAKE",
        ),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3, breakout_loss_policy="observe")
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "ENTRY_CONFIRMED"
    assert event["resolution_reason"] == "SAME_DIRECTION_TAKE"
    assert event["wait_steps"] == 2


def test_breakout_loss_cancel_still_cancels_immediately() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="WAIT", timeframe_result=_tf_result(long_ready=False)),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3, breakout_loss_policy="cancel")
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "CANCELLED"
    assert event["resolution_reason"] == "BREAKOUT_NOT_CONFIRMED"
    assert event["wait_steps"] == 1


def test_breakout_loss_observe_expires_at_window_end() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="WAIT", timeframe_result=_tf_result(long_ready=False)),
        _entry(2, signal="WAIT", timeframe_result=_tf_result(long_ready=False)),
        _entry(3, signal="WAIT", timeframe_result=_tf_result(long_ready=False)),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3, breakout_loss_policy="observe")
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "EXPIRED"
    assert event["resolution_reason"] == "MAX_WAIT_EXCEEDED"
    assert event["wait_steps"] == 3


def test_breakout_loss_policy_defaults_to_cancel() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="WAIT", timeframe_result=_tf_result(long_ready=False)),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["status"] == "CANCELLED"
    assert event["resolution_reason"] == "BREAKOUT_NOT_CONFIRMED"


def test_persistent_ready_creates_single_event() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(2, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(3, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert result["summary"]["ready_events"] == 1
    assert len(result["events"]) == 1


def test_new_event_after_false_gap() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="LONG", decision="TAKE", decision_direction="LONG", timeframe_result=_tf_result(long_ready=True), long_decision="TAKE"),
        _entry(2, signal="WAIT", timeframe_result=_tf_result(long_ready=False)),
        _entry(3, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(4, signal="LONG", decision="TAKE", decision_direction="LONG", timeframe_result=_tf_result(long_ready=True), long_decision="TAKE"),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert result["summary"]["ready_events"] == 2
    assert all(event["status"] == "ENTRY_CONFIRMED" for event in result["events"])


def test_long_and_short_independent_tracking() -> None:
    replay = [
        _entry(0, symbol="BTC/USDT", signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(0, symbol="ETH/USDT", signal="SHORT", timeframe_result=_tf_result(long_ready=False, short_ready=True)),
        _entry(1, symbol="BTC/USDT", signal="LONG", decision="TAKE", decision_direction="LONG", timeframe_result=_tf_result(long_ready=True), long_decision="TAKE"),
        _entry(1, symbol="ETH/USDT", signal="SHORT", decision="TAKE", decision_direction="SHORT", timeframe_result=_tf_result(long_ready=False, short_ready=True), short_decision="TAKE"),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    assert result["summary"]["ready_events"] == 2
    assert result["summary"]["entry_confirmed"] == 2
    assert {event["direction"] for event in result["events"]} == {"LONG", "SHORT"}


def test_empty_replay_results() -> None:
    result = validate_ready_to_take([])
    assert result["events"] == []
    assert result["summary"]["ready_events"] == 0


def test_malformed_records_are_safe() -> None:
    replay = [
        None,
        {},
        {"symbol": "BTC/USDT", "timeframe": "1h", "replay_index": 1, "timeframe_result": "bad"},
        _entry(2, timeframe_result={"quality": "bad", "decision_details": []}),
    ]

    result = validate_ready_to_take(replay)
    assert isinstance(result, dict)
    assert "events" in result and "summary" in result


def test_input_not_mutated() -> None:
    replay = [
        _entry(0, signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, signal="LONG", decision="TAKE", decision_direction="LONG", timeframe_result=_tf_result(long_ready=True), long_decision="TAKE"),
    ]

    original = deepcopy(replay)
    _ = validate_ready_to_take(replay)
    assert replay == original


def test_conversion_rate_and_aggregates() -> None:
    replay = [
        _entry(0, symbol="A", timeframe="1h", signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, symbol="A", timeframe="1h", signal="LONG", decision="TAKE", decision_direction="LONG", timeframe_result=_tf_result(long_ready=True), long_decision="TAKE"),

        _entry(0, symbol="B", timeframe="1h", signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, symbol="B", timeframe="1h", signal="SHORT", decision="TAKE", decision_direction="SHORT", timeframe_result=_tf_result(long_ready=True), short_decision="TAKE"),

        _entry(0, symbol="C", timeframe="4h", signal="LONG", timeframe_result=_tf_result(long_ready=True)),
        _entry(1, symbol="C", timeframe="4h", signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
        _entry(2, symbol="C", timeframe="4h", signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
        _entry(3, symbol="C", timeframe="4h", signal="WAIT", timeframe_result=_tf_result(long_ready=True)),
    ]

    result = validate_ready_to_take(replay, max_wait_steps=3)
    summary = result["summary"]

    assert summary["ready_events"] == 3
    assert summary["entry_confirmed"] == 1
    assert summary["cancelled"] == 1
    assert summary["expired"] == 1
    assert abs(summary["conversion_rate"] - (1.0 / 3.0)) < 1e-9

    assert "LONG" in summary["by_direction"]
    assert summary["by_timeframe"]["1h"]["ready_events"] == 2
    assert summary["by_timeframe"]["4h"]["ready_events"] == 1


if __name__ == "__main__":
    tests = [
        test_ready_to_take_next_step,
        test_ready_to_take_third_allowed_step,
        test_ready_to_expired,
        test_ready_cancelled_by_opposite_take,
        test_ready_cancelled_by_breakout_loss,
        test_breakout_loss_observe_confirms_take_within_window,
        test_breakout_loss_cancel_still_cancels_immediately,
        test_breakout_loss_observe_expires_at_window_end,
        test_breakout_loss_policy_defaults_to_cancel,
        test_persistent_ready_creates_single_event,
        test_new_event_after_false_gap,
        test_long_and_short_independent_tracking,
        test_empty_replay_results,
        test_malformed_records_are_safe,
        test_input_not_mutated,
        test_conversion_rate_and_aggregates,
    ]

    for test in tests:
        test()

    print(f"{len(tests)} ready->take replay tests passed.")
