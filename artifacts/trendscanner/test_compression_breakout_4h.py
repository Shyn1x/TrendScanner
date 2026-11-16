"""Check ex-ante contraction and breakout execution properties."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import compression_breakout_4h as m


def fake():
    n=290
    a={k:[v]*n for k,v in {
        "open":100.,"close":100.,"high":101.,"low":99.,
        "upper20":101.,"lower20":99.,
        "compression":.6,"atr14":2.,"ema200":95.}.items()}
    a["time"]=[i*m.TF for i in range(n)]
    return a


def test_compression_vs_plain_breakout():
    a=fake();a["close"][251]=102.
    assert m.side_at_close(a,251,"breakout20")==1
    assert m.side_at_close(a,251,"compression_breakout")==1
    a["compression"][251]=1.2
    assert m.side_at_close(a,251,"breakout20")==1
    assert m.side_at_close(a,251,"compression_breakout")==0


def test_ema_direction_gate():
    a=fake();a["close"][251]=98.;a["ema200"][251]=105.
    assert m.side_at_close(a,251,"compression_ema200")==-1
    a["ema200"][251]=90.
    assert m.side_at_close(a,251,"compression_ema200")==0


def test_no_entry_before_breakout_close():
    a=fake();a["close"][251]=102.
    d={s:{k:v[:] for k,v in a.items()} for s in m.SYMBOLS}
    r=m.replay(d,"compression_breakout",250,259)
    assert r["trades"]==3
    assert r["signal_time_cohorts"]==1
    assert r["capacity_or_gap_skips"]==len(m.SYMBOLS)-3


def test_no_orders_or_secret_access():
    code=(Path(__file__).resolve().parent/"compression_breakout_4h.py").read_text()
    for x in ("BYBIT_DEMO_API_KEY","DATABASE_URL","order/create","INSERT INTO"):
        assert x not in code
