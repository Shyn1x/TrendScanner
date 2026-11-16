import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as m

def synthetic():
    n=310
    d={}
    for s in m.SYMBOLS:
        d[s]={"open":[100.]*n,"close":[100.]*n,
              "rsi":[50.]*n,"sma200":[90.]*n,"sma5":[100.]*n}
    return d

class Check(unittest.TestCase):
    def test_rsi5_rsi10_separation(self):
        d=synthetic()
        d["BTC/USDT"]["rsi"][255]=7
        self.assertEqual(m.entry_candidates(d,255,"rsi5",{}),[])
        self.assertEqual(m.entry_candidates(d,255,"rsi10",{}),["BTC/USDT"])
    def test_entry_next_open_and_next_day_exit(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=2
        a["open"][252]=102
        a["close"][252]=104
        a["open"][253]=105
        a["close"][253]=105
        a["sma5"][252]=101
        r=m.replay(d,"rsi5",251,255)
        self.assertEqual(r["trades_closed"],1)
        self.assertEqual(r["median_holding_days"],1)
    def test_no_current_day_entry(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=2
        x=m.replay(d,"rsi5",251,252)
        self.assertEqual(x["trades_closed"],0)
    def test_flat_market_costs_are_negative(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=2
        r=m.replay(d,"rsi5",251,260)
        self.assertLess(r["return_pct"],0)
    def test_no_credential_or_order_operations(self):
        src=(Path(__file__).resolve().parent/"connors_rsi2_research.py").read_text()
        for forbidden in ("DATABASE_URL","BYBIT_DEMO_API_KEY","/v5/order/create","INSERT INTO"):
            self.assertNotIn(forbidden,src)
if __name__=="__main__":unittest.main()
