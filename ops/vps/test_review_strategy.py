import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('review_strategy',Path(__file__).with_name('review_strategy.py'))
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class ReviewTests(unittest.TestCase):
    def test_script_has_no_private_data_access(self):
        import ast
        tree=ast.parse(Path(__file__).with_name('review_strategy.py').read_text())
        imports=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): imports.extend(a.name for a in node.names)
            if isinstance(node,ast.ImportFrom): imports.append(node.module)
        self.assertFalse(set(imports) & {'audit_demo','bybit_demo_executor','psycopg','psycopg2'})
        source=Path(__file__).with_name('review_strategy.py').read_text()
        for forbidden in ['credentials.json','DATABASE_URL','API_SECRET','api-demo.bybit.com']:
            self.assertNotIn(forbidden,source)

    def test_public_path_cannot_submit_order(self):
        calls=[]
        with self.assertRaisesRegex(ValueError,'ALLOWLIST'):
            review.fetch_klines('/v5/order/create','BTC/USDT','1',60000,120000,
                                opener=lambda *args,**kwargs:calls.append(args))
        self.assertEqual(calls,[])

    def test_public_requests_are_get_and_missing_minutes_fail(self):
        def opener(request,**kwargs):
            self.assertEqual(request.get_method(),'GET')
            self.assertTrue(request.full_url.startswith('https://api.bybit.com/v5/market/kline?'))
            return io.StringIO(json.dumps({'retCode':0,'result':{'list':[]}}))
        with self.assertRaisesRegex(ValueError,'KLINE_GAP'):
            review.fetch_klines('/v5/market/kline','BTC/USDT','1',60000,180000,opener=opener)

    def test_entry_minute_touch_is_ambiguous_but_next_minute_is_not(self):
        candles=[[60000,100,101,98,100],[120000,100,101,97,100]]
        a=review.first_touch(candles,99,61000,180000)
        self.assertTrue(a['entry_minute_ambiguous'])
        b=review.first_touch(candles,97.5,61000,180000)
        self.assertEqual(b['bar_start_ms'],120000)
        self.assertFalse(b['entry_minute_ambiguous'])

    def test_signal_low_does_not_stop_trade_and_missing_future_fails(self):
        h=review.HOUR
        candles={i*h:[i*h,100,101,99,100] for i in range(13)}
        candles[0]=[0,100,101,90,100]
        self.assertFalse(review.long_outcome(candles,0)['stop_hit'])
        candles[h]=[h,100,101,98,100]
        self.assertTrue(review.long_outcome(candles,0)['stop_hit'])
        del candles[12*h]
        self.assertIsNone(review.long_outcome(candles,0))

    def test_baseline_requires_all_symbols_at_same_hour(self):
        h=review.HOUR
        series=[[i*h,100,101,99,100] for i in range(13)]
        outcomes=[dict(symbol='BTC/USDT',ready=0,gross_pct=0)]
        with self.assertRaisesRegex(ValueError,'INCOMPLETE_UNIVERSE'):
            review.build_baseline(outcomes,['BTC/USDT','ETH/USDT'],{'BTC/USDT':series,'ETH/USDT':series[:-1]})

    def test_baseline_recomputes_saved_contract_without_entry_candle_lookahead(self):
        h=review.HOUR
        series=[[i*h,100,101,99,100] for i in range(13)]
        outcomes=[dict(symbol='BTC/USDT',ready=0,gross_pct=0)]
        paired,pooled=review.build_baseline(outcomes,['BTC/USDT'],{'BTC/USDT':series})
        self.assertAlmostEqual(paired[0]['excess_pct'],0)
        self.assertEqual(len(pooled),1)


if __name__=='__main__':
    unittest.main()
