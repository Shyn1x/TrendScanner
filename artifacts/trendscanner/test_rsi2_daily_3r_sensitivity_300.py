"""Fast local checks before any slow historical RSI2 market-data download."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import rsi2_daily_3r_sensitivity_300 as study

class Sensitivity(unittest.TestCase):
    def setUp(self):
        self.old=base.SYMBOLS
        base.SYMBOLS=("TEST/USDT",)
        n=290
        self.d={"TEST/USDT":{"close":[100.]*n,"sma200":[90.]*n,"atr14":[1.]*n}}
        self.p={"TEST/USDT":{"open":[100.]*n,"close":[100.]*n,
                  "low":[99.]*n,"high":[101.]*n,
                  "exit_sma":[95.]*n,"rsi":[50.]*n}}
        self.p["TEST/USDT"]["rsi"][251]=6.
        self.p["TEST/USDT"]["high"][253]=112.
        self.filter=patch.object(study.diagnostic,"entry_filter",return_value=True)
        self.filter.start()
    def tearDown(self):
        self.filter.stop()
        base.SYMBOLS=self.old
    def run_case(self,**args):
        return study.simulate(self.p,self.d,251,260,"1d",3,.01,
                              add_enabled=False,exit_policy="3R",**args)
    def test_stricter_rsi_excludes_signal(self):
        self.assertEqual(self.run_case(entry_rsi=5.)["trades"],0)
        self.assertEqual(self.run_case(entry_rsi=8.)["exit_reasons"].get("take_profit_3R"),1)
    def test_atr_moves_target(self):
        narrow=self.run_case(entry_rsi=8.,stop_atr=2.)
        wide=self.run_case(entry_rsi=8.,stop_atr=5.)
        self.assertEqual(narrow["exit_reasons"].get("take_profit_3R"),1)
        self.assertNotIn("take_profit_3R",wide["exit_reasons"])
    def test_hold_closes_when_target_absent(self):
        self.p["TEST/USDT"]["high"][253]=101.
        result=self.run_case(entry_rsi=8.,hold_days=3)
        self.assertEqual(result["exit_reasons"].get("signal_exit"),1)

if __name__=="__main__":unittest.main()
