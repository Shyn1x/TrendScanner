from __future__ import annotations

import copy

import pandas as pd

from ready_outcome_analyzer import analyze_ready_outcomes, _extract_direction_pipeline_fields


def _assert_close(actual: float | None, expected: float, eps: float = 1e-9) -> None:
    assert actual is not None
    assert abs(actual - expected) <= eps, f"expected {expected}, got {actual}"


def _make_df(
    times: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000.0 for _ in times],
        }
    )


def _tf_result(*, long_ready: bool = False, short_ready: bool = False) -> dict:
    return {
        "quality": {
            "LONG": {
                "confirmed": long_ready,
                "signal": "LONG" if long_ready else "WAIT",
                "confidence": {"confidence": 70.0 if long_ready else 0.0},
                "trend_quality": {"trend_quality_score": 60.0 if long_ready else 0.0},
                "volume_quality": {"volume_score": 80.0 if long_ready else 0.0},
                "breakout_quality": {"confirmed": long_ready, "breakout_score": 80.0 if long_ready else 0.0},
            },
            "SHORT": {
                "confirmed": short_ready,
                "signal": "SHORT" if short_ready else "WAIT",
                "confidence": {"confidence": 70.0 if short_ready else 0.0},
                "trend_quality": {"trend_quality_score": 60.0 if short_ready else 0.0},
                "volume_quality": {"volume_score": 80.0 if short_ready else 0.0},
                "breakout_quality": {"confirmed": short_ready, "breakout_score": 80.0 if short_ready else 0.0},
            },
        },
        "decision_details": {
            "LONG": {
                "decision": "WATCH",
                "direction": "LONG",
                "decision_score": 55.0 if long_ready else 0.0,
                "confidence": 70.0 if long_ready else 0.0,
                "signal": "LONG" if long_ready else "WAIT",
                "breakout_confirmed": long_ready,
                "blockers": [],
                "component_scores": {
                    "trend_quality": 60.0 if long_ready else 0.0,
                    "volume_quality": 80.0 if long_ready else 0.0,
                    "breakout_quality": 80.0 if long_ready else 0.0,
                    "structure_quality": 50.0,
                },
                "market_context": {"alignment": "ALIGNED"},
            },
            "SHORT": {
                "decision": "WATCH",
                "direction": "SHORT",
                "decision_score": 55.0 if short_ready else 0.0,
                "confidence": 70.0 if short_ready else 0.0,
                "signal": "SHORT" if short_ready else "WAIT",
                "breakout_confirmed": short_ready,
                "blockers": [],
                "component_scores": {
                    "trend_quality": 60.0 if short_ready else 0.0,
                    "volume_quality": 80.0 if short_ready else 0.0,
                    "breakout_quality": 80.0 if short_ready else 0.0,
                    "structure_quality": 50.0,
                },
                "market_context": {"alignment": "ALIGNED"},
            },
        },
    }


def _entry(replay_index: int, timestamp: int | None, tf_result: dict, symbol: str = "BTC/USDT") -> dict:
    return {
        "symbol": symbol,
        "timeframe": "4h",
        "replay_index": replay_index,
        "signal_timestamp": timestamp,
        "timeframe_result": tf_result,
    }


