"""Synthetic regression: lifetime stop-loss (including fees) never exceeds budget after averaging."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_exit_risk_fee_capped_300 as m

class LifetimeRiskTests(unittest.TestCase):
    def fixture(self):
        px=100.; stop=90.;qty=.3
        fee=px*qty*m.FEE
        p={"stop":stop,"qty":qty,"cost_basis":px*qty,
           "entry_fee":fee,"risk_cap":4.}
        return p
    def test_add_cannot_exceed_initial_risk_including_fees(self):
        p=self.fixture()
        before=m.worst_net_loss_at_stop(p)
        self.assertLess(before,4.)
        add=m.allowed_add_notional(p,95.,300.,0.,.06)
        self.assertGreater(add,0.)
        fee=add*m.FEE
        p["cost_basis"]+=add
        p["entry_fee"]+=fee
        p["qty"]+=add/95.
        self.assertLessEqual(m.worst_net_loss_at_stop(p),4.+1e-8)
        self.assertLessEqual(m.worst_net_loss_at_stop(p),18.+1e-8)
        self.assertLessEqual(m.allowed_add_notional(p,95.,300.,0.,.06),1e-8)
    def test_no_add_after_price_below_stop(self):
        p=self.fixture()
        self.assertEqual(m.allowed_add_notional(p,90.,300.,0.,.06),0.)
    def test_portfolio_budget_blocks_add(self):
        p=self.fixture()
        self.assertEqual(m.allowed_add_notional(p,95.,300.,17.,.06),0.)
    def test_fee_inclusive_initial_trade_sizing(self):
        px=100.;stop=90.;budget=3.
        per_notional=1.+m.FEE-(stop/px)*(1.-m.FEE)
        value=budget/per_notional
        p={"stop":stop,"qty":value/px,"cost_basis":value,
           "entry_fee":value*m.FEE,"risk_cap":budget}
        self.assertAlmostEqual(m.worst_net_loss_at_stop(p),budget,places=8)

if __name__=="__main__":unittest.main()
