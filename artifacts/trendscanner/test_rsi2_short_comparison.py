"""RSI2 SHORT model invariants: direction gates, fee and no future leakage."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_short_comparison as m
import rsi2_one_add_down as old
import connors_rsi2_research as base

def synthetic(n=300):
    return {s:{"open":[100.]*n,"close":[100.]*n,
               "rsi":[50.]*n,"sma200":[110.]*n,"sma5":[100.]*n}
            for s in base.SYMBOLS}

class Check(unittest.TestCase):
    def test_short_95_and_sma200_gate(self):
        d=synthetic()
        d["BTC/USDT"]["rsi"][251]=96
        self.assertIn(("BTC/USDT",-1),m.direction_candidates(d,251,{},"short10"))
        self.assertNotIn(("BTC/USDT",-1),m.direction_candidates(d,251,{},"long_add20"))
        d["BTC/USDT"]["sma200"][251]=90
        self.assertNotIn(("BTC/USDT",-1),m.direction_candidates(d,251,{},"short10"))
    def test_short_price_down_profitable_and_entry_next_open(self):
        d=synthetic()
        a=d["BTC/USDT"];a["rsi"][251]=99
        a["open"][252]=110;a["close"][252]=100
        a["open"][253]=95;a["close"][253]=95
        a["sma5"][252]=105
        r=m.replay(d,"short10",251,255,.0008)
        self.assertEqual(r["closed_trades"],1)
        self.assertEqual(r["short_closed"],1)
        self.assertGreater(r["return_pct"],0)
    def test_short_rising_price_loses(self):
        d=synthetic()
        a=d["BTC/USDT"];a["rsi"][251]=99
        a["open"][252]=100;a["close"][252]=110
        for i in range(253,265):
            a["open"][i]=110;a["close"][i]=110;a["sma5"][i]=105
        r=m.replay(d,"short10",251,266,.0008)
        self.assertLess(r["return_pct"],0)
        self.assertEqual(r["one_time_long_adds"],0)
    def test_baseline_long_parity(self):
        d=synthetic()
        for a in d.values():
            a["sma200"]=[90.]*300
        a=d["BTC/USDT"];a["rsi"][251]=1
        a["open"][252]=100;a["close"][252]=94
        a["open"][253]=93;a["close"][253]=90
        a["open"][254]=92;a["close"][254]=95
        prev=old.replay(d,"add20",251,275,.0008)
        new=m.replay(d,"long_add20",251,275,.0008)
        self.assertAlmostEqual(prev["net_return_pct"],new["return_pct"],places=2)
        self.assertEqual(prev["completed_additions"],new["one_time_long_adds"])
    def test_current_close_not_usable_until_next_day(self):
        d=synthetic()
        d["BTC/USDT"]["rsi"][251]=99
        r=m.replay(d,"short10",251,252,.0008)
        self.assertEqual(r["short_closed"],0)
    def test_no_order_or_credentials(self):
        s=(Path(__file__).resolve().parent/"rsi2_short_comparison.py").read_text()
        for forbidden in ("BYBIT_DEMO_API_KEY","DATABASE_URL","/v5/order/create","INSERT INTO"):
            self.assertNotIn(forbidden,s)
if __name__=="__main__":unittest.main()