def _tf_result_pipeline(
    *,
    long_ready: bool = False,
    short_ready: bool = False,
    long_breakout_score: float | None = 81.0,
    short_breakout_score: float | None = 79.0,
    long_trend_quality: float | None = 61.0,
    short_trend_quality: float | None = 62.0,
    long_volume_quality: float | None = 71.0,
    short_volume_quality: float | None = 72.0,
    long_structure_quality: float | None = 51.0,
    short_structure_quality: float | None = 52.0,
    long_structure_state: str | None = "ALIGNED",
    short_structure_state: str | None = "OPPOSED",
    long_blockers: list | None = None,
    short_blockers: list | None = None,
) -> dict:
    def quality_direction(
        ready: bool,
        signal: str,
        breakout_score: float | None,
        trend_quality: float | None,
        volume_quality: float | None,
        structure_quality: float | None,
        structure_state: str | None,
    ) -> dict:
        q = {
            "confirmed": ready,
            "signal": signal if ready else "WAIT",
            "confidence": {"confidence": 70.0 if ready else 0.0},
            "trend_quality": {"trend_quality_score": trend_quality},
            "volume_quality": {"volume_score": volume_quality},
            "breakout_quality": {
                "confirmed": ready,
                "breakout_score": breakout_score,
            },
        }
        if structure_quality is not None:
            q["structure_quality"] = {"structure_score": structure_quality}
        if structure_state is not None:
            q["market_structure"] = {"structure": structure_state}
        return q

    return {
        "quality": {
            "LONG": quality_direction(
                long_ready,
                "LONG",
                long_breakout_score,
                long_trend_quality,
                long_volume_quality,
                long_structure_quality,
                long_structure_state,
            ),
            "SHORT": quality_direction(
                short_ready,
                "SHORT",
                short_breakout_score,
                short_trend_quality,
                short_volume_quality,
                short_structure_quality,
                short_structure_state,
            ),
        },
        "decision_details": {
            "LONG": {
                "decision": "WATCH",
                "direction": "LONG",
                "decision_score": 55.0 if long_ready else 0.0,
                "confidence": 70.0 if long_ready else 0.0,
                "signal": "LONG" if long_ready else "WAIT",
                "breakout_confirmed": long_ready,
                "blockers": long_blockers if long_blockers is not None else [],
                "component_scores": {
                    "trend_quality": long_trend_quality,
                    "volume_quality": long_volume_quality,
                    "breakout_quality": long_breakout_score,
                    "structure_quality": long_structure_quality,
                },
                "market_context": {"alignment": "ALIGNED"},
            },
            "SHORT": {
                "decision": "WATCH",
                "direction": "SHORT",
                "decision_score": 55.0 if short_ready else 0.0,
                "confidence": 70.0 if short_ready else 0.0,
                "signal": "SHORT" if short_ready else "WAIT",
                "breakout_confirmed": short_ready,
                "blockers": short_blockers if short_blockers is not None else [],
                "component_scores": {
                    "trend_quality": short_trend_quality,
                    "volume_quality": short_volume_quality,
                    "breakout_quality": short_breakout_score,
                    "structure_quality": short_structure_quality,
                },
                "market_context": {"alignment": "OPPOSED"},
            },
        },
    }


def test_long_metrics() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100, 100, 100, 100, 100],
        highs=[101, 101, 106, 102, 104],
        lows=[99, 99, 95, 97, 98],
        closes=[100, 100, 101, 98, 103],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    event = data["events"][0]
    h = event["horizons"]["3"]

    _assert_close(h["mfe_pct"], 6.0)
    _assert_close(h["mae_pct"], 5.0)
    _assert_close(h["close_return_pct"], 3.0)


def test_short_metrics() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100, 100, 100, 100, 100],
        highs=[101, 101, 104, 108, 106],
        lows=[99, 99, 95, 96, 97],
        closes=[100, 100, 97, 103, 98],
    )
    replay = [_entry(0, 20, _tf_result(short_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    event = data["events"][0]
    h = event["horizons"]["3"]

    _assert_close(h["mfe_pct"], 5.0)
    _assert_close(h["mae_pct"], 8.0)
    _assert_close(h["close_return_pct"], 2.0)


def test_future_window_starts_after_signal() -> None:
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100, 100, 100, 100],
        highs=[500, 101, 101, 101],
        lows=[1, 99, 99, 99],
        closes=[100, 100, 100, 100],
    )
    replay = [_entry(0, 10, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]

    _assert_close(h["mfe_pct"], 1.0)
    _assert_close(h["mae_pct"], 1.0)


def test_ready_bar_excluded_from_outcome() -> None:
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100, 100, 100, 100],
        highs=[101, 150, 101, 101],
        lows=[99, 50, 99, 99],
        closes=[100, 100, 100, 100],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(2,))
    h = data["events"][0]["horizons"]["2"]

    _assert_close(h["mfe_pct"], 1.0)
    _assert_close(h["mae_pct"], 1.0)


