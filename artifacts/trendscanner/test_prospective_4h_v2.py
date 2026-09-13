import unittest

import pandas as pd

from prospective_4h_v2_collector import (
    CLOSED_BARS,
    EXPECTED_BARS,
    TIMEFRAME_MS,
    _build_window_for_target,
    _targets_to_process,
)


class Prospective4hV2Tests(unittest.TestCase):
    def test_first_run_uses_latest_target(self):
        latest = 100 * TIMEFRAME_MS
        self.assertEqual(_targets_to_process(None, latest), [latest])

    def test_up_to_date_returns_no_targets(self):
        latest = 100 * TIMEFRAME_MS
        self.assertEqual(_targets_to_process(latest, latest), [])

    def test_gap_is_replayed_sequentially(self):
        previous = 90 * TIMEFRAME_MS
        latest = 94 * TIMEFRAME_MS
        self.assertEqual(
            _targets_to_process(previous, latest),
            [91 * TIMEFRAME_MS, 92 * TIMEFRAME_MS, 93 * TIMEFRAME_MS, 94 * TIMEFRAME_MS],
        )

    def test_catchup_is_bounded(self):
        previous = 1 * TIMEFRAME_MS
        latest = 40 * TIMEFRAME_MS
        targets = _targets_to_process(previous, latest, max_catchup=3)
        self.assertEqual(targets, [2 * TIMEFRAME_MS, 3 * TIMEFRAME_MS, 4 * TIMEFRAME_MS])

    def test_historical_window_has_signal_at_minus_two_and_placeholder(self):
        target = 500 * TIMEFRAME_MS
        first = target - (CLOSED_BARS - 1) * TIMEFRAME_MS
        rows = []
        for index, timestamp in enumerate(range(first, target + TIMEFRAME_MS, TIMEFRAME_MS)):
            value = 100.0 + index
            rows.append([timestamp, value, value + 1, value - 1, value + 0.5, 10.0])
        source = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])

        frame = _build_window_for_target(source, target)

        self.assertEqual(len(frame), EXPECTED_BARS)
        self.assertEqual(int(frame.iloc[-2]["time"]), target)
        self.assertEqual(int(frame.iloc[-1]["time"]), target + TIMEFRAME_MS)
        self.assertEqual(frame.iloc[-1]["close"], frame.iloc[-2]["close"])
        self.assertEqual(frame.iloc[-1]["volume"], 0.0)

    def test_noncontiguous_source_fails_closed(self):
        target = 500 * TIMEFRAME_MS
        first = target - (CLOSED_BARS - 1) * TIMEFRAME_MS
        timestamps = list(range(first, target + TIMEFRAME_MS, TIMEFRAME_MS))
        del timestamps[-5]
        rows = [[ts, 100.0, 101.0, 99.0, 100.5, 10.0] for ts in timestamps]
        source = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        frame = _build_window_for_target(source, target)
        self.assertTrue(frame.empty)


if __name__ == "__main__":
    unittest.main()
