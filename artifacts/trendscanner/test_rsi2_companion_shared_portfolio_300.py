"""Synthetic integration tests for a shared 4H cash book and symbol ownership."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import rsi2_companion_shared_portfolio_300 as model

class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.old=base.SYMBOLS
        base.SYMBOLS=("BTC/USDT","ETH/USDT")
        n=1560
        self.prices={s:{"open":[100.]*n,"high":[100.5]*n,
                        "low":[99.5]*n,"close":[100.]*n} for s in base.SYMBOLS}
        self.daily={s:{"open":[100.]*260,"high":[101.]*260,
                       "low":[99.]*260,"close":[100.]*260,
                       "sma200":[90.]*260,"rsi":[50.]*260,
                       "atr14":[1.]*260} for s in base.SYMBOLS}
        self.signals={s:{"rsi":[50.]*n} for s in base.SYMBOLS}
        self.filter=patch.object(model.diagnostic,"entry_filter",return_value=True)
        self.filter.start()
    def tearDown(self):
        self.filter.stop()
        base.SYMBOLS=self.old
    def test_rsi_priority_on_simultaneous_signals(self):
        self.daily["BTC/USDT"]["rsi"][250]=2.
        self.signals["BTC/USDT"]["rsi"][1505]=-.1
        result=model.run(self.prices,self.daily,self.signals,1504,1510)
        self.assertEqual(result["by_strategy"]["RSI2_1D"]["trades"],1)
        self.assertEqual(result["by_strategy"]["COMPANION_4H"]["trades"],0)
        self.assertTrue(all(x["owner"]=="RSI2_1D" for x in result["trade_ledger"]))
    def test_distinct_symbols_can_both_trade(self):
        self.daily["BTC/USDT"]["rsi"][250]=2.
        self.signals["ETH/USDT"]["rsi"][1505]=-.1
        result=model.run(self.prices,self.daily,self.signals,1504,1510)
        self.assertEqual(result["by_strategy"]["RSI2_1D"]["trades"],1)
        self.assertEqual(result["by_strategy"]["COMPANION_4H"]["trades"],1)
        self.assertLess(result["max_gross_equity_mult"],1.5)
        self.assertFalse(result["liquidated_proxy"])
    def test_no_signal_no_trade(self):
        r=model.run(self.prices,self.daily,self.signals,1504,1510)
        self.assertEqual(r["trades"],0)
        self.assertEqual(r["net_return_pct"],0.)
    def test_rsi_only_mode_never_accepts_breakout(self):
        self.signals["BTC/USDT"]["rsi"][1505]=-.1
        r=model.run(self.prices,self.daily,self.signals,1504,1510,
                    enable_rsi=True,enable_breakout=False)
        self.assertEqual(r["trades"],0)
    def test_breakout_only_mode_never_accepts_rsi(self):
        self.daily["BTC/USDT"]["rsi"][250]=2.
        r=model.run(self.prices,self.daily,self.signals,1504,1510,
                    enable_rsi=False,enable_breakout=True)
        self.assertEqual(r["trades"],0)

if __name__=="__main__":unittest.main()
