"""Unit tests for locked momentum hypothesis and entry-time attribution."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import momentum_loss_audit as m


def series():
    a={k:[v]*310 for k,v in {"open":100.,"close":100.,"high":102.,"low":98.,
       "ema200":95.,"atr":2.,"return60":.01,"return20":.02,
       "atr_pct":2.}.items()}
    a["time"]=[i*m.TF for i in range(310)]
    return a


def test_signal_at_threshold_only_on_crossover():
    a=series()
    a["return60"][255]=.035
    assert m.entry_side(a,255,"mom60")==1
    assert m.entry_side(a,255,"mom60_ema200")==1
    a["return60"][256]=.04
    assert m.entry_side(a,256,"mom60")==0


def test_ema_gate_and_short():
    a=series()
    a["return60"][255]=-.05
    a["ema200"][255]=105.
    assert m.entry_side(a,255,"mom60_ema200")==-1
    a["ema200"][255]=95.
    assert m.entry_side(a,255,"mom60_ema200")==0


def test_tags_are_entry_time_not_future():
    a=series()
    a["return60"][255]=.035
    tags=m.label_at_entry(a,255,1)
    a["close"][256]=1000.
    a["return20"][256]=100.
    assert tags==m.label_at_entry(a,255,1)


def test_backtest_stops_and_correlated_capacity():
    a=series()
    a["return60"][255]=.035
    for i in range(256,261):
        a["open"][i]=100.
        a["high"][i]=104.
        a["low"][i]=98.
    data={s:{k:v[:] for k,v in a.items()} for s in m.SYMBOLS}
    r=m.backtest(data,"mom60",251,262)
    assert r["metrics"]["trades"]==3
    assert r["metrics"]["independent_entry_times"]==1
    assert r["metrics"]["rejected_capacity"]==len(m.SYMBOLS)-3


def test_no_credentials_or_orders():
    text=(Path(__file__).resolve().parent/"momentum_loss_audit.py").read_text()
    for value in ("order/create","DATABASE_URL","BYBIT_DEMO_API_KEY","INSERT INTO"):
        assert value not in text