def test_continuous_ready_is_single_event() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50, 60],
        opens=[100] * 6,
        highs=[101] * 6,
        lows=[99] * 6,
        closes=[100] * 6,
    )
    replay = [
        _entry(0, 20, _tf_result(long_ready=True)),
        _entry(1, 30, _tf_result(long_ready=True)),
        _entry(2, 40, _tf_result(long_ready=True)),
    ]

    data = analyze_ready_outcomes(df, replay, horizons=(2,))
    assert len(data["events"]) == 1


def test_false_gap_creates_new_event() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50, 60, 70],
        opens=[100] * 7,
        highs=[101] * 7,
        lows=[99] * 7,
        closes=[100] * 7,
    )
    replay = [
        _entry(0, 20, _tf_result(long_ready=True)),
        _entry(1, 30, _tf_result(long_ready=True)),
        _entry(2, 40, _tf_result(long_ready=False)),
        _entry(3, 50, _tf_result(long_ready=True)),
    ]

    data = analyze_ready_outcomes(df, replay, horizons=(2,))
    assert len(data["events"]) == 2


def test_incomplete_horizon_available_false() -> None:
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101] * 4,
        lows=[99] * 4,
        closes=[100] * 4,
    )
    replay = [_entry(0, 30, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(2, 3))
    event = data["events"][0]
    assert event["horizons"]["2"]["available"] is False
    assert event["horizons"]["3"]["available"] is False


def test_bars_to_extremes_take_first_occurrence() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100] * 5,
        highs=[101, 101, 105, 105, 104],
        lows=[99, 99, 98, 98, 99],
        closes=[100] * 5,
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]
    assert h["bars_to_mfe"] == 1
    assert h["bars_to_mae"] == 1


def test_timestamp_not_found_skips_event() -> None:
    df = _make_df(
        times=[10, 20, 30],
        opens=[100] * 3,
        highs=[101] * 3,
        lows=[99] * 3,
        closes=[100] * 3,
    )
    replay = [_entry(0, 999, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(1,))
    assert data["summary"]["ready_events"] == 0
    assert data["summary"]["diagnostics"]["timestamp_not_found"] == 1


def test_invalid_price_is_handled() -> None:
    df = _make_df(
        times=[10, 20, 30],
        opens=[100] * 3,
        highs=[101] * 3,
        lows=[99] * 3,
        closes=[100, 0, 100],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(1,))
    assert data["summary"]["ready_events"] == 0
    assert data["summary"]["diagnostics"]["invalid_price"] == 1


def test_malformed_replay_entry_is_handled() -> None:
    df = _make_df(
        times=[10, 20, 30],
        opens=[100] * 3,
        highs=[101] * 3,
        lows=[99] * 3,
        closes=[100] * 3,
    )
    replay = [{"symbol": "BTC/USDT"}, "bad"]

    data = analyze_ready_outcomes(df, replay, horizons=(1,))
    assert data["summary"]["diagnostics"]["malformed_replay_entry"] == 2
    assert data["summary"]["ready_events"] == 0


def test_inputs_not_mutated() -> None:
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101] * 4,
        lows=[99] * 4,
        closes=[100] * 4,
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    df_before = df.copy(deep=True)
    replay_before = copy.deepcopy(replay)

    _ = analyze_ready_outcomes(df, replay, horizons=(2,))

    assert df.equals(df_before)
    assert replay == replay_before


def test_summary_and_rates() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50, 60],
        opens=[100] * 6,
        highs=[101, 101, 102, 101, 103, 101],
        lows=[99, 99, 99, 98, 100, 99],
        closes=[100, 100, 101, 99, 102, 100],
    )
    replay = [
        _entry(0, 20, _tf_result(long_ready=True), symbol="A"),
        _entry(1, 30, _tf_result(long_ready=True), symbol="B"),
    ]

    data = analyze_ready_outcomes(df, replay, horizons=(2,))
    summary = data["summary"]

    assert summary["ready_events"] == 2
    assert summary["by_direction"]["LONG"] == 2
    by_h = summary["by_horizon"]["2"]
    assert by_h["available_events"] == 2
    assert by_h["positive_close_rate"] == 50.0
    assert by_h["mfe_ge_1_pct_rate"] == 100.0


