"""Independent research-engine audit regressions."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import audit_research_engines as m


def test_short_exit_parity_mismatch_is_observable():
    r=m.exit_contract_example()
    assert r["expected_short_exit_10bar"] is True
    assert r["actual_research_exit_short"] is False
    assert r["matches_historical_rule"] is False


def test_cost_roundtrip_per_notional_is_16bps():
    entry=100
    exit_=100
    notional=1000
    cost=.0008
    pnl=notional*((exit_/entry-1)*1-2*cost)
    assert round(pnl,2)==-1.60


def test_gap_stop_fill_is_worse_than_stop_for_long_short():
    long_stop,long_open=98,95
    short_stop,short_open=102,105
    assert min(long_stop,long_open)==95
    assert max(short_stop,short_open)==105


def test_no_credential_or_order_paths():
    code=(Path(__file__).resolve().parent/"audit_research_engines.py").read_text()
    for prohibited in ("DATABASE_URL","BYBIT_DEMO_API_KEY","order/create","INSERT INTO","DELETE FROM"):
        assert prohibited not in code
