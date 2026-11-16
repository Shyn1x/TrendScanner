"""Reconstruct paths without changing the frozen entry or stop logic."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import momentum_path_diagnostics as m

def frame():
    d={"time":[i*m.base.TF for i in range(30)],
       "open":[100.]*30,"high":[102.]*30,"low":[98.]*30,"close":[100.]*30}
    d["close"][5]=96.
    d["high"][5]=104.
    d["low"][5]=94.
    d["close"][10]=105.
    return d

def trade(exit_i=5,reason="STOP",loss=-2.16):
    return {"symbol":"BTC/USDT","direction":"LONG",
            "entry_ms":2*m.base.TF,"exit_ms":exit_i*m.base.TF,
            "net_trade_pct":loss,"pnl_usdt":-20.,
            "exit_reason":reason,"trend_alignment":"aligned",
            "recent_conflict":"aligned","volatility":"normal"}

def test_observed_path_and_future_window():
    p=m.path_for_trade(trade(),frame(),29)
    assert p["is_loss"] is True
    assert p["mfe_intrabar_pct"]==4.
    assert p["mae_intrabar_pct"]==-6.
    assert p["post_exit_24h_pct"]==round((100/96-1)*100,5)
    assert p["hypothetical_no_stop_24h_net_pct"]==-0.16

def test_future_missing_at_split_end():
    p=m.path_for_trade(trade(28),frame(),29)
    assert p["post_exit_24h_pct"] is None

def test_no_future_changes_intrade_mfe():
    a=frame()
    x=m.path_for_trade(trade(),a,29)
    a["high"][25]=999.
    assert m.path_for_trade(trade(),a,29)["mfe_intrabar_pct"]==x["mfe_intrabar_pct"]

def test_aggregations_and_no_prediction():
    x=m.path_for_trade(trade(),frame(),29)
    s=m.groups([x])
    assert s["stopped_losses"]["trades"]==1
    assert s["nonlosses"]["trades"]==0
    assert s["stopped_losses"]["distinct_entry_times"]==1

def test_no_orders_or_secrets():
    s=(Path(__file__).resolve().parent/"momentum_path_diagnostics.py").read_text()
    for bad in ("order/create","DATABASE_URL","BYBIT_DEMO_API_KEY","INSERT INTO"):
        assert bad not in s