def test_empty_inputs() -> None:
    df = _make_df(times=[], opens=[], highs=[], lows=[], closes=[])
    data = analyze_ready_outcomes(df, [], horizons=(3,))

    assert data["events"] == []
    assert data["summary"]["ready_events"] == 0
    assert data["summary"]["by_horizon"]["3"]["available_events"] == 0


def test_long_short_tracked_independently() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100] * 5,
        highs=[101] * 5,
        lows=[99] * 5,
        closes=[100] * 5,
    )
    replay = [
        _entry(0, 20, _tf_result(long_ready=True, short_ready=False)),
        _entry(1, 30, _tf_result(long_ready=True, short_ready=True)),
        _entry(2, 40, _tf_result(long_ready=True, short_ready=False)),
        _entry(3, 50, _tf_result(long_ready=True, short_ready=True)),
    ]

    data = analyze_ready_outcomes(df, replay, horizons=(1,))

    by_direction = data["summary"]["by_direction"]
    assert by_direction["LONG"] == 1
    assert by_direction["SHORT"] == 2


def test_bars_to_extremes_first_tie_short() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100] * 5,
        highs=[101, 101, 104, 104, 102],
        lows=[99, 99, 95, 95, 96],
        closes=[100] * 5,
    )
    replay = [_entry(0, 20, _tf_result(short_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]
    assert h["bars_to_mfe"] == 1
    assert h["bars_to_mae"] == 1


def test_timestamp_ambiguous_skips_event() -> None:
    df = _make_df(
        times=[10, 20, 20, 30],
        opens=[100] * 4,
        highs=[101] * 4,
        lows=[99] * 4,
        closes=[100] * 4,
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(1,))
    assert data["summary"]["ready_events"] == 0
    assert data["summary"]["diagnostics"]["timestamp_not_found"] == 1


def test_nan_price_is_invalid() -> None:
    df = _make_df(
        times=[10, 20, 30],
        opens=[100] * 3,
        highs=[101] * 3,
        lows=[99] * 3,
        closes=[100, float("nan"), 100],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(1,))
    assert data["summary"]["diagnostics"]["invalid_price"] == 1


def test_long_positive_metrics_unchanged() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100, 100, 100, 100, 100],
        highs=[101, 101, 106, 102, 104],
        lows=[99, 99, 95, 97, 98],
        closes=[100, 100, 101, 98, 103],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]

    _assert_close(h["mfe_pct"], 6.0)
    _assert_close(h["mae_pct"], 5.0)
    _assert_close(h["close_return_pct"], 3.0)
    assert h["bars_to_mfe"] == 1
    assert h["bars_to_mae"] == 1


def test_short_positive_metrics_unchanged() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100, 100, 100, 100, 100],
        highs=[101, 101, 104, 108, 106],
        lows=[99, 99, 95, 96, 97],
        closes=[100, 100, 97, 103, 98],
    )
    replay = [_entry(0, 20, _tf_result(short_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]

    _assert_close(h["mfe_pct"], 5.0)
    _assert_close(h["mae_pct"], 8.0)
    _assert_close(h["close_return_pct"], 2.0)
    assert h["bars_to_mfe"] == 1
    assert h["bars_to_mae"] == 2


def test_short_all_future_highs_below_entry_mae_zero_and_no_bars() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100] * 5,
        highs=[101, 101, 99, 98, 97],
        lows=[99, 99, 95, 94, 93],
        closes=[100, 100, 96, 95, 94],
    )
    replay = [_entry(0, 20, _tf_result(short_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]

    _assert_close(h["mae_pct"], 0.0)
    assert h["bars_to_mae"] is None


def test_long_all_future_lows_above_entry_mae_zero_and_no_bars() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100] * 5,
        highs=[101, 101, 103, 104, 102],
        lows=[99, 99, 101, 102, 101],
        closes=[100, 100, 102, 103, 101],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]

    _assert_close(h["mae_pct"], 0.0)
    assert h["bars_to_mae"] is None


