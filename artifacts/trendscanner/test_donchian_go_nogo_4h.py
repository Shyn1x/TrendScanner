"""No-lookahead and portfolio-cap checks for Donchian replay."""
import importlib.util
from pathlib import Path
import sys
import pytest

root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location("go_nogo",root/"donchian_go_nogo_4h.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def synthetic():
    n=300
    base={"time":[i*m.TF for i in range(n)],"open":[100.]*n,
          "high":[101.]*n,"low":[99.]*n,"close":[100.]*n,
          "upper20":[101.]*n,"lower20":[99.]*n,
          "upper10":[101.]*n,"lower10":[99.]*n,
          "atr":[5.]*n}
    return {s:{k:v[:] for k,v in base.items()} for s in m.SYMBOLS}


def test_signal_enter_next_open_only_and_max_three_correlated():
    arrays=synthetic()
    for a in arrays.values():
        a["close"][250]=98
        a["low"][250]=97
        a["close"][252]=102
        a["high"][252]=103
    regimes=["BEAR"]*300
    r=m.replay(arrays,regimes,250,256,"regime_donchian")
    assert r["trades"]==3
    assert r["entry_time_cohorts"]==1
    assert r["capacity_rejections"]["correlation_capacity"]>=9
    # entry cannot occur until the candle after the close breakout.
    # Positions exit by next-open 10-bar opposing channel.
    assert r["stop_exits"]==0


def test_regime_gate_vs_unfiltered_baseline():
    arrays=synthetic()
    for a in arrays.values():
        a["close"][250]=98
        a["low"][250]=97
    mixed=["MIXED"]*300
    assert m.replay(arrays,mixed,250,255,"regime_donchian")["trades"]==0
    assert m.replay(arrays,mixed,250,255,"no_regime_donchian")["trades"]>0


def test_reject_or_inconclusive_not_optimistic():
    a={"entry_time_cohorts":40,"net_return_pct":.1,"max_realized_drawdown_pct":5}
    b={"entry_time_cohorts":40,"net_return_pct":.4,"max_realized_drawdown_pct":5}
    assert m.decision({"regime_donchian":a,"no_regime_donchian":b,
                       "short_only_donchian":a}).startswith("REJECT")
    a["entry_time_cohorts"]=10
    assert m.decision({"regime_donchian":a,"no_regime_donchian":b,
                       "short_only_donchian":a}).startswith("INCONCLUSIVE")


def test_no_order_endpoint_or_db_in_replay():
    src=(root/"donchian_go_nogo_4h.py").read_text()
    for forbidden in ("api-demo.bybit.com","order/create","INSERT INTO","DELETE FROM",
                      "BYBIT_DEMO_API_KEY","DATABASE_URL"):
        assert forbidden not in src
