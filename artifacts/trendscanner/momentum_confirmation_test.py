"""Frozen next-candle momentum entry confirmation A/B experiment.

Three modes, same predeclared 4H momentum signal and identical risk/exit:
immediate: enter next open
delay:     wait 1 complete candle, enter the following open regardless
confirm:   wait 1 complete candle, enter the following open only when that
           candle closes in the signal direction relative to signal close

Waited candle information is NEVER used before its close. Delayed strategies
incur full 1-candle opportunity cost, including absence of fills if gap breaks
the original 2ATR stop. No orders, DB writes or private credentials.
"""
from __future__ import annotations
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
import pandas as pd

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import momentum_loss_audit as base
from prospective_market_data import get_bybit_data_before

MODES=("immediate","delayed_unfiltered","delayed_confirm")
CONTRACT={
    "strategy":"4H 60-bar momentum +/-3% threshold CROSS, EMA200 directional gate",
    "modes":{
        "immediate":"signal candle i CLOSE, enter candle i+1 OPEN",
        "delayed_unfiltered":"signal i CLOSE, wait completed i+1 candle, enter i+2 OPEN regardless",
        "delayed_confirm":"signal i CLOSE, enter i+2 OPEN iff CLOSE(i+1) moved in signal direction vs CLOSE(i)"
    },
    "risk":"original signal close +/-2ATR14 stop, 1% equity nominal risk, max 3 positions, max 1x per symbol, 2x gross",
    "exit":"first hit of initial stop, else six held 4H candles; roundtrip 16bp cost",
    "comparison":"same symbols/periods/timed exit for each executed entry; skipped entries not cherry-picked",
    "sources":"12 fixed Bybit linear USDT pairs, 4800 4H candles, historical public OHLC",
    "train_holdout":"first 60% train, 20-bar embargo, remaining 40% holdout, reset portfolio",
    "caveats":"funding not modeled; signals at end of bar; no delisted pairs; 4H OHLC lacks intrabar path"
}


def replay(data,mode,start,end):
    if mode not in MODES:raise ValueError("UNKNOWN_MODE")
    equity=10000.
    peak=equity
    max_dd=0.
    positions={}
    next_open={}  # Candidate already eligible for execution at this candle open.
    waiting={}    # Original signal awaiting following candle close.
    trades=[]
    rejected=0
    expired=0
    dropped_confirmation=0
    original_signals=0
    ts=data[base.SYMBOLS[0]]["time"]

    for i in range(start,end):
        # Process only candidates whose eligibility was decided at <=i-1 close.
        for sym in sorted(next_open):
            c=next_open[sym]
            if sym in positions:continue
            if len(positions)>=3:
                rejected+=1
                continue
            a=data[sym]
            entry=a["open"][i]
            distance=(entry-c["stop"])*c["side"]
            if entry<=0 or distance<=0:
                expired+=1
                continue
            gross=sum(p["notional"] for p in positions.values())
            notion=min(equity*.01*entry/distance,equity,max(0,2*equity-gross))
            if notion<=0:
                rejected+=1
                continue
            positions[sym]={**c,"price":entry,"index":i,"notional":notion,
                            "entry_ms":ts[i]}
        next_open={}

        # Risk model: the original stop can trigger on entry candle.
        for sym in list(positions):
            p=positions[sym];a=data[sym]
            hit=(a["low"][i]<=p["stop"]) if p["side"]==1 else (a["high"][i]>=p["stop"])
            timed=i-p["index"]+1>=6 or i==end-1
            if not hit and not timed:continue
            price=(min(p["stop"],a["open"][i]) if p["side"]==1 else
                   max(p["stop"],a["open"][i])) if hit else a["close"][i]
            gross_return=(price/p["price"]-1)*p["side"]
            pnl=p["notional"]*(gross_return-2*base.COST_SIDE)
            equity+=pnl
            trades.append({"symbol":sym,"entry_ms":p["entry_ms"],
                           "signal_ms":p["signal_ms"],
                           "side":p["side"],"net_pct":round((gross_return-2*base.COST_SIDE)*100,5),
                           "pnl_usdt":round(pnl,5),"exit_reason":"STOP" if hit else "TIME"})
            del positions[sym]

        marked=equity+sum(p["notional"]*((a["close"][i]/p["price"]-1)*p["side"]-2*base.COST_SIDE)
                            for sym,p in positions.items() for a in [data[sym]])
        peak=max(peak,marked)
        max_dd=max(max_dd,1-marked/peak)
        if equity<=0:raise ValueError("EQUITY_EXHAUSTED")
        if i>=end-1:break

        if mode!="immediate":
            # Signal at close i-1 is adjudicated only NOW, at close i.
            # It can be traded only at i+1 OPEN.
            for sym,c in waiting.items():
                if sym in positions:continue
                moved=(data[sym]["close"][i]/c["signal_close"]-1)*c["side"]
                if mode=="delayed_confirm" and moved<=0:
                    dropped_confirmation+=1
                    continue
                next_open[sym]=c
            waiting={}

        # New frozen momentum signals from the current CLOSED candle i.
        for sym in base.SYMBOLS:
            if sym in positions or sym in waiting or sym in next_open:continue
            a=data[sym]
            side=base.entry_side(a,i,"mom60_ema200")
            if not side:continue
            original_signals+=1
            candidate={"side":side,"stop":a["close"][i]-side*2*a["atr"][i],
                       "signal_ms":ts[i],"signal_close":a["close"][i]}
            if mode=="immediate":
                next_open[sym]=candidate
            else:
                waiting[sym]=candidate

    return {"net_return_pct":round((equity/10000-1)*100,4),
            "max_marked_drawdown_pct":round(max_dd*100,4),
            "trades":len(trades),
            "independent_signal_times":len({t["signal_ms"] for t in trades}),
            "win_rate_pct":round(100*sum(t["pnl_usdt"]>0 for t in trades)/len(trades),2)
                           if trades else None,
            "mean_trade_net_pct":round(statistics.mean(t["net_pct"] for t in trades),4)
                                 if trades else None,
            "signal_candidates_seen":original_signals,
            "rejected_portfolio_capacity":rejected,
            "skipped_failed_confirmation":dropped_confirmation,
            "invalidated_by_stop_gap":expired,
            "stop_exits":sum(t["exit_reason"]=="STOP" for t in trades)}