def test_short_no_favorable_movement_mfe_zero_and_no_bars() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100] * 5,
        highs=[101, 101, 103, 104, 102],
        lows=[99, 99, 100, 101, 100],
        closes=[100, 100, 102, 103, 101],
    )
    replay = [_entry(0, 20, _tf_result(short_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]

    _assert_close(h["mfe_pct"], 0.0)
    assert h["bars_to_mfe"] is None


def test_long_no_favorable_movement_mfe_zero_and_no_bars() -> None:
    df = _make_df(
        times=[10, 20, 30, 40, 50],
        opens=[100] * 5,
        highs=[101, 101, 100, 99, 100],
        lows=[99, 99, 95, 96, 97],
        closes=[100, 100, 98, 97, 99],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(3,))
    h = data["events"][0]["horizons"]["3"]

    _assert_close(h["mfe_pct"], 0.0)
    assert h["bars_to_mfe"] is None


def test_input_not_mutated() -> None:
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101] * 4,
        lows=[99] * 4,
        closes=[100] * 4,
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    df_before = df.copy(deep=True)
    replay_before = copy.deepcopy(replay)

    _ = analyze_ready_outcomes(df, replay, horizons=(2,))

    assert df.equals(df_before)
    assert replay == replay_before


def test_long_extracts_all_pipeline_fields() -> None:
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101, 102, 103, 104],
        lows=[99, 98, 97, 96],
        closes=[100, 101, 102, 103],
    )
    replay = [_entry(0, 20, _tf_result_pipeline(long_ready=True, short_ready=False, long_blockers=[]))]

    data = analyze_ready_outcomes(df, replay, horizons=(2,))
    event = data["events"][0]

    assert event["direction"] == "LONG"
    assert event["breakout_score"] == 81.0
    assert event["breakout_confirmed"] is True
    assert event["trend_quality"] == 61.0
    assert event["volume_quality"] == 71.0
    assert event["structure_quality"] == 51.0
    assert event["structure_state"] == "ALIGNED"
    assert event["blocker_codes"] == []


def test_short_uses_short_direction_fields() -> None:
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101, 102, 103, 104],
        lows=[99, 98, 97, 96],
        closes=[100, 101, 102, 103],
    )
    replay = [_entry(0, 20, _tf_result_pipeline(long_ready=False, short_ready=True, short_blockers=[]))]

    data = analyze_ready_outcomes(df, replay, horizons=(2,))
    event = data["events"][0]

    assert event["direction"] == "SHORT"
    assert event["breakout_score"] == 79.0
    assert event["trend_quality"] == 62.0
    assert event["volume_quality"] == 72.0
    assert event["structure_quality"] == 52.0
    assert event["structure_state"] == "OPPOSED"
    assert event["blocker_codes"] == []


def test_missing_optional_fields_stay_none() -> None:
    tf = {
        "quality": {
            "LONG": {
                "confirmed": True,
                "signal": "LONG",
                "confidence": {"confidence": 70.0},
                "trend_quality": {},
                "volume_quality": {},
                "breakout_quality": {},
            }
        },
        "decision_details": {
            "LONG": {
                "decision": "WATCH",
                "decision_score": 55.0,
                "signal": "LONG",
                "breakout_confirmed": True,
                "component_scores": {},
                "market_context": {},
            }
        },
    }
    fields, presence = _extract_direction_pipeline_fields(tf, "LONG")

    assert fields["breakout_score"] is None
    assert fields["trend_quality"] is None
    assert fields["volume_quality"] is None
    assert fields["structure_quality"] is None
    assert fields["structure_state"] is None
    assert fields["blocker_codes"] is None
    assert presence["breakout_score"] is False
    assert presence["trend_quality"] is False
    assert presence["volume_quality"] is False
    assert presence["structure_quality"] is False
    assert presence["structure_state"] is False
    assert presence["blocker_codes"] is False


