import unittest

from prospective_collector import (
    EXPECTED_EVALUATIONS,
    _bad_shadow_statuses,
    _expected_closed_timestamp,
)
from prospective_coverage import CoverageError, TIMEFRAME_MS, _validate_timestamp


class ProspectiveCollectorIntegrityTests(unittest.TestCase):
    def test_expected_population_is_frozen(self):
        self.assertEqual(EXPECTED_EVALUATIONS, 50)

    def test_expected_closed_timestamp_uses_previous_4h_candle(self):
        boundary = 1_789_070_400_000  # 2026-09-10 20:00 UTC
        now = boundary + 60 * 60 * 1000
        self.assertEqual(_expected_closed_timestamp(now), boundary - TIMEFRAME_MS)

    def test_unavailable_and_unknown_are_failures(self):
        bad = _bad_shadow_statuses({"NOT_READY": 48, "UNAVAILABLE": 1, "UNKNOWN": 1})
        self.assertEqual(bad, {"UNAVAILABLE": 1, "UNKNOWN": 1})

    def test_safe_shadow_statuses_are_accepted(self):
        self.assertEqual(
            _bad_shadow_statuses({"NOT_READY": 48, "NO_TRANSITION": 1, "OK": 1}),
            {},
        )

    def test_coverage_timestamp_must_be_aligned_integer(self):
        aligned = 1_789_056_000_000
        self.assertEqual(_validate_timestamp(aligned), aligned)
        with self.assertRaises(CoverageError):
            _validate_timestamp(True)
        with self.assertRaises(CoverageError):
            _validate_timestamp(aligned + 1)


if __name__ == "__main__":
    unittest.main()
