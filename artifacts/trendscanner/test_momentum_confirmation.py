"""No-lookahead confirmation and matched wait-only comparator tests."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import momentum_confirmation_test as m

def arrays(confirm=True):
    n=280
    a={"time":[i*m.base.TF for i in range(n)],
       "open":[100.]*n,"close":[100.]*n,
       "high":[103.]*n,"low":[98.]*n,
       "ema200":[95.]*n,"atr":[2.]*n,"return60":[.0]*n}
    a["return60"][255]=.04
    a["close"][256]=101. if confirm else 99.
    return {sym:{k:v[:] for k,v in a.items()} for sym in m.base.SYMBOLS}


def test_delayed_confirm_only_when_waited_bar_favorable():
    good=arrays(True)
    bad=arrays(False)
    a=m.replay(good,"delayed_confirm",251,265)
    b=m.replay(bad,"delayed_confirm",251,265)
    assert a["trades"]==3
    assert b["trades"]==0
    assert b["skipped_failed_confirmation"]==len(m.base.SYMBOLS)


def test_delay_control_keeps_entry_even_if_bar_unfavorable():
    bad=arrays(False)
    result=m.replay(bad,"delayed_unfiltered",251,265)
    assert result["trades"]==3
    assert result["skipped_failed_confirmation"]==0


def test_immediate_ignores_lookahead_bar_confirmation():
    result=m.replay(arrays(False),"immediate",251,265)
    assert result["trades"]==3
    assert result["independent_signal_times"]==1


def test_decision_rejects_negative_or_small_sample():
    base={"independent_signal_times":40,"net_return_pct":-1.,
          "max_marked_drawdown_pct":3}
    d={k:base for k in m.MODES}
    assert m.decision(d,d)=="REJECT_NO_POSITIVE_TWO_PERIOD_EDGE"
    tiny={**base,"independent_signal_times":5}
    assert m.decision(d,{**d,"delayed_confirm":tiny}).startswith("INSUFFICIENT")


def test_no_secret_or_order_routes():
    s=(Path(__file__).resolve().parent/"momentum_confirmation_test.py").read_text()
    for bad in ("DATABASE_URL","BYBIT_DEMO_API_KEY","order/create","INSERT INTO"):
        assert bad not in s
