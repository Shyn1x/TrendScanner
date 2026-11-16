"""No lookahead, bounded regime selection, exact base/volume accounting."""
import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_volume_causal_regimes as r
import rsi2_volume_calendar_stress as p
import connors_rsi2_research as base

def artificial(n=320):
    d={s:{"open":[100.]*n,"close":[100.]*n,"volume":[100.]*n,
          "rsi":[50.]*n,"sma200":[90.]*n,"sma5":[110.]*n}
       for s in base.SYMBOLS}
    d["_regime"]=r.calc_btc_regime(d)
    return d

class Tests(unittest.TestCase):
    def test_regime_uses_only_completed_data(self):
        d=artificial()
        d["BTC/USDT"]["close"][251]=60
        prior=r.calc_btc_regime(d)
        d["BTC/USDT"]["close"][252]=10000
        after=r.calc_btc_regime(d)
        self.assertEqual(prior["below_sma50"][251],after["below_sma50"][251])
        self.assertEqual(prior["high_vol"][251],after["high_vol"][251])
        self.assertTrue(prior["below_sma50"][251])
    def test_gate_on_vs_gate_off(self):
        d=artificial()
        d["ETH/USDT"]["close"][251]=99
        d["ETH/USDT"]["volume"][251]=200
        d["_regime"]["below_sma50"][251]=True
        d["_regime"]["high_vol"][251]=False
        self.assertFalse(r.entry_filter(d,"ETH/USDT",251,"volume_btc_below_sma50"))
        self.assertTrue(r.entry_filter(d,"ETH/USDT",251,"volume_btc_high_vol"))
        self.assertFalse(r.entry_filter(d,"ETH/USDT",251,"volume"))
    def test_original_and_volume_parity(self):
        d=artificial()
        a=d["ETH/USDT"]
        a["rsi"][251]=2
        a["close"][251]=99
        a["volume"][251]=200
        for fee in r.COSTS:
            for mode in ("baseline","volume"):
                a=p.replay(d,mode,251,290,fee)
                b=r.replay(d,mode,251,290,fee)
                for key in ("net_return_pct","max_dd_pct","closed_trades","completed_additions"):
                    self.assertEqual(a[key],b[key])
    def test_no_secrets_or_order_access(self):
        src=(Path(__file__).resolve().parent/"rsi2_volume_causal_regimes.py").read_text()
        for forbidden in ("DATABASE_URL","BYBIT_DEMO_API_KEY","/v5/order/create","INSERT INTO"):
            self.assertNotIn(forbidden,src)
if __name__=="__main__":unittest.main()
