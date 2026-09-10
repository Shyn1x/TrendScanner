"""Offline live hook tests. All config access and PostgreSQL are injected."""
import ast
from copy import deepcopy
from pathlib import Path
import subprocess
import types
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

import live_shadow as live
import ready_engine
with patch.dict("sys.modules", {"streamlit": Mock()}):
    import ready_ui
import quality_pipeline as quality
import market_regime_shadow as shadow
import shadow_storage
from test_ready_engine import make_tf
from test_shadow_storage import FakePostgresStore, FakeConnection, Context
from test_market_regime_shadow import TARGET, TF

URL = 'postgresql://synthetic-secret@offline/test'


def production(ready=True, timestamp=TARGET, timeframe='4h'):
    source = make_tf(confirmed=ready)
    source['ready_timestamp'] = timestamp
    source['quality']['LONG']['analysis_available'] = True
    return {'BTC/USDT': {timeframe: source, 'FINAL': {'decision':'WATCH','decision_score':48}}}


def evaluations(data):
    result=[]
    ready_engine.build_ready_report(data, evaluations=result)
    return result


def submit(entries, **kwargs):
    return live.observe_streamlit_results(entries, secrets_getter=lambda: URL,
                                             environ_getter=lambda: None, **kwargs)


def complete(result):
    return result


