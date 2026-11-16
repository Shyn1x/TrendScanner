"""Isolated deterministic checks of next-day add, sizing, and 10-day parity."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_one_add_down as m
import connors_rsi2_research as base

def synthetic(n=300):
    return {sym:{"open":[100.]*n,"close":[100.]*n,
                 "rsi":[50.]*n,"sma200":[80.]*n,"sma5":[100.]*n}
            for sym in base.SYMBOLS}

class Tests(unittest.TestCase):
    def test_baseline_exact_model_parity(self):
        d=synthetic()
        a=d["BTC/USDT"];a["rsi"][251]=1
        a["open"][252]=98;a["close"][252]=95
        a["open"][253]=95;a["close"][253]=96
        a["open"][254]=97;a["close"][254]=97
        old=base.replay(d,"rsi5",251,270)
        new=m.replay(d,"base10",251,270,.0008)
        self.assertAlmostEqual(old["return_pct"],new["net_return_pct"],places=2)
        self.assertEqual(old["trades_closed"],new["closed_trades"])
    def test_one_add_only_after_close_not_on_same_day(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=1
        a["open"][252]=100
        a["close"][252]=94
        a["open"][253]=93
        a["close"][253]=90
        a["open"][254]=86
        a["close"][254]=80
        # Even if it continues to fall, only one addition permitted.
        r=m.replay(d,"add20",251,259,.0008)
        self.assertEqual(r["completed_additions"],1)
        self.assertEqual(r["new_entries"],1)
    def test_skip_add_when_next_open_rebounds(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=1;a["close"][252]=93
        a["open"][253]=99;a["close"][253]=101
        r=m.replay(d,"add20",251,259,.0008)
        self.assertEqual(r["completed_additions"],0)
        self.assertGreaterEqual(r["blocked_additions"]["gap_rebound_or_already_added"],1)
    def test_add_never_resets_holding_limit(self):
        d=synthetic(310)
        a=d["BTC/USDT"]
        a["rsi"][251]=1
        for i in range(252,285):
            a["open"][i]=90
            a["close"][i]=90
            a["sma5"][i]=110
        r=m.replay(d,"add20",251,300,.0008)
        self.assertEqual(r["completed_additions"],1)
        self.assertEqual(r["median_holding_days"],20)
    def test_cost_stress_cannot_create_profit_for_flat_trade(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=1
        low=m.replay(d,"base10",251,267,.0008)
        high=m.replay(d,"base10",251,267,.0016)
        self.assertLess(high["net_return_pct"],low["net_return_pct"])
    def test_no_orders_and_secrets(self):
        src=(Path(__file__).resolve().parent/"rsi2_one_add_down.py").read_text()
        for x in ["BYBIT_DEMO_API_KEY","DATABASE_URL","order/create","INSERT INTO"]:
            self.assertNotIn(x,src)

if __name__=="__main__":unittest.main()
