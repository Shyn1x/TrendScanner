"""Aggregate report must not leak identifiers or per-trade fills."""
import unittest
from summarize_demo_fees import summarize

class FeeAuditTest(unittest.TestCase):
    def test_aggregate_fees_and_private_fields_not_emitted(self):
        r={"read_only":True,"comparison_status":"EVIDENCE_COLLECTED",
           "journal_trades":3,"journal_status_counts":{"CLOSED":3},
           "journal_failure_codes":{},"eligible_detail_trades":3,
           "attempted_detail_trades":1,"matched_closed_trades":1,
           "errors":[],"trades":[{
             "symbol":"BTC/USDT","order_id":"SECRET",
             "entry_fills":{"quantity":"2","average_price":"100","first_ms":9},
             "exit_fills":{"quantity":"2","average_price":"101","first_ms":13},
             "entry_fees_usdt":"0.12","exit_fees_usdt":"0.1212",
             "funding_cashflow_usdt":"-.01","entry_delay_ms":1200,"issues":[]}]}
        x=summarize(r)
        self.assertAlmostEqual(x["mean_entry_fee_bps"],6)
        self.assertAlmostEqual(x["mean_exit_fee_bps"],6)
        self.assertEqual(x["entry_delay_median_seconds"],1.2)
        self.assertNotIn("SECRET",str(x))
        self.assertNotIn("BTC/USDT",str(x))
    def test_missing_fee_not_zero(self):
        x=summarize({"trades":[{"issues":["ENTRY_FEES_MISSING_OR_NON_USDT"]}]})
        self.assertIsNone(x["mean_entry_fee_bps"])
        self.assertEqual(x["issues_counts"]["ENTRY_FEES_MISSING_OR_NON_USDT"],1)
if __name__=="__main__":unittest.main()
