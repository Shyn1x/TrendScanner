"""Preflight for no-lookahead RSI2 early slot release and frozen universe."""
import sys,json,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from rsi2_frozen_early_release_300 import early_release_due
class FrozenEarlyRelease(unittest.TestCase):
    def test_snapshot_uniqueness_and_count(self):
        a=json.loads((Path(__file__).parent/"rsi2_frozen_universe_20261117.json").read_text())
        self.assertEqual(len(a["symbols"]),100)
        self.assertEqual(len(set(a["symbols"])),100)
        self.assertEqual(a["boundary_utc"],"2026-11-17T00:00:00Z")
    def test_only_completed_bar_for_stale_exit(self):
        p={"opened":5,"first":100.}
        self.assertFalse(early_release_due(p,99.,8,1,20,5))
        self.assertTrue(early_release_due(p,99.,9,1,20,5))
        self.assertFalse(early_release_due(p,101.,9,1,20,5))
        self.assertTrue(early_release_due(p,101.,24,1,20,5))
    def test_hold10_vs_hold20(self):
        p={"opened":100,"first":100.}
        self.assertTrue(early_release_due(p,101.,109,1,10,None))
        self.assertFalse(early_release_due(p,101.,109,1,20,None))
    def test_4h_units(self):
        p={"opened":100,"first":100.}
        self.assertFalse(early_release_due(p,95.,128,6,20,5))
        self.assertTrue(early_release_due(p,95.,129,6,20,5))
if __name__=="__main__":unittest.main()
