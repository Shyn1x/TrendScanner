import unittest
from datetime import datetime,timezone,timedelta
from btc_monthly_sma_cash import month_signals,replay,MODES

class TestSMA(unittest.TestCase):
    def sample(self):
        start=datetime(2023,1,1,tzinfo=timezone.utc)
        days=[]
        for i in range(400):
            t=start+timedelta(days=i)
            price=100+i*.1
            days.append({"ts":int(t.timestamp()*1000),"open":price,"close":price})
        return days
    def test_month_end_signal_first_trades_next_open(self):
        days=self.sample();signals=month_signals(days)
        for index in signals:
            self.assertTrue(index>0)
            self.assertNotEqual(datetime.fromtimestamp(days[index-1]["ts"]/1000,timezone.utc).month,
                                datetime.fromtimestamp(days[index]["ts"]/1000,timezone.utc).month)
    def test_passive_higher_uptrend_than_cash(self):
        days=self.sample();signals=month_signals(days)
        a=replay(days,signals,320,399,"passive_btc")
        b=replay(days,signals,320,399,"monthly_sma_half")
        self.assertGreater(a["net_return_pct"],b["net_return_pct"])
    def test_no_future_signal_used(self):
        days=self.sample()
        a=month_signals(days)
        days[-1]["close"]=99999.
        b=month_signals(days)
        self.assertEqual({k:v for k,v in a.items() if k<399},
                         {k:v for k,v in b.items() if k<399})
if __name__=="__main__":unittest.main()
