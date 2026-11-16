import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_long_leverage_sensitivity as m
import rsi2_volume_causal_regimes as ref
import connors_rsi2_research as base

def sample(n=320):
    d={s:{"open":[100.]*n,"close":[100.]*n,
        "low":[99.]*n,"rsi":[50.]*n,"sma200":[80.]*n,
        "sma5":[110.]*n,"volume":[100.]*n}
       for s in base.SYMBOLS}
    d["_regime"]=ref.calc_btc_regime(d)
    return d

class TestLeverage(unittest.TestCase):
    def test_unlevered_baseline_parity(self):
        d=sample()
        a=d["ETH/USDT"]
        a["rsi"][251]=2
        a["open"][252]=100
        a["close"][252]=94
        a["low"][252]=92
        a["open"][253]=93
        a["low"][253]=91
        for cost in (.0008,.0016):
            ref_result=ref.replay(d,"volume_btc_high_vol",251,280,cost)
            got=m.replay(d,"volume_btc_high_vol",251,280,cost,1,0)
            for field in ("net_return_pct","max_dd_pct","closed_trades","completed_additions"):
                self.assertEqual(ref_result[field],got[field])
    def test_magnified_loss_and_fees(self):
        d=sample()
        a=d["ETH/USDT"]
        a["rsi"][251]=2
        a["close"][252]=96
        a["low"][252]=95
        results=[m.replay(d,"volume_btc_high_vol",251,269,.0008,lev,0) for lev in (1,5,10)]
        self.assertTrue(all(not r["liquidation_idx"] for r in results))
        self.assertTrue(results[0]["net_return_pct"]>results[1]["net_return_pct"]>results[2]["net_return_pct"])
    def test_synthetic_daily_low_liquidation(self):
        d=sample()
        a=d["ETH/USDT"]
        a["rsi"][251]=2
        a["close"][252]=100
        a["low"][252]=1
        r=m.replay(d,"volume_btc_high_vol",251,280,.0008,10,0)
        self.assertEqual(r["liquidation_idx"],252)
        self.assertEqual(r["net_return_pct"],-100)
    def test_funding_drag_no_positive_cash_yield(self):
        d=sample()
        a=d["ETH/USDT"]
        a["rsi"][251]=2
        a["sma5"]=[130.]*320
        low=m.replay(d,"volume_btc_high_vol",251,270,.0008,1,0)
        high=m.replay(d,"volume_btc_high_vol",251,270,.0008,1,.0001)
        self.assertLess(high["net_return_pct"],low["net_return_pct"])
    def test_no_live_access(self):
        src=(Path(__file__).resolve().parent/"rsi2_long_leverage_sensitivity.py").read_text()
        for word in ("DATABASE_URL","BYBIT_DEMO_API_KEY","/v5/order/create","INSERT INTO"):
            self.assertNotIn(word,src)
if __name__=="__main__":unittest.main()
