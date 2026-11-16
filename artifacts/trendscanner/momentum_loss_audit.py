"""Frozen 4H time-series momentum baseline with per-trade loss attribution.

Research ONLY: public Bybit OHLC, no orders/DB writes, 12 established symbols.
Signals at completed candle close, entries at next candle open. Fixed 6-bar
holding period or intrabar initial ATR stop, max 3 overlapping positions.
Costs assumed 8bp per side; funding omitted. Tags are ENTRY-TIME features,
not explanations inferred from future outcomes.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import pandas as pd

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from prospective_market_data import get_bybit_data_before

TF=14_400_000
BARS=4800
SYMBOLS=("BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT","XRP/USDT",
         "DOGE/USDT","ADA/USDT","LINK/USDT","LTC/USDT","BCH/USDT",
         "AVAX/USDT","DOT/USDT")
WARMUP=250
COST_SIDE=.0008
HOLD_BARS=6
MOMENTUM_BARS=60
MODELS=("mom60","mom60_ema200")
SCHEMA={
    "research_only":True,"venue":"Bybit USDT linear perpetual public OHLC",
    "bars":BARS,"timeframe":"4h","symbols":SYMBOLS,
    "signal":"LONG if trailing close/close[-60]-1 >= +3%; SHORT if <= -3%",
    "regime_comparator":"same signal, only LONG above EMA200/SHORT below EMA200",
    "entry":"only when a completed 4H candle crosses the momentum threshold, next candle open",
    "stop":"2*ATR14 from signal close, intrabar trigger and pessimistic gap handling",
    "exit":"fixed 6 candles, close of sixth candle, or earlier stop",
    "costs":"8bp each side; funding and spread regime not independently measured",
    "risk":"1% equity theoretical stop risk, max 1x equity per asset, max 3 concurrent positions, max gross 2x equity",
    "split":"first 60% training; 20-bar embargo; last 40% chronological holdout",
    "features":"all tags computed at signal candle close before entry; no future leakage",
    "decision_rule":"NO deployment without positive replicated OOS, controlled max DD and independent cohorts"
}


def features(frames):
    if set(frames)!=set(SYMBOLS):raise ValueError("BAD_UNIVERSE")
    ts=frames[SYMBOLS[0]]["time"].astype("int64").tolist()
    if len(ts)!=BARS or any(b-a!=TF for a,b in zip(ts,ts[1:])):
        raise ValueError("NONCONTIGUOUS_TIME")
    out={}
    for symbol in SYMBOLS:
        f=frames[symbol]
        if f["time"].astype("int64").tolist()!=ts:raise ValueError("MISALIGNED")
        op=f["open"].astype(float);hi=f["high"].astype(float)
        lo=f["low"].astype(float);cl=f["close"].astype(float)
        prev=cl.shift(1)
        tr=pd.concat([hi-lo,(hi-prev).abs(),(lo-prev).abs()],axis=1).max(axis=1)
        atr=tr.rolling(14).mean()
        out[symbol]={"time":ts,"open":op.tolist(),"high":hi.tolist(),"low":lo.tolist(),
                     "close":cl.tolist(),"ema200":cl.ewm(span=200,adjust=False).mean().tolist(),
                     "atr":atr.tolist(),"return60":(cl/cl.shift(60)-1).tolist(),
                     "return20":(cl/cl.shift(20)-1).tolist(),
                     "atr_pct":(atr/cl*100).tolist()}
    return out


def entry_side(a,i,model):
    if i<WARMUP:return 0
    momentum=a["return60"][i]
    previous=a["return60"][i-1]
    if not math.isfinite(momentum) or not math.isfinite(previous):return 0
    side=(1 if momentum>=.03 and previous<.03 else
          -1 if momentum<=-.03 and previous>-.03 else 0)
    if model=="mom60_ema200" and side:
        if side==1 and a["close"][i]<=a["ema200"][i]:return 0
        if side==-1 and a["close"][i]>=a["ema200"][i]:return 0
    if model not in MODELS:raise ValueError("BAD_MODEL")
    return side


def label_at_entry(a,i,side):
    """Strictly ex-ante input features; descriptive tags are not causation."""
    return {
        "trend_alignment":"aligned" if (side==1 and a["close"][i]>a["ema200"][i]) or
                          (side==-1 and a["close"][i]<a["ema200"][i]) else "counter",
        "volatility":"high" if a["atr_pct"][i]>=4 else "normal",
        "signal_strength":"strong" if abs(a["return60"][i])>=.10 else "moderate",
        "recent_conflict":"conflict" if a["return20"][i]*side<0 else "aligned",
        "atr_pct":round(a["atr_pct"][i],4),
        "momentum60_pct":round(a["return60"][i]*100,4),
        "momentum20_pct":round(a["return20"][i]*100,4),
        "utc_hour":pd.to_datetime(a["time"][i],unit="ms",utc=True).hour
    }


def backtest(data,model,start,end):
    equity=10000.
    positions={}
    pending={}
    trades=[]
    time_index=data[SYMBOLS[0]]["time"]
    peak=equity;max_dd=0.
    rejected=0
    for i in range(start,end):
        # Prior candle signals executed at the current bar's open.
        for symbol in sorted(pending):
            if symbol in positions:continue
            if len(positions)>=3:
                rejected+=1;continue
            p=pending[symbol]
            entry=data[symbol]["open"][i]
            stop=p["stop"]
            dist=(entry-stop)*p["side"]
            if entry<=0 or dist<=0:continue
            gross=sum(z["notional"] for z in positions.values())
            notional=min(equity*.01*entry/dist,equity,max(0,2*equity-gross))
            if notional<=0:
                rejected+=1;continue
            positions[symbol]={**p,"entry_price":entry,"opened":i,
                               "entry_ms":time_index[i],"notional":notional}
        pending={}
        for symbol in list(positions):
            p=positions[symbol];a=data[symbol]
            side=p["side"]
            hit=a["low"][i]<=p["stop"] if side==1 else a["high"][i]>=p["stop"]
            due=i-p["opened"]+1>=HOLD_BARS or i==end-1
            if not hit and not due:continue
            price=(min(p["stop"],a["open"][i]) if side==1 else
                   max(p["stop"],a["open"][i])) if hit else a["close"][i]
            pnl=p["notional"]*side*(price/p["entry_price"]-1)-2*COST_SIDE*p["notional"]
            equity+=pnl
            trades.append({"symbol":symbol,"signal_ms":p["signal_ms"],
                           "entry_ms":p["entry_ms"],"exit_ms":time_index[i],
                           "direction":"LONG" if side==1 else "SHORT",
                           "pnl_usdt":round(pnl,5),
                           "net_trade_pct":round(pnl/p["notional"]*100,5),
                           "exit_reason":"STOP" if hit else "TIME",
                           **p["tags"]})
            del positions[symbol]
        marked=equity+sum(p["notional"]*p["side"]*
                  (data[s]["close"][i]/p["entry_price"]-1)-2*COST_SIDE*p["notional"]
                  for s,p in positions.items())
        peak=max(peak,marked)
        max_dd=max(max_dd,1-marked/peak)
        if equity<=0:raise ValueError("EQUITY_EXHAUSTED")
        if i==end-1:break
        for symbol in SYMBOLS:
            if symbol in positions:continue
            a=data[symbol]
            side=entry_side(a,i,model)
            if side:
                pending[symbol]={"side":side,"signal_ms":time_index[i],
                                 "stop":a["close"][i]-side*2*a["atr"][i],
                                 "tags":label_at_entry(a,i,side)}
    return {
        "metrics":{"net_pct":round((equity/10000-1)*100,4),
                   "max_marked_dd_pct":round(max_dd*100,4),
                   "trades":len(trades),
                   "independent_entry_times":len({t["entry_ms"] for t in trades}),
                   "win_rate_pct":round(100*sum(t["pnl_usdt"]>0 for t in trades)/len(trades),2)
                      if trades else None,"rejected_capacity":rejected},
        "trades":trades
    }


def summarize_features(trades):
    results={}
    for feature in ("direction","trend_alignment","volatility",
                    "signal_strength","recent_conflict","utc_hour"):
        groups={}
        for tag in sorted({t[feature] for t in trades},key=str):
            rows=[t for t in trades if t[feature]==tag]
            groups[str(tag)]={"n":len(rows),
                              "entry_time_cohorts":len({r["entry_ms"] for r in rows}),
                              "mean_trade_pct":round(statistics.mean(r["net_trade_pct"] for r in rows),4),
                              "win_pct":round(100*sum(r["pnl_usdt"]>0 for r in rows)/len(rows),2),
                              "total_pnl_usdt":round(sum(r["pnl_usdt"] for r in rows),3)}
        results[feature]=groups
    return results


def evaluate(frames):
    data=features(frames)
    cut=int(BARS*.60)
    output={}
    for split,(start,end) in {"train":(WARMUP,cut),"holdout":(cut+20,BARS)}.items():
        output[split]={}
        for model in MODELS:
            r=backtest(data,model,start,end)
            output[split][model]={"metrics":r["metrics"],
                                  "features":summarize_features(r["trades"]),
                                  "trades":r["trades"]}
    return {"contract":SCHEMA,
            "start_utc":pd.to_datetime(data[SYMBOLS[0]]["time"][0],unit="ms",utc=True).isoformat(),
            "end_utc":pd.to_datetime(data[SYMBOLS[0]]["time"][-1],unit="ms",utc=True).isoformat(),
            "train_end_utc":pd.to_datetime(data[SYMBOLS[0]]["time"][cut-1],unit="ms",utc=True).isoformat(),
            "results":output,
            "limitations":["fixed surviving assets, missing delisted instruments",
                           "funding omitted, possibly optimistic",
                           "feature attribution is association not proof of cause",
                           "conditional group mining invites multiple-testing overfit",
                           "OHLC-only conservative stop gaps, unknown actual fills"]}


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//TF*TF
    frames={s:get_bybit_data_before(s,"4h",boundary,total_limit=BARS) for s in SYMBOLS}
    print(json.dumps(evaluate(frames),sort_keys=True))


if __name__=="__main__":main()
