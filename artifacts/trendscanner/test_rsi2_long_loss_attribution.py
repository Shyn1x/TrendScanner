import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_long_loss_attribution as audit
import rsi2_one_add_down as old
import connors_rsi2_research as base

def synthetic(n=310):
    return {s:{"open":[100.]*n,"close":[100.]*n,
               "rsi":[50.]*n,"sma200":[80.]*n,"sma5":[100.]*n}
            for s in base.SYMBOLS}

class Check(unittest.TestCase):
    def test_flat_no_trades_parity(self):
        d=synthetic()
        expected=old.replay(d,"add20",251,300,.0008)
        got,trades=audit.analyze(d,[i*86400000 for i in range(310)],251,300,.0008)
        self.assertEqual(len(trades),0)
        self.assertEqual(expected["net_return_pct"],got["net_return_pct"])
    def test_decline_and_one_add_parity(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=1
        a["open"][252]=100
        a["close"][252]=93
        a["open"][253]=92
        a["close"][253]=90
        a["open"][254]=90
        a["close"][254]=95
        got,trades=audit.analyze(d,[i*86400000 for i in range(310)],251,300,.0008)
        self.assertEqual(got["completed_additions"],1)
        self.assertTrue(trades[0]["added"])
        self.assertLess(trades[0]["worst_daily_close_pct"],-5)
    def test_fee_stress_consistent(self):
        d=synthetic()
        a=d["BTC/USDT"]
        a["rsi"][251]=1
        low,_=audit.analyze(d,[i*86400000 for i in range(310)],251,270,.0008)
        high,_=audit.analyze(d,[i*86400000 for i in range(310)],251,270,.0016)
        self.assertLess(high["net_return_pct"],low["net_return_pct"])
    def test_diagnostic_summarization(self):
        r=audit.summarize([
            {"symbol":"BTC/USDT","exit_year":2025,"reason":"20d_limit","net":-9,"gross":2,"fee":11,"added":True,"first_day_pct":-3,"worst_daily_close_pct":-12},
            {"symbol":"ETH/USDT","exit_year":2026,"reason":"sma5_rebound","net":7,"gross":10,"fee":3,"added":False,"first_day_pct":2,"worst_daily_close_pct":0}])
        self.assertEqual(r["total"]["losers"],1)
        self.assertEqual(r["total"]["fees_turning_gross_win_to_net_loss"],1)
        self.assertEqual(r["exit_year"]["2026"]["net_pnl_usdt"],7)
    def test_no_live_api(self):
        source=(Path(__file__).resolve().parent/"rsi2_long_loss_attribution.py").read_text()
        for s in ("BYBIT_DEMO_API_KEY","DATABASE_URL","/v5/order/create","INSERT INTO"):
            self.assertNotIn(s,source)
if __name__=="__main__":unittest.main()
