"""Fast synthetic regression: RSI2 2R/3R/partial exits and fixed add risk."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from unittest.mock import patch
import connors_rsi2_research as base
import rsi2_exit_risk_300 as model

class RiskRewardChecks(unittest.TestCase):
    def test_add_cannot_exceed_original_trade_budget(self):
        p={"stop":90.,"qty":1.,"risk_cap":12.}
        self.assertAlmostEqual(model.allowed_add_notional(p,95.,300.,0.,.06),133.)
        # Existing stop distance alone already consumes the budget.
        self.assertEqual(model.allowed_add_notional(p,103.,300.,0.,.06),0.)
        self.assertEqual(model.allowed_add_notional(p,90.,300.,0.,.06),0.)
    def test_other_positions_respect_account_cap(self):
        p={"stop":90.,"qty":1.,"risk_cap":12.}
        self.assertEqual(model.allowed_add_notional(p,95.,300.,16.,.06),0.)

    def test_targets_and_partial_use_actual_ohlc(self):
        n=270
        a={"open":[100.]*n,"close":[100.]*n,"low":[99.]*n,
           "high":[101.]*n,"exit_sma":[95.]*n,"rsi":[50.]*n}
        a["rsi"][251]=3.
        a["high"][253]=112.
        day={"close":[100.]*n,"sma200":[90.]*n,"atr14":[1.]*n}
        original=base.SYMBOLS
        try:
            base.SYMBOLS=("TEST/USDT",)
            with patch.object(model.diagnostic,"entry_filter",return_value=True):
                r2=model.simulate({"TEST/USDT":a},{"TEST/USDT":day},251,258,"1d",3,.01,
                                  add_enabled=False,exit_policy="2R")
                r3=model.simulate({"TEST/USDT":a},{"TEST/USDT":day},251,258,"1d",3,.01,
                                  add_enabled=False,exit_policy="3R")
                half=model.simulate({"TEST/USDT":a},{"TEST/USDT":day},251,258,"1d",3,.01,
                                    add_enabled=False,exit_policy="partial_1R_3R")
            self.assertEqual(r2["exit_reasons"].get("take_profit_2R"),1)
            self.assertEqual(r3["exit_reasons"].get("take_profit_3R"),1)
            self.assertEqual(half["exit_reasons"].get("partial_then_3R"),1)
            self.assertEqual(half["trade_ledger"][0]["partial_fills"],1)
            self.assertGreater(r3["net_return_pct"],half["net_return_pct"])
            self.assertAlmostEqual(half["net_return_pct"],r2["net_return_pct"],places=2)
        finally:
            base.SYMBOLS=original
if __name__=="__main__":unittest.main()
