from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import ccxt
import pandas as pd
import pytest

import market_regime_shadow as shadow

TF = shadow.TIMEFRAME_MS
TARGET = 1000 * TF
NOW = TARGET + TF + 123
SYMBOLS = tuple(f"COIN{i}/USDT" for i in range(24)) + ("BTC/USDT",)


def frame(target=TARGET):
    return pd.DataFrame({"time": [target - (213-i)*TF for i in range(214)],
        "open": [100+i*.1 for i in range(214)], "high": [102+i*.1 for i in range(214)],
        "low": [98+i*.1 for i in range(214)], "close": [100+i*.1 for i in range(214)]})


class Loader:
    def __init__(self): self.calls = []
    def __call__(self, symbol, timeframe, *, before_timestamp, total_limit):
        self.calls.append((symbol, timeframe, before_timestamp, total_limit))
        return frame(before_timestamp-TF)


def candidate():
    return {"ready": True, "symbol": "BTC/USDT", "timeframe": "4h", "direction": "LONG",
            "ready_timestamp": TARGET, "production_decision": "WATCH", "decision_score": 51,
            "confidence": 65, "warnings": ["original"], "blockers": []}


def cache(loader=None, clock=lambda: NOW, symbols=SYMBOLS):
    return shadow.MarketContextCache(loader or Loader(), clock, symbols)


@pytest.mark.parametrize("direction,regime,volatility,expected", [
    ("LONG", "MIXED", "NORMAL", ("P3_MATCH", "PRIMARY")),
    ("LONG", "BULL", "NORMAL", ("P2_MATCH", "EXPERIMENTAL")),
    ("SHORT", "MIXED", "NORMAL", ("NONE", "NONE")),
    ("SHORT", "BEAR", "HIGH", ("NONE", "NONE")),
    ("LONG", "BULL", "HIGH", ("NONE", "NONE")),
])
def test_preregistered_mapping(direction, regime, volatility, expected):
    assert shadow.shadow_tag(direction, regime, volatility) == expected


def test_exact_closed_boundary_and_shared_functions():
    loader = Loader()
    with patch.object(shadow.validated, '_frame_features', wraps=shadow.validated._frame_features) as features, \
         patch.object(shadow.validated, '_attach_market', wraps=shadow.validated._attach_market) as attach, \
         patch.object(shadow.validated, 'classify_regime', wraps=shadow.validated.classify_regime) as classify:
        snap = cache(loader).snapshot()
    assert snap['shadow_status'] == 'OK'
    assert snap['market']['target_4h_timestamp'] == TARGET
    assert snap['market']['prior_atr_observations'] == 200
    assert snap['market']['available_symbols'] == 25
    assert all(call[1:] == ('4h', TARGET+TF, 214) for call in loader.calls)
    assert features.call_count == 25 and attach.call_count == classify.call_count == 1


def test_cached_once_concurrently_and_rolls_over():
    loader, now = Loader(), [NOW]
    context = cache(loader, clock=lambda: now[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: context.snapshot(), range(8)))
    assert len(loader.calls) == 25 and all(s == snapshots[0] for s in snapshots)
    snapshots[0]['market']['market_regime'] = 'MUTATED'
    assert context.snapshot()['market']['market_regime'] != 'MUTATED'
    now[0] += TF
    assert context.snapshot()['market']['target_4h_timestamp'] == TARGET+TF
    assert len(loader.calls) == 50
    now[0] -= TF
    context.snapshot()
    assert len(loader.calls) == 50


@pytest.mark.parametrize('problem', ['missing_target','open_candle','short_history','gap','duplicate','nan','invalid_range'])
def test_bad_ohlcv_unavailable(problem):
    def load(*args, **kwargs):
        df=frame()
        if problem == 'missing_target': df.loc[213,'time'] -= TF
        if problem == 'open_candle': df.loc[213,'time'] += TF
        if problem == 'short_history': df = df.iloc[1:]
        if problem == 'gap': df.loc[3,'time'] -= TF
        if problem == 'duplicate': df.loc[3,'time'] = df.loc[2,'time']
        if problem == 'nan': df.loc[3,'close'] = float('nan')
        if problem == 'invalid_range': df.loc[3,'low'] = 999
        return df
    result=cache(load, symbols=['BTC/USDT']).snapshot()
    assert result['shadow_status'] == 'UNAVAILABLE' and result['shadow_tag'] == 'NONE'


def test_missing_btc():
    assert cache(symbols=['ETH/USDT']).snapshot()['shadow_status'] == 'UNAVAILABLE'


