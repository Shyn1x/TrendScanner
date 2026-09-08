from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from analysis import analyze_timeframe
from ready_engine import evaluate_ready_candidate


SIGNAL_INDEX = -2
LAST_INDEX = -1


def _base_frame(seed: int, rows: int = 140) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.linspace(0.0, 8.0, rows) + np.cumsum(rng.normal(0.0, 0.35, rows))
    open_ = close - rng.uniform(-0.25, 0.25, rows)
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.45, rows)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.45, rows)
    volume = rng.uniform(800.0, 1200.0, rows)
    return pd.DataFrame(
        {
            "time": np.arange(rows, dtype=np.int64) * 4 * 60 * 60 * 1000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def _last_candle_variants(frame: pd.DataFrame) -> list[dict[str, float]]:
    signal_close = float(frame.iloc[SIGNAL_INDEX]["close"])
    historical_volume = float(frame.iloc[:SIGNAL_INDEX]["volume"].mean())
    return [
        {
            "open": signal_close * 1.001,
            "high": signal_close * 1.004,
            "low": signal_close * 0.999,
            "close": signal_close * 1.002,
            "volume": historical_volume,
        },
        {
            "open": signal_close,
            "high": signal_close * 1.60,
            "low": signal_close * 0.35,
            "close": signal_close * 0.50,
            "volume": historical_volume * 60.0,
        },
        {
            "open": signal_close * 1.001,
            "high": signal_close * 1.004,
            "low": signal_close * 0.999,
            "close": signal_close * 1.002,
            "volume": historical_volume * 100.0,
        },
        {
            "open": signal_close,
            "high": signal_close * 1.60,
            "low": signal_close * 0.99,
            "close": signal_close * 1.50,
            "volume": historical_volume,
        },
        {
            "open": signal_close,
            "high": signal_close * 1.01,
            "low": signal_close * 0.40,
            "close": signal_close * 0.50,
            "volume": historical_volume,
        },
        {
            "open": signal_close,
            "high": signal_close,
            "low": signal_close,
            "close": signal_close,
            "volume": 0.0,
        },
    ]


def _with_last_candle(frame: pd.DataFrame, values: dict[str, float]) -> pd.DataFrame:
    result = frame.copy(deep=True)
    for column, value in values.items():
        result.loc[result.index[LAST_INDEX], column] = value
    return result


def _without_latest_close(result: dict) -> dict:
    normalized = copy.deepcopy(result)
    for direction in ("LONG", "SHORT"):
        normalized["quality"][direction]["breakout_quality"].pop("latest_close", None)
    return normalized


def _assert_same_decision_result(frame_a: pd.DataFrame, frame_b: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(frame_a.iloc[:LAST_INDEX], frame_b.iloc[:LAST_INDEX])
    result_a = analyze_timeframe(frame_a)
    result_b = analyze_timeframe(frame_b)

    assert _without_latest_close(result_a) == _without_latest_close(result_b)

    for timeframe in ("4h", "1h"):
        for direction in ("LONG", "SHORT"):
            candidate_a = evaluate_ready_candidate(
                "TEST/USDT", timeframe, direction, result_a
            )
            candidate_b = evaluate_ready_candidate(
                "TEST/USDT", timeframe, direction, result_b
            )
            assert candidate_a == candidate_b


def test_signal_decisions_are_invariant_to_current_candle_content() -> None:
    for seed in (7, 19, 101):
        base = _base_frame(seed)
        normal = _with_last_candle(base, _last_candle_variants(base)[0])
        for variant in _last_candle_variants(base)[1:]:
            extreme = _with_last_candle(base, variant)
            _assert_same_decision_result(normal, extreme)
