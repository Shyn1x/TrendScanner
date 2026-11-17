import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from strategy_conflict_guard import Position,Proposal,select_entries
class ConflictGuardTests(unittest.TestCase):
    def test_existing_owner_cannot_be_overridden(self):
        a,b=select_entries([Position("BTC/USDT","RSI2_1D",2,60)],
          [Proposal("BTC/USDT","COMPANION_4H","LONG",1,25)],300)
        self.assertFalse(a)
        self.assertEqual(b[0][1],"SYMBOL_ALREADY_OWNED")
    def test_priority_and_duplicate(self):
        a,b=select_entries([],[
          Proposal("ETH/USDT","COMPANION_4H","LONG",2,40),
          Proposal("ETH/USDT","RSI2_1D","LONG",2,40)],300)
        self.assertEqual(a[0].owner,"RSI2_1D")
        self.assertEqual(b[0][1],"SYMBOL_ALREADY_OWNED")
    def test_no_short_or_reverse(self):
        a,b=select_entries([],[
          Proposal("SOL/USDT","COMPANION_4H","SHORT",1,20)],300)
        self.assertFalse(a)
        self.assertEqual(b[0][1],"UNSUPPORTED_STRATEGY_OR_SIDE")
    def test_cumulative_aggregate_stop_cap(self):
        a,b=select_entries([Position("BTC/USDT","RSI2_1D",7,60)],
            [Proposal("ETH/USDT","COMPANION_4H","LONG",3,40)],300)
        self.assertFalse(a)
        self.assertEqual(b[0][1],"AGGREGATE_STOP_RISK")
    def test_existing_position_prevents_slot_overfill(self):
        held=[Position(s,"RSI2_1D",1,25) for s in ("BTC/USDT","ETH/USDT","SOL/USDT")]
        a,b=select_entries(held,[Proposal("XRP/USDT","COMPANION_4H","LONG",2,50)],300)
        self.assertFalse(a)
        self.assertEqual(b[0][1],"MAX_SYMBOL_SLOTS")
    def test_two_independent_symbols_accepted(self):
        a,b=select_entries([],[
            Proposal("ETH/USDT","COMPANION_4H","LONG",2,45),
            Proposal("BTC/USDT","RSI2_1D","LONG",2,45)],300)
        self.assertEqual([p.owner for p in a],["RSI2_1D","COMPANION_4H"])
        self.assertFalse(b)
    def test_signal_strength_precedes_alphabetical_symbol(self):
        a,b=select_entries([],[
            Proposal("AAA/USDT","RSI2_1D","LONG",2,40,2.9),
            Proposal("ZZZ/USDT","RSI2_1D","LONG",2,40,0.4),
            Proposal("BBB/USDT","RSI2_1D","LONG",2,40,1.3),
            Proposal("CCC/USDT","RSI2_1D","LONG",2,40,2.0)],300)
        self.assertEqual([x.symbol for x in a],
                         ["ZZZ/USDT","BBB/USDT","CCC/USDT"])
        self.assertEqual(b[0][1],"MAX_SYMBOL_SLOTS")
    def test_notional_limit(self):
        a,b=select_entries([Position("BTC/USDT","RSI2_1D",2,440)],
            [Proposal("ETH/USDT","COMPANION_4H","LONG",2,20)],300)
        self.assertFalse(a)
        self.assertEqual(b[0][1],"MAX_NOTIONAL")
if __name__=="__main__":unittest.main()
