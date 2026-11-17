"""Purely synthetic checks: five signal families, non-lookahead, ownership caps."""
import sys, unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import multi_strategy_batch_300 as batch

class MultiFamilyTests(unittest.TestCase):
    def setUp(self):
        self.original=base.SYMBOLS
        base.SYMBOLS=("BTC/USDT",)
    def tearDown(self):
        base.SYMBOLS=self.original
    def frame(self):
        n=170
        f=pd.DataFrame({"open":[100.]*n,"close":[100.]*n,
           "high":[100.5]*n,"low":[99.5]*n})
        return {"BTC/USDT":f}
    def test_all_signal_families_are_valid_and_aligned(self):
        frames=self.frame()
        for kind in ("breakout30","breakout60","ema_cross",
                     "pullback_reclaim","range_breakout"):
            signal=batch.make_signal_family(frames,kind)
            self.assertEqual(len(signal["BTC/USDT"]["rsi"]),170)
            self.assertEqual(signal["BTC/USDT"]["rsi"][0],50.)
    def test_breakout_previous_high_only(self):
        frames=self.frame()
        frames["BTC/USDT"].loc[120,["open","high","close"]]=[100.,160.,150.]
        signal=batch.make_signal_family(frames,"breakout60")["BTC/USDT"]["rsi"]
        self.assertLess(signal[120],1.)
        self.assertEqual(signal[119],50.)
    def test_invalid_policy_rejected(self):
        with self.assertRaises(ValueError):
            batch.make_signal_family(self.frame(),"lookahead")
        with self.assertRaises(ValueError):
            batch.run({}, {}, {},1,2,max_companion=4)
    def test_no_strategy_conflict_when_disabled(self):
        with self.assertRaises(ValueError):
            batch.run({}, {}, {},1,2,max_companion=-1)
if __name__=="__main__":unittest.main()
