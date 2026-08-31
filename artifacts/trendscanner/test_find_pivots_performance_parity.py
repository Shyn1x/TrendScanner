"""Exact-parity coverage for the low-level find_pivots optimization."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from trendlines import find_pivots


def _reference_find_pivots(df, window=5):
    highs = []
    lows = []

    for i in range(window, len(df) - window):
        high = df.high.iloc[i]
        low = df.low.iloc[i]

        if high == max(df.high.iloc[i - window:i + window]):
            highs.append((i, high))

        if low == min(df.low.iloc[i - window:i + window]):
            lows.append((i, low))

    return highs, lows


def _ohlc_from_high_low(highs, lows):
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    close = (highs + lows) / 2
    return pd.DataFrame({
        "open": close,
        "high": highs,
        "low": lows,
        "close": close,
    })


def _random_ohlc(size, seed):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1, size))
    open_ = close + rng.normal(0, 0.3, size)
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + rng.uniform(0, 1, size),
        "low": np.minimum(open_, close) - rng.uniform(0, 1, size),
        "close": close,
    })


class FindPivotsPerformanceParityTests(unittest.TestCase):
    def assert_exact_parity(self, df, window=5):
        self.assertEqual(find_pivots(df, window=window), _reference_find_pivots(df, window=window))

    def test_empty_dataframe(self):
        self.assert_exact_parity(_ohlc_from_high_low([], []))
        self.assert_exact_parity(pd.DataFrame())

    def test_short_dataframes(self):
        for size in range(1, 11):
            self.assert_exact_parity(_random_ohlc(size, seed=size))
        self.assert_exact_parity(pd.DataFrame(index=range(10)))

    def test_flat_prices(self):
        self.assert_exact_parity(_ohlc_from_high_low([100] * 50, [100] * 50))

    def test_rising_market(self):
        values = np.arange(50, dtype=float)
        self.assert_exact_parity(_ohlc_from_high_low(values + 1, values))

    def test_falling_market(self):
        values = np.arange(50, 0, -1, dtype=float)
        self.assert_exact_parity(_ohlc_from_high_low(values + 1, values))

    def test_zig_zag_data(self):
        values = np.resize(np.array([100, 110, 90, 115, 85], dtype=float), 100)
        self.assert_exact_parity(_ohlc_from_high_low(values + 1, values - 1))

    def test_repeated_highs_and_lows(self):
        values = np.resize(np.array([100, 105, 105, 95, 95, 102], dtype=float), 120)
        self.assert_exact_parity(_ohlc_from_high_low(values, values - 3))

    def test_nan_and_boundary_values(self):
        df = _ohlc_from_high_low([100, np.nan, 105, 102, 110, 101, 99, np.nan, 103, 100, 98],
                                  [90, np.nan, 92, 91, 95, 89, 88, np.nan, 90, 87, 86])
        self.assert_exact_parity(df)

    def test_random_ohlc_sizes_and_seeds(self):
        for size in (50, 120, 200, 500, 1000):
            for seed in (0, 7, 42, 2026):
                with self.subTest(size=size, seed=seed):
                    self.assert_exact_parity(_random_ohlc(size, seed))

    def test_btc_4h_1000_candles_when_network_available(self):
        try:
            from research_data import get_research_data
            df = get_research_data("BTC/USDT", "4h", 1000)
        except Exception as exc:
            self.skipTest(f"BTC data unavailable: {exc}")

        if len(df) < 1000:
            self.skipTest(f"BTC data source returned only {len(df)} candles")

        self.assert_exact_parity(df.iloc[-1000:].reset_index(drop=True))


if __name__ == "__main__":
    unittest.main()