def test_empty_blockers_stays_empty_list() -> None:
    tf = _tf_result_pipeline(long_ready=True, short_ready=False, long_blockers=[])
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101] * 4,
        lows=[99] * 4,
        closes=[100] * 4,
    )
    data = analyze_ready_outcomes(df, [_entry(0, 20, tf)], horizons=(2,))
    assert data["events"][0]["blocker_codes"] == []


def test_blocker_codes_from_dict_and_string() -> None:
    tf = _tf_result_pipeline(
        long_ready=True,
        short_ready=False,
        long_blockers=[{"code": "C1"}, {"message": "MSG"}, "RAW"],
    )
    fields, presence = _extract_direction_pipeline_fields(tf, "LONG")
    assert fields["blocker_codes"] == ["C1", "MSG", "RAW"]
    assert presence["blocker_codes"] is True


def test_malformed_nested_no_crash() -> None:
    tf = {
        "quality": {"LONG": "bad", "SHORT": "bad"},
        "decision_details": {"LONG": "bad", "SHORT": "bad"},
    }
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101] * 4,
        lows=[99] * 4,
        closes=[100] * 4,
    )
    data = analyze_ready_outcomes(df, [_entry(0, 20, tf)], horizons=(2,))
    assert isinstance(data, dict)


def test_pipeline_field_presence_summary() -> None:
    tf = _tf_result_pipeline(long_ready=True, short_ready=False, long_blockers=[])
    df = _make_df(
        times=[10, 20, 30, 40],
        opens=[100] * 4,
        highs=[101] * 4,
        lows=[99] * 4,
        closes=[100] * 4,
    )
    data = analyze_ready_outcomes(df, [_entry(0, 20, tf)], horizons=(2,))
    presence = data["summary"]["pipeline_field_presence"]
    assert presence["breakout_score"]["present"] == 1
    assert presence["trend_quality"]["present"] == 1
    assert presence["volume_quality"]["present"] == 1
    assert presence["structure_quality"]["present"] == 1
    assert presence["structure_state"]["present"] == 1
    assert presence["blocker_codes"]["present"] == 1


if __name__ == "__main__":
    tests = [
        test_long_metrics,
        test_short_metrics,
        test_future_window_starts_after_signal,
        test_ready_bar_excluded_from_outcome,
        test_continuous_ready_is_single_event,
        test_false_gap_creates_new_event,
        test_incomplete_horizon_available_false,
        test_bars_to_extremes_take_first_occurrence,
        test_timestamp_not_found_skips_event,
        test_invalid_price_is_handled,
        test_malformed_replay_entry_is_handled,
        test_inputs_not_mutated,
        test_summary_and_rates,
        test_empty_inputs,
        test_long_short_tracked_independently,
        test_bars_to_extremes_first_tie_short,
        test_timestamp_ambiguous_skips_event,
        test_nan_price_is_invalid,
        test_long_positive_metrics_unchanged,
        test_short_positive_metrics_unchanged,
        test_short_all_future_highs_below_entry_mae_zero_and_no_bars,
        test_long_all_future_lows_above_entry_mae_zero_and_no_bars,
        test_short_no_favorable_movement_mfe_zero_and_no_bars,
        test_long_no_favorable_movement_mfe_zero_and_no_bars,
        test_input_not_mutated,
        test_long_extracts_all_pipeline_fields,
        test_short_uses_short_direction_fields,
        test_missing_optional_fields_stay_none,
        test_empty_blockers_stays_empty_list,
        test_blocker_codes_from_dict_and_string,
        test_malformed_nested_no_crash,
        test_pipeline_field_presence_summary,
    ]

    for test in tests:
        test()

    print(f"{len(tests)} ready outcome analyzer tests passed.")