@pytest.mark.parametrize('ready', [True, False])
def test_actual_ui_hook_reuses_evaluation_once(ready, monkeypatch):
    data=production(ready); original=deepcopy(data)
    observer=Mock(return_value={'shadow_status':'OK','inserted':False})
    monkeypatch.setattr(shadow,'observe_ready',observer)
    monkeypatch.setattr(live.os,'environ',{})
    st=types.SimpleNamespace(session_state={}, secrets={'DATABASE_URL':URL},
                             markdown=Mock(),subheader=Mock(),info=Mock(),caption=Mock())
    monkeypatch.setattr(ready_ui,'st',st)
    module=ast.parse(Path(__file__).with_name('app.py').read_text())
    start=next(i for i,n in enumerate(module.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_shadow_evaluations' for t in n.targets))
    hook=ast.Module(body=module.body[start:start+3],type_ignores=[])
    namespace={'st':st,'all_results':data,'render_ready_section':ready_ui.render_ready_section}
    with patch.object(ready_engine,'evaluate_ready_candidate',wraps=ready_engine.evaluate_ready_candidate) as evaluate:
        exec(compile(hook,'live-hook','exec'),namespace)
    assert complete(st.session_state['shadow_collection'])['status']=='OK'
    assert evaluate.call_count==4  # LONG/SHORT for 4h/1h, one UI pass only.
    observer.assert_called_once()
    sent=observer.call_args.args[0]
    assert sent['ready'] is ready and sent['ready_timestamp']==TARGET
    assert observer.call_args.kwargs=={'database_url':URL,'db_path':None}
    assert data==original


@pytest.mark.parametrize('change', ['missing','unknown','error','timestamp','boolean','direction','decision'])
def test_invalid_is_not_false(change, monkeypatch):
    source=production()['BTC/USDT']['4h']; candidate=evaluations(production())[0][0]
    if change=='missing': source={}
    if change=='unknown': source['quality']['LONG'].pop('analysis_available')
    if change=='error': source['quality']['LONG']['analysis_available']=False
    if change=='timestamp': candidate['ready_timestamp']=None
    if change=='boolean': candidate['ready']=None
    if change=='direction': candidate['direction']='NONE'
    if change=='decision': source['decision_details']={}
    observer=Mock(); monkeypatch.setattr(shadow,'observe_ready',observer)
    assert submit([(candidate,source)])['status']=='NO_VALID_EVALUATIONS'
    observer.assert_not_called()


def test_1h_never_submitted(monkeypatch):
    observer=Mock(); monkeypatch.setattr(shadow,'observe_ready',observer)
    assert submit(evaluations(production(timeframe='1h')))['status']=='NO_VALID_EVALUATIONS'
    observer.assert_not_called()


def test_absent_config_no_storage_no_shadow_import(monkeypatch):
    observer=Mock(); monkeypatch.setattr(shadow,'observe_ready',observer)
    local=Mock(side_effect=AssertionError('SQLite forbidden')); monkeypatch.setattr(shadow_storage,'_persist_sqlite',local)
    for getter in (lambda:None, lambda: (_ for _ in ()).throw(KeyError('DATABASE_URL'))):
        result=live.observe_streamlit_results(evaluations(production()),secrets_getter=getter,environ_getter=lambda:None)
        assert result=={'status':'DISABLED'}
    observer.assert_not_called();local.assert_not_called()


def test_config_env_bridge_unchanged(monkeypatch):
    observer=Mock(return_value={'shadow_status':'OK'}); monkeypatch.setattr(shadow,'observe_ready',observer)
    secret=Mock(side_effect=AssertionError('env wins'))
    result=live.observe_streamlit_results(evaluations(production()),secrets_getter=secret,environ_getter=lambda:URL)
    assert complete(result)['status']=='OK'
    secret.assert_not_called()
    assert observer.call_args.kwargs['database_url']==URL


def test_reruns_real_observer_mock_postgres_transitions(monkeypatch):
    store=FakePostgresStore()
    monkeypatch.setattr(shadow_storage,'_connect_postgres',lambda url:FakeConnection(store))
    local=Mock(side_effect=AssertionError('SQLite forbidden'));monkeypatch.setattr(shadow_storage,'_persist_sqlite',local)
    real=shadow.observe_ready
    def observer(candidate, **kwargs):
        return real(candidate,context=Context(candidate['ready_timestamp']),observed_at_ms=TARGET+10*TF,**kwargs)
    monkeypatch.setattr(shadow,'observe_ready',observer)
    sequence=[(0,False),(1,True),(1,True),(2,True),(3,False),(4,True),(1,False),(5,True)]
    inserted=[]
    for offset,ready in sequence:
        inserted.append(complete(submit(evaluations(production(ready,TARGET+offset*TF))))['inserted'])
    assert inserted==[0,1,0,0,0,1,0,0]
    assert len(store.events)==2
    local.assert_not_called()


def test_false_does_not_load_context(monkeypatch):
    monkeypatch.setattr(shadow_storage,'_connect_postgres',lambda url:FakeConnection(FakePostgresStore()))
    context=Mock(side_effect=AssertionError('False must not load context'))
    monkeypatch.setattr(shadow._PROCESS_CONTEXT,'snapshot',context)
    assert complete(submit(evaluations(production(False))))['status']=='OK'
    context.assert_not_called()


@pytest.mark.parametrize('failure', ['observer','postgres','config','import'])
def test_failures_sanitized_production_unchanged(failure,monkeypatch,capsys):
    original=production();before=deepcopy(original)
    def fail(*a,**k): raise RuntimeError(URL)
    if failure=='config':
        result=live.observe_streamlit_results(evaluations(original),secrets_getter=fail,environ_getter=lambda:None)
    else:
        if failure=='observer': monkeypatch.setattr(shadow,'observe_ready',fail)
        if failure=='postgres':
            monkeypatch.setattr(shadow_storage,'_connect_postgres',fail)
            original=production(False);before=deepcopy(original)
        if failure=='import':
            import builtins
            actual=builtins.__import__
            def importer(name,*a,**k):
                if name=='market_regime_shadow': raise ImportError(URL)
                return actual(name,*a,**k)
            monkeypatch.setattr(builtins,'__import__',importer)
        result=complete(submit(evaluations(original)))
    assert result['status'] in ('ERROR','UNAVAILABLE')
    assert original==before
    captured = capsys.readouterr()
    assert URL not in repr(result)+captured.out+captured.err


def test_observer_completes_synchronously(monkeypatch):
    import threading
    caller = threading.get_ident()
    completed=[]
    def observer(*args, **kwargs):
        assert threading.get_ident() == caller
        completed.append(True)
        return {'shadow_status':'OK'}
    monkeypatch.setattr(shadow,'observe_ready',observer)
    result=submit(evaluations(production()))
    assert completed == [True] and result['status'] == 'OK'
    assert 'completion' not in result


def test_no_background_primitives():
    tree=ast.parse(Path(live.__file__).read_text())
    imports=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import): imports.extend(a.name for a in node.names)
        if isinstance(node,ast.ImportFrom): imports.append(node.module)
    assert not any(name.split('.')[0] in ('threading','queue','concurrent','asyncio','multiprocessing') for name in imports)
    assert not any(isinstance(n,(ast.AsyncFunctionDef,ast.Await)) for n in ast.walk(tree))


def test_report_ranking_unchanged_and_observer_cannot_mutate(monkeypatch):
    data=production();before=deepcopy(data)
    expected=ready_engine.build_ready_report(data)
    collected=[];actual=ready_engine.build_ready_report(data,evaluations=collected)
    def observer(candidate,**kwargs):
        candidate['ready']=False;candidate['confidence']=0
        return {'shadow_status':'OK','decision':'TAKE','ranking':['injected']}
    monkeypatch.setattr(shadow,'observe_ready',observer)
    complete(submit(collected))
    assert data==before and actual==expected


def test_quality_availability_distinguishes_no_pattern_and_error(monkeypatch):
    df=pd.DataFrame({'open':[1.]*30,'high':[2.]*30,'low':[.5]*30,'close':[1.]*30})
    monkeypatch.setattr(quality,'create_trendline',lambda points:None)
    assert quality.analyze_both_directions(df)['LONG']['analysis_available'] is True
    def fail(*args,**kwargs):raise RuntimeError('offline error')
    monkeypatch.setattr(quality,'find_pivots',fail)
    assert quality.analyze_both_directions(df)['LONG']['analysis_available'] is False


@pytest.mark.parametrize('seed',[0,7,101])
def test_quality_production_values_equal_checkpoint(seed):
    old=types.ModuleType('old_quality')
    exec(subprocess.check_output(['git','show','f1c8a076c0575dff4585670766cd15dd01cd4b7b:artifacts/trendscanner/quality_pipeline.py'],text=True),old.__dict__)
    rng=np.random.default_rng(seed);close=100+np.cumsum(rng.normal(0,.5,250))
    df=pd.DataFrame({'open':close,'high':close+1,'low':close-1,'close':close,'volume':rng.uniform(100,1000,250)})
    actual=quality.analyze_both_directions(df)
    for direction in ('LONG','SHORT'): actual[direction].pop('analysis_available')
    assert actual==old.analyze_both_directions(df)


def test_synchronous_calls_and_batch_timestamps_sorted(monkeypatch):
    seen=[]
    def observer(candidate,**kwargs):
        seen.append((candidate['ready_timestamp'],candidate['ready']))
        return {'shadow_status':'OK'}
    monkeypatch.setattr(shadow,'observe_ready',observer)
    first=submit(evaluations(production(True,TARGET+TF))+evaluations(production(False,TARGET)))
    second=submit(evaluations(production(False,TARGET+2*TF)))
    complete(first);complete(second)
    assert seen==[(TARGET,False),(TARGET+TF,True),(TARGET+2*TF,False)]


def test_component_exception_does_not_become_valid_false(monkeypatch):
    df=pd.DataFrame({'open':[1.]*30,'high':[2.]*30,'low':[.5]*30,'close':[1.]*30})
    monkeypatch.setattr(quality,'find_pivots',lambda *a,**k: ([],[]))
    monkeypatch.setattr(quality,'create_trendline',lambda points:{'x1':0,'x2':10,'y1':2.,'y2':1.})
    def fail(*args,**kwargs):raise RuntimeError('component error')
    monkeypatch.setattr(quality,'calc_trend_quality',fail)
    assert quality.analyze_both_directions(df)['LONG']['analysis_available'] is False
