import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_entry_crash_filters as m
import rsi2_one_add_down as b
import connors_rsi2_research as base
def sample(n=310):
    return {s:{"open":[100.]*n,"close":[100.]*n,"rsi":[50.]*n,
               "sma200":[90.]*n,"sma5":[110.]*n,"volume":[100.]*n}
            for s in base.SYMBOLS}
class TestFilters(unittest.TestCase):
    def test_exact_baseline(self):
        d=sample()
        d["ETH/USDT"]["rsi"][251]=2
        d["ETH/USDT"]["open"][252]=100
        d["ETH/USDT"]["close"][252]=93
        d["ETH/USDT"]["open"][253]=92
        for cost in m.COSTS:
            x=b.replay(d,"add20",251,280,cost)
            y=m.replay(d,"baseline",251,280,cost)
            for z in ("net_return_pct","max_dd_pct","closed_trades","completed_additions"):
                self.assertEqual(x[z],y[z])
    def test_btc_crash_blocks(self):
        d=sample()
        d["BTC/USDT"]["close"][251]=94
        self.assertFalse(m.entry_filter(d,"ETH/USDT",251,"btc5"))
        self.assertTrue(m.entry_filter(d,"ETH/USDT",251,"coin5"))
    def test_coin_crash_blocks(self):
        d=sample()
        d["ETH/USDT"]["close"][251]=89
        self.assertFalse(m.entry_filter(d,"ETH/USDT",251,"coin5"))
    def test_volume_uses_previous_20_not_current(self):
        d=sample()
        a=d["ETH/USDT"]
        a["close"][251]=98
        a["volume"][251]=151
        self.assertFalse(m.entry_filter(d,"ETH/USDT",251,"volume"))
        a["volume"][251]=149
        self.assertTrue(m.entry_filter(d,"ETH/USDT",251,"volume"))
    def test_all_three_is_union(self):
        d=sample()
        d["BTC/USDT"]["close"][251]=94
        self.assertFalse(m.entry_filter(d,"ETH/USDT",251,"all3"))
    def test_future_data_not_used(self):
        d=sample()
        v=m.entry_filter(d,"ETH/USDT",251,"all3")
        d["ETH/USDT"]["volume"][252]=1e12
        d["BTC/USDT"]["close"][252]=1
        self.assertEqual(v,m.entry_filter(d,"ETH/USDT",251,"all3"))
    def test_no_live_access(self):
        src=(Path(__file__).resolve().parent/"rsi2_entry_crash_filters.py").read_text()
        for token in ("BYBIT_DEMO_API_KEY","DATABASE_URL","/v5/order/create","INSERT INTO"):
            self.assertNotIn(token,src)
if __name__=="__main__":unittest.main()
