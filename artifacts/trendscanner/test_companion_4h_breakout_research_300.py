"""Breakout entry must only use completed bars and prior highs."""
import sys, unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import companion_4h_breakout_research_300 as study
class BreakoutSignalTest(unittest.TestCase):
    def setUp(self):
        self.old=base.SYMBOLS
        base.SYMBOLS=("TEST/USDT",)
    def tearDown(self):
        base.SYMBOLS=self.old
    def test_breakout_event_and_prior_candles(self):
        n=100
        f=pd.DataFrame({"open":[100.]*n,"close":[100.]*n,
                        "high":[101.]*n,"low":[99.]*n})
        f.loc[70,["open","high","close"]]=[100.,105.,104.]
        f.loc[71,["open","high","close"]]=[104.,106.,105.5]
        b=study.build_4h({"TEST/USDT":f},{},30)["TEST/USDT"]
        self.assertEqual(b["rsi"][69],50.)
        self.assertLess(b["rsi"][70],0.)
        # New highs without an intervening reset do not repeatedly re-enter.
        self.assertEqual(b["rsi"][71],50.)
    def test_current_high_not_used_in_its_own_breakout_threshold(self):
        n=90
        f=pd.DataFrame({"open":[100.]*n,"close":[100.]*n,
                        "high":[101.]*n,"low":[99.]*n})
        f.loc[50,["open","high","close"]]=[100.,200.,150.]
        b=study.build_4h({"TEST/USDT":f},{},30)["TEST/USDT"]
        self.assertLess(b["rsi"][50],0.)
        self.assertEqual(b["rsi"][51],50.)
    def test_still_ignores_uncompleted_warmup(self):
        n=100
        f=pd.DataFrame({"open":[100.]*n,"close":[100.]*n,
                        "high":[101.]*n,"low":[99.]*n})
        f.loc[20,["open","high","close"]]=[100.,110.,108.]
        b=study.build_4h({"TEST/USDT":f},{},30)["TEST/USDT"]
        self.assertEqual(b["rsi"][20],50.)
if __name__=="__main__": unittest.main()
