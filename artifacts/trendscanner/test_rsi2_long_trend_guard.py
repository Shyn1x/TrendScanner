import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_long_trend_guard as m
import rsi2_one_add_down as base_add
import connors_rsi2_research as base
def sample():
    return {s:{"open":[100.]*310,"close":[100.]*310,
               "rsi":[50.]*310,"sma200":[80.]*310,"sma5":[110.]*310} for s in base.SYMBOLS}
class Guard(unittest.TestCase):
    def test_parity_original(self):
        d=sample()
        d["BTC/USDT"]["rsi"][251]=1
        d["BTC/USDT"]["close"][252]=90
        for f in (.0008,.0016):
            ref=base_add.replay(d,"add20",251,275,f)
            copy=m.replay(d,"add20",251,275,f)
            self.assertEqual(ref["net_return_pct"],copy["net_return_pct"])
            self.assertEqual(ref["completed_additions"],copy["completed_additions"])
    def test_sma200_guard_blocks_add(self):
        d=sample()
        a=d["BTC/USDT"]
        a["rsi"][251]=1
        a["open"][252]=100
        a["close"][252]=90
        a["sma200"][252]=95
        a["open"][253]=89
        ref=m.replay(d,"add20",251,275,.0008)
        guarded=m.replay(d,"guarded_add20",251,275,.0008)
        self.assertEqual(ref["completed_additions"],1)
        self.assertEqual(guarded["completed_additions"],0)
    def test_guard_permits_add_above_trend(self):
        d=sample()
        a=d["BTC/USDT"]
        a["rsi"][251]=1
        a["open"][252]=100
        a["close"][252]=90
        a["sma200"][252]=80
        a["open"][253]=89
        guarded=m.replay(d,"guarded_add20",251,275,.0008)
        self.assertEqual(guarded["completed_additions"],1)
    def test_no_exchange_access(self):
        src=(Path(__file__).resolve().parent/"rsi2_long_trend_guard.py").read_text()
        for key in ("BYBIT_DEMO_API_KEY","DATABASE_URL","/v5/order/create","INSERT INTO"):
            self.assertNotIn(key,src)
if __name__=="__main__":unittest.main()
