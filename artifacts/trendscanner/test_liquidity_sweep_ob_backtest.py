"""Deterministic no-future 4H signal tests."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import liquidity_sweep_ob_backtest as m

def series():
    n=260
    a={k:[v]*n for k,v in {"open":100.,"high":102.,"low":98.,"close":101.,
        "ema20":100.,"ema50":110.,"ema200":90.,"atr":2.,
        "prev_low10":98.,"prev_high10":102.}.items()}
    a["time"]=[i*m.TF for i in range(n)]
    return a

def test_liquidity_sweep_requires_reclaim():
    a=series();i=251
    a["open"][i]=99.;a["low"][i]=96.;a["close"][i]=100.
    assert m.signal(a,i,"sweep")==1
    a["close"][i]=97.
    assert m.signal(a,i,"sweep")==0

def test_ob_needs_prior_confirmed_displacement():
    a=series();i=251
    a["open"][i]=99.;a["low"][i]=96.;a["close"][i]=100.
    # Candidate bearish candle j=i-2, then decisive upside close before sweep.
    j=i-2
    a["open"][j]=100.;a["close"][j]=99.;a["high"][j]=101.;a["low"][j]=97.
    a["close"][i-1]=103.
    assert m.signal(a,i,"sweep_ob")==1
    # No strong displacement means not a qualified OB.
    a["close"][i-1]=101.
    assert m.signal(a,i,"sweep_ob")==0


def test_baseline_and_side():
    a=series();i=251
    a["open"][i]=99.;a["close"][i]=101.;a["low"][i]=99.
    assert m.signal(a,i,"ema_pullback")==1
    a["ema50"][i]=80.;a["ema200"][i]=90.
    a["open"][i]=101.;a["close"][i]=99.;a["high"][i]=101.
    assert m.signal(a,i,"ema_pullback")==-1

def test_no_optimistic_verdict_with_sparse_data():
    row={"entry_time_cohorts":10,"net_return_pct":5,"max_marked_drawdown_pct":1}
    assert m.verdict({s:row for s in m.MODES},{s:row for s in m.MODES}).startswith("INCONCLUSIVE")

def test_no_orders_or_secrets():
    text=(Path(__file__).resolve().parent/"liquidity_sweep_ob_backtest.py").read_text()
    for forbidden in ("order/create","api-demo.bybit.com","BYBIT_DEMO_API_KEY","DATABASE_URL","INSERT INTO"):
        assert forbidden not in text
