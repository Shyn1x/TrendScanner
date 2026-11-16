"""Independent audit of previously used research engines, with no trading mutations.

Reports explicit invariant mismatch between production-shadow Donchian evaluator
and historical 10-bar short exit, plus costs-only historical sensitivities.
No credentials, DB writes or order surfaces. No backtest parameters optimized.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import donchian_go_nogo_4h as donchian
import momentum_loss_audit as momentum
import compression_breakout_4h as compression
import regime_donchian_research as signal_spec
from prospective_market_data import get_bybit_data_before

COSTS=(0.0,0.0008,0.0016)
END_LABEL="later_period_already_seen"


def exit_contract_example():
    """Construct valid 25-candle SHORT exit at 10-bar but NOT 20-bar high."""
    rows=[]
    for i in range(25):
        high=120. if i==5 else 105.
        rows.append({"time":i*donchian.TF,"open":100.,"high":high,
                     "low":95.,"close":100.})
    rows[-1]["close"]=110.
    rows[-1]["high"]=112.
    result=signal_spec.evaluate(rows,"MIXED",rows[-1]["time"])
    prior10=max(r["high"] for r in rows[-11:-1])
    prior20=max(r["high"] for r in rows[-21:-1])
    assert prior10==105. and prior20==120.
    assert rows[-1]["close"]>prior10 and rows[-1]["close"]<prior20
    return {"prior10_upper":prior10,"prior20_upper":prior20,
            "latest_close":rows[-1]["close"],
            "expected_short_exit_10bar":True,
            "actual_research_exit_short":result["exit_short"],
            "matches_historical_rule":result["exit_short"] is True,
            "research_signal":result["signal"]}


def audit_pnl_engines(frames):
    """Re-run unchanged entry/exit engines at frozen 0/8/16bp per-side costs."""
    results={}
    d,_=donchian.features(frames)
    c=compression.features(frames)
    m=momentum.features(frames)
    cut=int(momentum.BARS*.6)
    split_bounds={"train":(momentum.WARMUP,cut),
                  END_LABEL:(cut+20,momentum.BARS)}
    for cost in COSTS:
        donchian.COST_SIDE=cost
        compression.COST_SIDE=cost
        momentum.COST_SIDE=cost
        key=str(round(cost*10000))
        results[key]={}
        for period,(start,end) in split_bounds.items():
            results[key][period]={
                "donchian":donchian.replay(d,["MIXED"]*momentum.BARS,
                                          start,end,"no_regime_donchian"),
                "compression":compression.replay(c,"compression_ema200",start,end),
                "momentum":momentum.backtest(m,"mom60_ema200",start,end)["metrics"]
            }
    return results


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    mismatch=exit_contract_example()
    boundary=int(time.time()*1000)//donchian.TF*donchian.TF
    frames={s:get_bybit_data_before(s,"4h",boundary,total_limit=momentum.BARS)
            for s in momentum.SYMBOLS}
    scenarios=audit_pnl_engines(frames)
    abbreviated={cost:{period:{
        name:{"return_pct":r.get("net_return_pct",r.get("net_pct")),
              "trades":r["trades"],
              "dd_pct":r.get("max_marked_drawdown_pct",r.get("max_marked_dd_pct",
                        r.get("max_realized_drawdown_pct")))}
        for name,r in tests.items()}
        for period,tests in periods.items()}
        for cost,periods in scenarios.items()}
    print(json.dumps({
        "research_only":True,
        "donchian_contract_check":mismatch,
        "cost_bps_per_side":[0,8,16],
        "cost_sensitivity":abbreviated,
        "caveats":[
            "No funding, real fill or delisting data; results optimistic",
            "The historical period has been inspected in previous research, not blind",
            "Comparison uses different exit designs for different strategies",
            "No recommendation to deploy or modify running shadow; signal source discrepancy identified"
        ]},sort_keys=True))


if __name__=="__main__":main()
