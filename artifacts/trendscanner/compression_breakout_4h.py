"""Predeclared 4H volatility-contraction breakout comparative research.

Research-only Bybit public OHLC: no credentials, no trading, no DB writes.
Signals use completed 4H candle close. Entry at next candle open.
Contraction is measured BEFORE breakout candle, avoiding breakout contamination.
Previously inspected 2024-2026 history: exploratory comparison, NOT blind OOS.
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

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from prospective_market_data import get_bybit_data_before

TF=14_400_000
BARS=4800
SYMBOLS=("BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT","XRP/USDT",
         "DOGE/USDT","ADA/USDT","LINK/USDT","LTC/USDT","BCH/USDT",
         "AVAX/USDT","DOT/USDT")
MODES=("breakout20","compression_breakout","compression_ema200")
COST_SIDE=.0008
MAX_HOLD=6
WARMUP=250
COMPRESSION_RATIO=.75
CONTRACT={
    "hypothesis":"A 20-bar breakout after prior compression outperforms a plain 20-bar breakout",
    "data":"public Bybit USDT linear perpetual 4h candles; 12 fixed major instruments, 4800 bars",
    "entry":"4H close outside previous 20-bar high/low (not current bar), next 4H open",
    "breakout20":"any 20-bar close breakout regardless of prior volatility",
    "compression_breakout":"same breakout only if mean TR of prior 10 bars <=75% of mean TR of prior 60 bars",
    "compression_ema200":"same contraction, LONG above EMA200 or SHORT below EMA200 on signal close",
    "exit":"initial 2ATR14 stop from signal close, else sixth held bar close; never use future to enter",
    "capacity":"max 3 positions and 2x gross equity; max 1x notional per asset; size for 1% stop risk",
    "cost":"8 basis points fees/slippage each side, funding omitted",
    "split":"first 60% train, 20-bar embargo, remaining 40% holdout, reset positions",
    "integrity":"past 2024-2026 dataset has been explored in earlier experiments: later segment is NOT pristine blind holdout",
    "verdict":"only future/unseen markets and funding stress can validate; this run screens a hypothesis"
}


def features(frames):
    if set(frames)!=set(SYMBOLS):raise ValueError("BAD_UNIVERSE")
    times=frames[SYMBOLS[0]]["time"].astype("int64").tolist()
    if len(times)!=BARS or any(b-a!=TF for a,b in zip(times,times[1:])):
        raise ValueError("NONCONSECUTIVE_DATA")
    out={}
    for sym in SYMBOLS:
        f=frames[sym]
        if f["time"].astype("int64").tolist()!=times:raise ValueError("MISALIGNED")
        op=f["open"].astype(float);cl=f["close"].astype(float)
        hi=f["high"].astype(float);lo=f["low"].astype(float)
        prev=cl.shift(1)
        tr=pd.concat([hi-lo,(hi-prev).abs(),(lo-prev).abs()],axis=1).max(axis=1)
        avg10=tr.shift(1).rolling(10).mean()
        avg60=tr.shift(1).rolling(60).mean()
        out[sym]={
            "time":times,"open":op.tolist(),"close":cl.tolist(),"high":hi.tolist(),"low":lo.tolist(),
            "upper20":hi.shift(1).rolling(20).max().tolist(),
            "lower20":lo.shift(1).rolling(20).min().tolist(),
            "compression":(avg10/avg60).tolist(),
            "ema200":cl.ewm(span=200,adjust=False).mean().tolist(),
            "atr14":tr.rolling(14).mean().tolist()
        }
    return out


def side_at_close(a,i,mode):
    if mode not in MODES:raise ValueError("UNKNOWN_MODE")
    if i<WARMUP:return 0
    price=a["close"][i]
    atr=a["atr14"][i]
    compress=a["compression"][i]
    if not all(math.isfinite(z) and z>0 for z in (price,atr,compress)):return 0
    side=1 if price>a["upper20"][i] else -1 if price<a["lower20"][i] else 0
    if not side:return 0
    # The last ten bars all end at i-1; the breakout bar i cannot affect ratio.
    if mode!="breakout20" and compress>COMPRESSION_RATIO:return 0
    if mode=="compression_ema200" and (price-a["ema200"][i])*side<=0:return 0
    return side


def replay(data,mode,start,end):
    equity=10000.
    positions={}
    pending={}
    trades=[]
    peak=equity
    dd=0.
    passed=0
    skipped=0
    time_index=data[SYMBOLS[0]]["time"]
    for i in range(start,end):
        for sym in sorted(pending):
            if sym in positions:continue
            if len(positions)>=3:
                skipped+=1
                continue
            c=pending[sym]
            a=data[sym]
            entry=a["open"][i]
            stop=c["stop"]
            distance=(entry-stop)*c["side"]
            if distance<=0 or entry<=0:
                skipped+=1
                continue
            gross=sum(p["notional"] for p in positions.values())
            n=min(equity*.01*entry/distance,equity,max(0,2*equity-gross))
            if n<=0:
                skipped+=1
                continue
            positions[sym]={**c,"entry":entry,"opened":i,
                            "notional":n,"entry_ms":time_index[i]}
            passed+=1
        pending={}
        for sym in list(positions):
            a=data[sym];p=positions[sym]
            hit=a["low"][i]<=p["stop"] if p["side"]==1 else a["high"][i]>=p["stop"]
            due=i-p["opened"]+1>=MAX_HOLD or i==end-1
            if not hit and not due:continue
            price=(min(p["stop"],a["open"][i]) if p["side"]==1 else
                   max(p["stop"],a["open"][i])) if hit else a["close"][i]
            pnl=p["notional"]*((price/p["entry"]-1)*p["side"]-2*COST_SIDE)
            equity+=pnl
            trades.append({"symbol":sym,"side":p["side"],"signal_ms":p["signal_ms"],
                           "entry_ms":p["entry_ms"],"pnl":pnl,
                           "pct":pnl/p["notional"]*100,"stop":hit})
            del positions[sym]
        marked=equity+sum(p["notional"]*((data[s]["close"][i]/p["entry"]-1)*p["side"]-2*COST_SIDE)
                          for s,p in positions.items())
        peak=max(peak,marked)
        dd=max(dd,1-marked/peak)
        if equity<=0:raise ValueError("EQUITY_EXHAUSTED")
        if i==end-1:break
        for sym in SYMBOLS:
            if sym in positions:continue
            a=data[sym]
            side=side_at_close(a,i,mode)
            if side:
                pending[sym]={"side":side,"stop":a["close"][i]-2*side*a["atr14"][i],
                              "signal_ms":time_index[i]}
    return {
        "net_return_pct":round((equity/10000-1)*100,3),
        "max_marked_drawdown_pct":round(dd*100,3),
        "trades":len(trades),"signal_time_cohorts":len({x["signal_ms"] for x in trades}),
        "win_rate_pct":round(100*sum(t["pnl"]>0 for t in trades)/len(trades),2) if trades else None,
        "mean_trade_net_pct":round(statistics.mean(t["pct"] for t in trades),4) if trades else None,
        "longs":sum(t["side"]==1 for t in trades),
        "shorts":sum(t["side"]==-1 for t in trades),
        "stops":sum(t["stop"] for t in trades),
        "capacity_or_gap_skips":skipped,
        "executed_entries":passed
    }


def evaluate(frames):
    a=features(frames)
    cut=int(BARS*.60)
    results={}
    for split,(s,e) in {"train":(WARMUP,cut),"later":(cut+20,BARS)}.items():
        results[split]={mode:replay(a,mode,s,e) for mode in MODES}
    return {
        "contract":CONTRACT,"dates":{
            "start":pd.to_datetime(a[SYMBOLS[0]]["time"][0],unit="ms",utc=True).isoformat(),
            "train_last":pd.to_datetime(a[SYMBOLS[0]]["time"][cut-1],unit="ms",utc=True).isoformat(),
            "later_last":pd.to_datetime(a[SYMBOLS[0]]["time"][-1],unit="ms",utc=True).isoformat()},
        "results":results,
        "historical_research_only":True,
        "warning":"These same historical years have informed prior research. No result here proves out-of-sample edge."
    }


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//TF*TF
    frames={s:get_bybit_data_before(s,"4h",boundary,total_limit=BARS)
            for s in SYMBOLS}
    print(json.dumps(evaluate(frames),sort_keys=True))

if __name__=="__main__":main()
