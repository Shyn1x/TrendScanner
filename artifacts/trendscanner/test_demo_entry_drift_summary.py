"""Exclude identifiers and test harmful LONG direction attribution."""
import unittest
from demo_entry_drift_summary import summarise
class TestDrift(unittest.TestCase):
    def test_buckets_and_redaction(self):
        a=summarise([{"errors":[],"trades":[
            {"symbol":"SECRET","entry_delay_ms":245000,"entry_gap_from_signal_close_bps":"120"},
            {"symbol":"SECRET2","entry_delay_ms":100000,"entry_gap_from_signal_close_bps":"-60"}]}])
        self.assertEqual(a["gap_over_100bps_count"],1)
        self.assertEqual(a["gap_under_minus_50bps_count"],1)
        self.assertEqual(a["harmful_positive_gap_pct"],50)
        self.assertEqual(a["delay_over_4min_count"],1)
        self.assertNotIn("SECRET",str(a))
    def test_missing_reference_not_zero(self):
        a=summarise([{"trades":[{"entry_delay_ms":200000}]}])
        self.assertIsNone(a["reference_to_fill_bps_LONG"])
        self.assertIsNone(a["harmful_positive_gap_pct"])
if __name__=="__main__":unittest.main()
