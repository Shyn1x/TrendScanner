"""Offline event contract; no production shadow caller is installed."""
from copy import deepcopy
import json
import sqlite3
import subprocess
import types
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import analysis
import multi_tf
import market_regime_shadow as shadow
from ready_engine import evaluate_ready_candidate, build_ready_report
from test_ready_engine import make_tf
from test_market_regime_shadow import candidate, TARGET, TF, NOW


class Context:
    def __init__(self, target=TARGET, regime='MIXED'):
        self.target, self.regime = target, regime
    def snapshot(self):
        return {'shadow_status': 'OK', 'shadow_error': None,
                'market': {**dict.fromkeys(shadow.MARKET_FIELDS),
                           'market_regime': self.regime, 'volatility': 'NORMAL',
                           'target_4h_timestamp': self.target}}


def observe(db, timestamp, ready=True, **changes):
    return shadow.observe_ready({**candidate(), 'ready_timestamp': timestamp, 'ready': ready, **changes},
                                context=Context(timestamp), db_path=db, observed_at_ms=timestamp+TF+123)


def test_transitions_restart_ordering_and_dedup(tmp_path):
    db = tmp_path/'events.db'
    assert not observe(db, TARGET-TF, False)['inserted']
    first = observe(db, TARGET)
    assert first['inserted'] and first['shadow_tag'] == 'P3_MATCH'
    assert observe(db, TARGET)['event'] == first['event']
    assert not observe(db, TARGET+TF)['inserted']  # True -> True, new candle
    assert not observe(db, TARGET, False)['inserted']  # stale False cannot reset
    assert not observe(db, TARGET+2*TF)['inserted']
    assert not observe(db, TARGET+3*TF, False)['inserted']
    assert observe(db, TARGET+4*TF)['inserted']
    with sqlite3.connect(db) as con:
        rows = con.execute('SELECT ready_timestamp FROM market_regime_shadow_events ORDER BY ready_timestamp').fetchall()
    assert rows == [(TARGET,), (TARGET+4*TF,)]


@pytest.mark.parametrize('regime', ['MIXED', 'BULL'])
def test_only_4h_can_tag(tmp_path, regime):
    db=tmp_path/'events.db'
    ctx=Context(regime=regime)
    one=shadow.observe_ready({**candidate(), 'timeframe':'1h'}, context=ctx, db_path=db, observed_at_ms=NOW)
    assert not one['inserted'] and one['shadow_tag']=='NONE' and not db.exists()
    four=shadow.observe_ready(candidate(), context=ctx, db_path=db, observed_at_ms=NOW)
    assert four['inserted'] and four['shadow_tag'] == ('P3_MATCH' if regime=='MIXED' else 'P2_MATCH')


@pytest.mark.parametrize('offset', [-TF, TF])
def test_exact_context_match_required(tmp_path, offset):
    db=tmp_path/'events.db'
    result=shadow.observe_ready(candidate(), context=Context(TARGET+offset), db_path=db, observed_at_ms=NOW)
    assert result['shadow_status']=='UNAVAILABLE' and result['shadow_tag']=='NONE'
    assert result['event']['shadow']['shadow_error']=='CONTEXT_TIMESTAMP_MISMATCH'
    # Immutable first observation cannot be upgraded by a later refresh.
    retry=shadow.observe_ready(candidate(), context=Context(), db_path=db, observed_at_ms=NOW)
    assert not retry['inserted'] and retry['shadow_tag']=='NONE'


def test_timestamp_pipeline_and_ready_semantics():
    df=pd.DataFrame({'time':[TARGET-TF, TARGET, TARGET+TF], 'close':[1.,2.,3.]})
    quality=make_tf()['quality']; quality['FINAL']={'signal':'LONG','confidence':68.,'label':'MEDIUM','reason':''}
    decision=make_tf()['decision_details']; decision['FINAL']={'decision':'WATCH','direction':'LONG','decision_score':48.}
    with patch.object(analysis,'analyze_both_directions',return_value=quality), patch.object(analysis,'evaluate_both_directions',return_value=decision), patch.object(multi_tf,'get_data',return_value=df):
        result=multi_tf.multi_analysis('BTC/USDT')
    assert result['4h']['ready_timestamp']==int(df.iloc[-2]['time'])
    report=build_ready_report({'BTC/USDT':result})
    assert report['candidates'] and all(c['ready_timestamp']==TARGET for c in report['candidates'])
    for direction in ('LONG','SHORT'):
        baseline=deepcopy(result['4h']); baseline.pop('ready_timestamp')
        a=evaluate_ready_candidate('BTC/USDT','4h',direction,baseline)
        b=evaluate_ready_candidate('BTC/USDT','4h',direction,result['4h'])
        assert b.pop('ready_timestamp')==TARGET
        a.pop('ready_timestamp')
        assert a==b


@pytest.mark.parametrize('seed', [0,7,101])
def test_real_analysis_decisions_equal_checkpoint(seed):
    old=types.ModuleType('checkpoint_analysis')
    exec(subprocess.check_output(['git','show','e62ecbdb04c9d341a0d6324b052b6a1436ab91a5:artifacts/trendscanner/analysis.py'],text=True), old.__dict__)
    rng=np.random.default_rng(seed); close=100+np.cumsum(rng.normal(0,.5,250))
    df=pd.DataFrame({'time':np.arange(250)*TF,'open':close,'high':close+1,'low':close-1,'close':close,'volume':rng.uniform(100,1000,250)})
    before=old.analyze_timeframe(df); after=analysis.analyze_timeframe(df)
    assert after.pop('ready_timestamp')==248*TF
    assert after==before


@pytest.mark.parametrize('value', [None, float('nan'), 1.5, True])
def test_invalid_timestamp_does_not_invent_metadata(value):
    df=pd.DataFrame({'time':[0,value,2]})
    assert analysis._ready_timestamp(df) is None


def test_first_false_candle_cannot_be_revised(tmp_path):
    db=tmp_path/'events.db'
    assert not observe(db, TARGET, False)['inserted']
    assert not observe(db, TARGET)['inserted']
    assert not observe(db, TARGET-TF)['inserted']
    assert observe(db, TARGET+TF)['inserted']