def decision(train,hold):
    a=hold["immediate"];d=hold["delayed_unfiltered"];c=hold["delayed_confirm"]
    if c["independent_signal_times"]<30:
        return "INSUFFICIENT_INDEPENDENT_CONFIRMATION_SIGNALS"
    if c["net_return_pct"]<=0 or train["delayed_confirm"]["net_return_pct"]<=0:
        return "REJECT_NO_POSITIVE_TWO_PERIOD_EDGE"
    if c["net_return_pct"]<=max(a["net_return_pct"],d["net_return_pct"]):
        return "REJECT_CONFIRMATION_NOT_BETTER_THAN_WAIT_ONLY"
    if c["max_marked_drawdown_pct"]>=20:
        return "REJECT_UNACCEPTABLE_DRAWDOWN"
    return "RESEARCH_ONLY_REQUIRES_ADDITIONAL_UNTOUCHED_VALIDATION"


def evaluate(frames):
    data=base.features(frames)
    cut=int(base.BARS*.6)
    result={}
    for name,(s,e) in {"train":(base.WARMUP,cut),"holdout":(cut+20,base.BARS)}.items():
        result[name]={mode:replay(data,mode,s,e) for mode in MODES}
    return {"contract":CONTRACT,"period":{
                "start":pd.to_datetime(data[base.SYMBOLS[0]]["time"][0],unit="ms",utc=True).isoformat(),
                "train_last":pd.to_datetime(data[base.SYMBOLS[0]]["time"][cut-1],unit="ms",utc=True).isoformat(),
                "holdout_last":pd.to_datetime(data[base.SYMBOLS[0]]["time"][-1],unit="ms",utc=True).isoformat()},
            "results":result,"verdict":decision(result["train"],result["holdout"]),
            "limitations":["Historical 4H OHLC and modelled 16bp roundtrip expenses; funding omitted",
                           "Fixed 12-symbol survivor universe, no delisted assets",
                           "Different signals can be accepted under different occupancy; full portfolio comparison matters",
                           "Observed post-entry confirmation is not free: delayed bar forfeits movement and can gap through stop"]}


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//base.TF*base.TF
    frames={s:get_bybit_data_before(s,"4h",boundary,total_limit=base.BARS)
            for s in base.SYMBOLS}
    print(json.dumps(evaluate(frames),sort_keys=True))

if __name__=="__main__":
    main()
