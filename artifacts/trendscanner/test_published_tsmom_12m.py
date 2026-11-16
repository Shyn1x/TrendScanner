"""Timing, cost, gross cap and benchmark tests for published TSMOM adaptation."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import published_tsmom_12m as m

def synthetic(up=True):
    n=950
    d={}
    for sym in m.SYMBOLS:
        prices=[100*(1.001 if up else 0.999)**k for k in range(n)]
        d[sym]={"time":[k*86400000 for k in range(n)],
                "open":prices[:],"close":prices[:]}
    return d

class TSMOMTests(unittest.TestCase):
    def test_sign_and_long_only_cash(self):
        d=synthetic(False)
        s=m.weights_at_previous_close(d,500,"tsmom_long_short")
        l=m.weights_at_previous_close(d,500,"tsmom_long_only")
        self.assertTrue(all(w<0 for w in s.values()))
        self.assertTrue(all(w==0 for w in l.values()))
    def test_gross_and_symbol_caps(self):
        d=synthetic(True)
        w=m.weights_at_previous_close(d,500,"tsmom_long_short")
        self.assertLessEqual(sum(abs(x) for x in w.values()),m.GROSS_CAP+1e-10)
        self.assertLessEqual(max(abs(x) for x in w.values()),m.ASSET_CAP+1e-10)
    def test_uses_only_prior_close(self):
        d=synthetic(True)
        prior=m.weights_at_previous_close(d,500,"tsmom_long_short")
        for a in d.values():
            a["close"][501]*=100
        self.assertEqual(prior,m.weights_at_previous_close(d,500,"tsmom_long_short"))
    def test_annualization_and_cost_reduce_returns(self):
        d=synthetic(True)
        original=m.COST_SIDE
        try:
            m.COST_SIDE=0.
            free=m.replay(d,"passive_long",430,600)
            m.COST_SIDE=.0008
            charged=m.replay(d,"passive_long",430,600)
            self.assertLess(charged["net_return_pct"],free["net_return_pct"])
            self.assertEqual(charged["rebalances"],6)
        finally:
            m.COST_SIDE=original
    def test_research_only_no_credentials(self):
        code=(Path(__file__).resolve().parent/"published_tsmom_12m.py").read_text()
        for forbidden in ("BYBIT_DEMO_API_SECRET","DATABASE_URL","order/create","INSERT INTO"):
            self.assertNotIn(forbidden,code)

if __name__=="__main__":unittest.main()
