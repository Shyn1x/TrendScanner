"""Regression for 4H RSI2 portfolio stop-risk OHLC indexing (KeyError: 311)."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from rsi2_4h_vs_daily_300 import open_stop_risk

class CrossRiskTests(unittest.TestCase):
    def test_4h_open_risk_uses_open_series(self):
        prices={"BTC/USDT":{"open":[100.0,105.0,110.0]},
                "ETH/USDT":{"open":[50.0,45.0,40.0]}}
        positions={"BTC/USDT":{"qty":2.0,"stop":90.0},
                   "ETH/USDT":{"qty":1.0,"stop":42.0}}
        self.assertAlmostEqual(open_stop_risk(positions,prices,1),33.0)
    def test_empty_positions(self):
        self.assertEqual(open_stop_risk({},{"BTC/USDT":{"open":[100]}},0),0)
    def test_at_bar_311(self):
        prices={"BTC/USDT":{"open":[100.0]*312}}
        positions={"BTC/USDT":{"qty":0.2,"stop":90.0}}
        self.assertAlmostEqual(open_stop_risk(positions,prices,311),2.0)

if __name__=="__main__":
    unittest.main()
