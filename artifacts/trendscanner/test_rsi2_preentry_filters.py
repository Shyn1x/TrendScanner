import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_preentry_filters as m
import rsi2_one_add_down as old
import connors_rsi2_research as base

def sample(n=300):
    data={}
    vol={}
    for s in base.SYMBOLS:
        data[s]={"open":[100.]*n,"close":[100.]*n,"rsi":[50.]*n,
                 "sma200":[90.]*n,"sma5":[100.]*n}
        vol[s]=[100.]*n
    return data,vol

class Checks(unittest.TestCase):
    def test_masks_only_new_entry_rsi(self):
        d,v=sample()
        d["BTC/USDT"]["close"][255]=80
        d["ETH/USDT"]["rsi"][255]=1
        orig_rsi=d["ETH/USDT"]["rsi"].copy()
        modified,count=m.masked_data(d,v,"btc_trend")
        self.assertGreater(count,0)
        self.assertEqual(modified["ETH/USDT"]["rsi"][255],50)
        self.assertEqual(d["ETH/USDT"]["rsi"],orig_rsi)
        self.assertEqual(modified["BTC/USDT"]["close"],d["BTC/USDT"]["close"])
    def test_selloff_3days_gate(self):
        d,v=sample()
        d["ETH/USDT"]["close"][255]=90
        self.assertFalse(m.allowed_flags(d,v,"ETH/USDT",255)["selloff_3d"])
    def test_volume_uses_previous_20_days(self):
        d,v=sample()
        d["ETH/USDT"]["close"][255]=99
        v["ETH/USDT"][255]=250
        self.assertFalse(m.allowed_flags(d,v,"ETH/USDT",255)["volume_stress"])
        d["ETH/USDT"]["close"][255]=101
        self.assertTrue(m.allowed_flags(d,v,"ETH/USDT",255)["volume_stress"])
    def test_unchanged_exact_replay(self):
        d,v=sample()
        d["BTC/USDT"]["rsi"][251]=1
        for cost in m.FEES:
            modified,_=m.masked_data(d,v,"unchanged")
            self.assertEqual(old.replay(d,"add20",251,280,cost),
                             old.replay(modified,"add20",251,280,cost))
    def test_no_orders_and_secrets(self):
        content=(Path(__file__).resolve().parent/"rsi2_preentry_filters.py").read_text()
        for needle in ["BYBIT_DEMO_API_KEY","DATABASE_URL","/v5/order/create","INSERT INTO"]:
            self.assertNotIn(needle,content)
if __name__=="__main__":unittest.main()
