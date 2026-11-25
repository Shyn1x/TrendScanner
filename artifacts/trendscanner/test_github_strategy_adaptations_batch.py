"""Research-only synthetic tests for externally inspired strategy entry groups."""
import sys,unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import github_strategy_adaptations_batch_300 as study
class TestAdaptations(unittest.TestCase):
    def setUp(self):
        self.old=base.SYMBOLS
        base.SYMBOLS=("BTC/USDT",)
    def tearDown(self):
        base.SYMBOLS=self.old
    def test_10_source_inspired_families_return_causal_length(self):
        n=650
        f=pd.DataFrame({"open":[100.]*n,"close":[100.]*n,
             "high":[101.]*n,"low":[99.]*n})
        for name in ("tr_pullback","tr_ema50_bounce","tr_rsi_bounce",
                     "tr_ema_cross","tr_bb_bounce","tr_macd_cross",
                     "ft_rsi_revert","ft_macd_bull","ft_bb_revert","ft_ema_trend"):
            sig=study.external_source_signals({"BTC/USDT":f},name)
            self.assertEqual(len(sig["BTC/USDT"]["rsi"]),n)
            self.assertEqual(sig["BTC/USDT"]["rsi"][0],50.)
    def test_invalid_family_raises(self):
        with self.assertRaises(ValueError):
            study.external_source_signals({},"unknown")
if __name__=="__main__":unittest.main()