def test_partial_context_not_substituted():
    def load(symbol,*args,**kwargs):
        if symbol == 'ETH/USDT': raise ccxt.NetworkError('private URL')
        return frame()
    snap=cache(load,symbols=['BTC/USDT','ETH/USDT']).snapshot()
    assert snap['shadow_status']=='UNAVAILABLE' and snap['market']['available_symbols']==1
    assert 'private URL' not in json.dumps(snap)


def test_network_failure_cached_and_production_unchanged(tmp_path):
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1)
        raise ccxt.NetworkError('private URL')
    context=cache(fail,symbols=['BTC/USDT'])
    original=candidate(); before=deepcopy(original)
    for _ in range(2):
        result=shadow.observe_ready(original,context=context,db_path=tmp_path/'events.db',observed_at_ms=NOW)
        assert result['shadow_status']=='UNAVAILABLE' and result['shadow_tag']=='NONE'
        assert original==before
    assert len(calls)==1


def test_immutable_event_schema_and_dedup(tmp_path):
    original=candidate(); before=deepcopy(original); context=cache(symbols=['BTC/USDT'])
    db=tmp_path/'events.db'
    first=shadow.observe_ready(original,context=context,db_path=db,observed_at_ms=NOW)
    second=shadow.observe_ready(original,context=context,db_path=db,observed_at_ms=NOW+100)
    assert first['inserted'] and not second['inserted'] and original==before
    assert first['event'] == second['event']
    with sqlite3.connect(db) as con:
        rows=con.execute('SELECT payload FROM market_regime_shadow_events').fetchall()
        assert len(rows)==1
        event=json.loads(rows[0][0])
        assert event==first['event']
        with pytest.raises(sqlite3.IntegrityError): con.execute('UPDATE market_regime_shadow_events SET ready_timestamp=1')
        with pytest.raises(sqlite3.IntegrityError): con.execute('DELETE FROM market_regime_shadow_events')
    assert set(event)=={'schema_version','shadow_version','observed_at','symbol','timeframe','direction','ready_timestamp','production','market','shadow'}
    assert event['shadow_version']=='market-regime-v1-preregistered'
    assert set(event['market']) == set(shadow.MARKET_FIELDS)|{'target_4h_timestamp'}
    assert event['production']=={'decision':'WATCH','decision_score':51,'confidence':65}
    assert 'outcome' not in json.dumps(event)


def test_concurrent_storage_and_restart_dedup(tmp_path):
    context=cache(symbols=['BTC/USDT']); db=tmp_path/'events.db'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:shadow.observe_ready(candidate(),context=context,db_path=db,observed_at_ms=NOW),range(8)))
    assert sum(r['inserted'] for r in results)==1
    assert all(r['shadow_status']=='OK' for r in results)
    assert not shadow.observe_ready(candidate(),context=cache(symbols=['BTC/USDT']),db_path=db,observed_at_ms=NOW)['inserted']


def test_only_existing_ready_with_timestamp(tmp_path):
    loader=Loader(); context=cache(loader)
    for change in ({'ready':False},{'ready':None},{'ready_timestamp':None}):
        row={**candidate(),**change}
        result=shadow.observe_ready(row,context=context,db_path=tmp_path/'events.db',observed_at_ms=NOW)
        assert not result['inserted']
    row=candidate(); del row['ready_timestamp']
    assert not shadow.observe_ready(row,context=context,db_path=tmp_path/'events.db',observed_at_ms=NOW)['inserted']
    assert not loader.calls and not (tmp_path/'events.db').exists()


def test_internal_shadow_and_storage_failure_fail_open(tmp_path):
    original=candidate(); before=deepcopy(original)
    with patch.object(shadow.validated,'_attach_market',side_effect=RuntimeError('internal')):
        result=shadow.observe_ready(original,context=cache(symbols=['BTC/USDT']),db_path=tmp_path/'a.db',observed_at_ms=NOW)
        assert result['shadow_status']=='UNAVAILABLE'
    result=shadow.observe_ready(original,context=cache(symbols=['BTC/USDT']),db_path=tmp_path,observed_at_ms=NOW)
    assert result['shadow_status']=='UNAVAILABLE' and not result['inserted']
    assert original==before


def test_clock_failure_fail_open():
    def fail(): raise RuntimeError('clock')
    assert cache(clock=fail).snapshot()['shadow_status']=='UNAVAILABLE'


def test_source_frames_not_mutated():
    df=frame(); before=df.copy(deep=True)
    cache(lambda *a,**k:df,symbols=['BTC/USDT']).snapshot()
    pd.testing.assert_frame_equal(df,before)
