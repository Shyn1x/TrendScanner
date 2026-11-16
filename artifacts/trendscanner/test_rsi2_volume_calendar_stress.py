import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_volume_calendar_stress as s
import rsi2_entry_crash_filters as orig
import connors_rsi2_research as base

def mock(n=310):
    return {k:{"open":[100.]*n,"close":[100.]*n,"volume":[100.]*n,
       "rsi":[50.]*n,"sma200":[90.]*n,"sma5":[110.]*n} for k in base.SYMBOLS}

class Tests(unittest.TestCase):
    def test_parity_of_both_modes(self):
        d=mock()
        d["ETH/USDT"]["rsi"][251]=2
        d["ETH/USDT"]["volume"][251]=180
        d["ETH/USDT"]["close"][251]=99
        times=[i*86400000 for i in range(310)]
        for cost in s.COSTS:
            for mode in s.MODES:
                a=orig.replay(d,mode,251,290,cost)
                b=s.replay(d,mode,251,290,cost)
                self.assertEqual(a["net_return_pct"],b["net_return_pct"])
                self.assertEqual(a["completed_additions"],b["completed_additions"])
                self.assertEqual(b["days"],len(b["daily_equity_unliquidated"]))
    def test_equity_year_pieces_multiply_to_full_return(self):
        from datetime import datetime,timezone,timedelta
        day=datetime(2024,12,29,tzinfo=timezone.utc)
        times=[int((day+timedelta(days=j)).timestamp()*1000) for j in range(6)]
        y=s.yearly_stats([10100,10200,10400,10500,10700,11000],times,0)
        self.assertEqual(set(y),{"2024","2025"})
        gross=1.
        for row in y.values():gross*=1+row["calendar_piece_net_pct"]/100
        self.assertAlmostEqual(gross,1.1,places=4)
    def test_worst_window_selected_from_btc_only(self):
        d=mock()
        btc=d["BTC/USDT"]["close"]
        for i in range(270,301):btc[i]=100-(i-270)*2
        times=[i*86400000 for i in range(310)]
        worst=s.top_drawdowns(d,251,309,times,30,1)
        self.assertEqual(len(worst),1)
        self.assertLess(worst[0]["btc_30d_pct"],-50)
    def test_no_exchange_orders_or_secrets(self):
        code=(Path(__file__).resolve().parent/"rsi2_volume_calendar_stress.py").read_text()
        for token in ("DATABASE_URL","BYBIT_DEMO_API_KEY","/v5/order/create","INSERT INTO"):
            self.assertNotIn(token,code)
if __name__=="__main__":unittest.main()
