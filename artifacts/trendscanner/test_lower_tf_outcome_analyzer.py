import unittest

import pandas as pd

import lower_tf_outcome_analyzer as analyzer


COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class LowerTfOutcomeAnalyzerTests(unittest.TestCase):
    def test_long_metrics(self):
        future = pd.DataFrame([
            [1, 100, 105, 98, 103, 1],
            [2, 103, 110, 95, 108, 1],
            [3, 108, 109, 101, 104, 1],
        ], columns=COLUMNS)
        result = analyzer._compute_metrics("LONG", 100.0, future)
        self.assertAlmostEqual(result["mfe_pct"], 10.0)
        self.assertAlmostEqual(result["mae_pct"], 5.0)
        self.assertAlmostEqual(result["close_return_pct"], 4.0)
        self.assertEqual(result["bars_to_mfe"], 2)
        self.assertEqual(result["bars_to_mae"], 2)

    def test_short_metrics(self):
        future = pd.DataFrame([
            [1, 100, 104, 96, 97, 1],
            [2, 97, 102, 90, 92, 1],
            [3, 92, 99, 91, 95, 1],
        ], columns=COLUMNS)
        result = analyzer._compute_metrics("SHORT", 100.0, future)
        self.assertAlmostEqual(result["mfe_pct"], 10.0)
        self.assertAlmostEqual(result["mae_pct"], 4.0)
        self.assertAlmostEqual(result["close_return_pct"], 5.0)
        self.assertEqual(result["bars_to_mfe"], 2)
        self.assertEqual(result["bars_to_mae"], 1)

    def test_long_stop_hit_uses_first_touch_and_fixed_loss(self):
        future = pd.DataFrame([
            [1, 100, 103, 99.6, 102, 1],
            [2, 102, 104, 98.9, 103, 1],
            [3, 103, 108, 97, 107, 1],
        ], columns=COLUMNS)
        result = analyzer._compute_stop_metrics("LONG", 100.0, future, 100)
        self.assertAlmostEqual(result["stop_loss_pct"], 1.0)
        self.assertAlmostEqual(result["stop_price"], 99.0)
        self.assertTrue(result["stop_hit"])
        self.assertEqual(result["bars_to_stop"], 2)
        self.assertAlmostEqual(result["horizon_close_return_pct"], 7.0)
        self.assertAlmostEqual(result["stop_adjusted_return_pct"], -1.0)

    def test_long_stop_not_hit_keeps_horizon_close_return(self):
        future = pd.DataFrame([
            [1, 100, 102, 99.51, 101, 1],
            [2, 101, 104, 99.50, 103, 1],
        ], columns=COLUMNS)
        result = analyzer._compute_stop_metrics("LONG", 100.0, future, 50)
        self.assertAlmostEqual(result["stop_price"], 99.5)
        self.assertTrue(result["stop_hit"])
        self.assertEqual(result["bars_to_stop"], 2)

        future.loc[1, "low"] = 99.51
        result = analyzer._compute_stop_metrics("LONG", 100.0, future, 50)
        self.assertFalse(result["stop_hit"])
        self.assertIsNone(result["bars_to_stop"])
        self.assertAlmostEqual(result["horizon_close_return_pct"], 3.0)
        self.assertAlmostEqual(result["stop_adjusted_return_pct"], 3.0)

    def test_short_stop_hit_on_exact_touch(self):
        future = pd.DataFrame([
            [1, 100, 100.74, 96, 97, 1],
            [2, 97, 100.75, 90, 92, 1],
            [3, 92, 104, 88, 89, 1],
        ], columns=COLUMNS)
        result = analyzer._compute_stop_metrics("SHORT", 100.0, future, 75)
        self.assertAlmostEqual(result["stop_price"], 100.75)
        self.assertTrue(result["stop_hit"])
        self.assertEqual(result["bars_to_stop"], 2)
        self.assertAlmostEqual(result["horizon_close_return_pct"], 11.0)
        self.assertAlmostEqual(result["stop_adjusted_return_pct"], -0.75)

    def test_stop_result_changes_when_later_horizon_reaches_stop(self):
        future = pd.DataFrame([
            [1, 100, 102, 99.1, 101, 1],
            [2, 101, 104, 98.9, 103, 1],
            [3, 103, 106, 97, 105, 1],
        ], columns=COLUMNS)
        early = analyzer._compute_stop_metrics("LONG", 100.0, future.iloc[:1], 100)
        later = analyzer._compute_stop_metrics("LONG", 100.0, future, 100)
        self.assertFalse(early["stop_hit"])
        self.assertAlmostEqual(early["stop_adjusted_return_pct"], 1.0)
        self.assertTrue(later["stop_hit"])
        self.assertEqual(later["bars_to_stop"], 2)
        self.assertAlmostEqual(later["stop_adjusted_return_pct"], -1.0)

    def test_stop_loss_must_be_from_preregistered_grid(self):
        future = pd.DataFrame([
            [1, 100, 101, 99, 100, 1],
        ], columns=COLUMNS)
        with self.assertRaisesRegex(ValueError, "INVALID_STOP_LOSS_BPS"):
            analyzer._compute_stop_metrics("LONG", 100.0, future, 125)

    def test_exact_window_has_signal_plus_future_bars(self):
        tf = analyzer.TIMEFRAME_MS["15m"]
        ready = 10 * tf
        rows = []
        for index in range(-1, 4):
            ts = ready + index * tf
            price = 100.0 + index
            rows.append([ts, price, price + 1, price - 1, price, 1])
        source = pd.DataFrame(rows, columns=COLUMNS)
        frame = analyzer._build_exact_window(source, "15m", ready, 3)
        self.assertEqual(len(frame), 4)
        self.assertEqual(frame["time"].tolist(), [ready + i * tf for i in range(4)])
        self.assertEqual(frame.attrs["filled_no_tick_timestamps"], [])

    def test_bounded_no_tick_slot_uses_previous_close(self):
        tf = analyzer.TIMEFRAME_MS["1h"]
        ready = 20 * tf
        source = pd.DataFrame([
            [ready - tf, 99, 100, 98, 99, 1],
            [ready, 100, 101, 99, 100, 1],
            [ready + 2 * tf, 102, 103, 101, 102, 1],
            [ready + 3 * tf, 103, 104, 102, 103, 1],
        ], columns=COLUMNS)
        frame = analyzer._build_exact_window(source, "1h", ready, 3)
        filled = ready + tf
        self.assertEqual(frame.attrs["filled_no_tick_timestamps"], [filled])
        repaired = frame[frame["time"] == filled].iloc[0]
        self.assertEqual(float(repaired["close"]), 100.0)
        self.assertEqual(float(repaired["volume"]), 0.0)

    def test_more_than_five_missing_slots_fails_closed(self):
        tf = analyzer.TIMEFRAME_MS["15m"]
        ready = 30 * tf
        source = pd.DataFrame([
            [ready - tf, 99, 100, 98, 99, 1],
            [ready, 100, 101, 99, 100, 1],
            [ready + 12 * tf, 100, 101, 99, 100, 1],
        ], columns=COLUMNS)
        with self.assertRaisesRegex(ValueError, "TOO_MANY_NO_TICK_SLOTS"):
            analyzer._build_exact_window(source, "15m", ready, 12)

    def test_horizon_matures_only_after_last_future_candle_closes(self):
        tf = analyzer.TIMEFRAME_MS["1h"]
        ready = 40 * tf
        self.assertNotIn(3, analyzer._mature_horizons(ready, "1h", ready + 4 * tf - 1))
        self.assertIn(3, analyzer._mature_horizons(ready, "1h", ready + 4 * tf))

    def test_event_accepts_exact_stored_4h_anchor(self):
        tf = analyzer.TIMEFRAME_MS["15m"]
        ready = 50 * tf
        expected_anchor = analyzer.context_4h_timestamp(ready, "15m")
        event = {
            "experiment_version": analyzer.EXPERIMENT_VERSION,
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "direction": "LONG",
            "ready_timestamp": ready,
            "market_context": {"target_4h_timestamp": expected_anchor},
        }
        parsed = analyzer._parse_event(event)
        self.assertEqual(parsed["market_context"]["target_4h_timestamp"], expected_anchor)

    def test_event_rejects_wrong_stored_4h_anchor(self):
        tf = analyzer.TIMEFRAME_MS["1h"]
        ready = 60 * tf
        event = {
            "experiment_version": analyzer.EXPERIMENT_VERSION,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "direction": "SHORT",
            "ready_timestamp": ready,
            "market_context": {"target_4h_timestamp": 0},
        }
        with self.assertRaisesRegex(ValueError, "WRONG_4H_CONTEXT_ANCHOR"):
            analyzer._parse_event(event)


if __name__ == "__main__":
    unittest.main()
