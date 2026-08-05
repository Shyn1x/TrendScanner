from __future__ import annotations

import copy
import math

import pandas as pd

from ready_outcome_analyzer import analyze_ready_outcomes


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


def _dir_payload(direction: str, ready: bool) -> dict:
    signal = direction if ready else "WAIT"
    confidence = 70.0 if ready else 0.0
    decision_score = 55.0 if ready else 0.0
    trend = 60.0 if ready else 0.0
    volume = 80.0 if ready else 0.0

    return {
        "confirmed": ready,
        "signal": signal,
        "confidence": {"confidence": confidence},
        "trend_quality": {"trend_quality_score": trend},
        "volume_quality": {"volume_score": volume},
        "breakout_quality": {
            "confirmed": ready,
            "breakout_score": 80.0 if ready else 0.0,
        },
        "decision": "WATCH",
        "direction": direction,
        "decision_score": decision_score,
        "breakout_confirmed": ready,
        "blockers": [],
        "component_scores": {
            "trend_quality": trend,
            "volume_quality": volume,
            "breakout_quality": 80.0 if ready else 0.0,
            "structure_quality": 50.0,
        },
        "market_context": {"alignment": "ALIGNED"},
    }


def _tf_result(*, long_ready: bool = False, short_ready: bool = False) -> dict:
    return {
        "quality": {
            "LONG": {
                "confirmed": long_ready,
                "signal": "LONG" if long_ready else "WAIT",
                "confidence": {"confidence": 70.0 if long_ready else 0.0},
                "trend_quality": {"trend_quality_score": 60.0 if long_ready else 0.0},
                "volume_quality": {"volume_score": 80.0 if long_ready else 0.0},
                "breakout_quality": {
                    "confirmed": long_ready,
                    "breakout_score": 80.0 if long_ready else 0.0,
                },
            },
            "SHORT": {
                "confirmed": short_ready,
                "signal": "SHORT" if short_ready else "WAIT",
                "confidence": {"confidence": 70.0 if short_ready else 0.0},
                "trend_quality": {"trend_quality_score": 60.0 if short_ready else 0.0},
                "volume_quality": {"volume_score": 80.0 if short_ready else 0.0},
                "breakout_quality": {
                    "confirmed": short_ready,
                    "breakout_score": 80.0 if short_ready else 0.0,
                },
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
        "timeframe": "1h",
        "replay_index": replay_index,
        "signal_timestamp": timestamp,
        "timeframe_result": tf_result,
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
    replay = [
        {"symbol": "BTC/USDT"},
        "bad",
    ]

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
        closes=[100, math.nan, 100],
    )
    replay = [_entry(0, 20, _tf_result(long_ready=True))]

    data = analyze_ready_outcomes(df, replay, horizons=(1,))
    assert data["summary"]["diagnostics"]["invalid_price"] == 1


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
    ]

    for test in tests:
        test()

    print(f"{len(tests)} ready outcome analyzer tests passed.")
