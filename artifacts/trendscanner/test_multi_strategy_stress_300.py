"""Synthetic cost + isolated-symbol checks for batch stress reruns."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import multi_strategy_stress_300 as study

class StressTests(unittest.TestCase):
    def setUp(self):
        self.old=base.SYMBOLS
        base.SYMBOLS=("BTC/USDT","ETH/USDT")
        n=1550
        self.prices={s:{"open":[100.]*n,"high":[101.]*n,
                       "low":[99.]*n,"close":[100.]*n} for s in base.SYMBOLS}
        self.daily={s:{"close":[100.]*259,"rsi":[50.]*259,
                     "sma200":[90.]*259,"atr14":[2.]*259} for s in base.SYMBOLS}
        self.signals={s:{"rsi":[50.]*n} for s in base.SYMBOLS}
        self.patcher=patch.object(study.diagnostic,"entry_filter",return_value=True)
        self.patcher.start()
    def tearDown(self):
        self.patcher.stop()
        base.SYMBOLS=self.old
    def call(self,**kw):
        self.daily["BTC/USDT"]["rsi"][250]=2.
        return study.run(self.prices,self.daily,self.signals,1504,1514,
                         enable_breakout=False,**kw)
    def test_filter_excludes_signal(self):
        self.assertEqual(self.call(allowed_symbols=("ETH/USDT",))["trades"],0)
        self.assertEqual(self.call(allowed_symbols=("BTC/USDT",))["trades"],1)
    def test_cost_stress_cannot_improve_flat_price_pnl(self):
        low=self.call(all_in_cost_per_side=.0016)
        high=self.call(all_in_cost_per_side=.0048)
        self.assertLess(high["net_return_pct"],low["net_return_pct"])
    def test_reject_invalid_cost_and_universe(self):
        with self.assertRaises(ValueError):
            self.call(all_in_cost_per_side=-.001)
        with self.assertRaises(ValueError):
            self.call(allowed_symbols=("NOT_REAL/USDT",))
    def test_strategy_caps_unchanged(self):
        a=self.call()
        self.assertLessEqual(a["selection"]["accepted_rsi"],1)
        self.assertFalse(a["liquidated_proxy"])
if __name__=="__main__": unittest.main()
