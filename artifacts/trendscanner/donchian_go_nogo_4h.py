"""Predeclared walk-forward 4h Donchian GO/NO-GO test; public Bybit OHLC only.

Three frozen comparisons use identical next-bar-open execution, ATR2 stop,
10-bar channel exit, 1% per position risk, max 3 positions, 2x gross leverage
cap, 8bps fees+slippage per side. Funding excluded (optimistic bias).
All metrics are simulated and no orders or DB mutation is possible.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from prospective_market_data import get_bybit_data_before

TF = 14_400_000
BARS = 3600
SYMBOLS = ("BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT",
           "XRP/USDT","DOGE/USDT","ADA/USDT","LINK/USDT",
           "LTC/USDT","BCH/USDT","AVAX/USDT","DOT/USDT")
COST_SIDE = .0008  # 6bp fee + 2bp slippage per side
TRAIN_SHARE = .60
WARMUP = 250
MIN_COHORTS = 30
CONTRACT = {
    "market":"Bybit linear USDT perpetual trade bars", "timeframe":"4h",
    "universe":SYMBOLS, "bars":BARS,"regime":"BTC EMA200 and 12-symbol breadth EMA50/20-bar return",
    "entry":"close crosses prior20 high/low; next bar open; no intra-bar breakout entries",
    "exit":"close crosses opposite prior10; exit next bar open; 2 ATR initial hard stop within bars",
    "risk":"1pct stop distance per new position capped at 1x equity notional; max3 positions; gross<=2x",
    "cost":"8bp per side; funding absent; conservative gap stops",
    "splits":"first 60% train, last 40% holdout; 20-bar embargo; flat reset on splits",
    "models":("regime_donchian","no_regime_donchian","short_only_donchian"),
}


def features(frames):
    if len(frames)!=len(SYMBOLS):
        raise ValueError("MISSING_UNIVERSE")
    times = frames[SYMBOLS[0]]["time"].astype("int64").tolist()
    for sym in SYMBOLS:
        f=frames[sym]
        if len(f)!=len(times) or f["time"].astype("int64").tolist()!=times:
            raise ValueError("NONCONTIGUOUS_OR_MISALIGNED_UNIVERSE")
        if any(b-a!=TF for a,b in zip(times,times[1:])):
            raise ValueError("NONCONTIGUOUS_CANDLES")
    arrays={}
    for symbol,frame in frames.items():
        close=frame["close"].astype(float)
        high=frame["high"].astype(float)
        low=frame["low"].astype(float)
        prev=close.shift(1)
        tr=pd.concat([high-low,(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1)
        arrays[symbol]={
            "time":times,
            "open":frame["open"].astype(float).tolist(),
            "high":high.tolist(), "low":low.tolist(), "close":close.tolist(),
            "ema50":close.ewm(span=50,adjust=False).mean().tolist(),
            "ema200":close.ewm(span=200,adjust=False).mean().tolist(),
            "ret20":(close/close.shift(20)-1).tolist(),
            "upper20":high.shift(1).rolling(20).max().tolist(),
            "lower20":low.shift(1).rolling(20).min().tolist(),
            "upper10":high.shift(1).rolling(10).max().tolist(),
            "lower10":low.shift(1).rolling(10).min().tolist(),
            "atr":tr.rolling(14).mean().tolist()
        }
    regime=[]
    for i in range(len(times)):
        if i<WARMUP:
            regime.append("MIXED")
            continue
        rows=[arrays[s] for s in SYMBOLS]
        above=sum(a["close"][i]>a["ema50"][i] for a in rows)/len(rows)
        med=statistics.median(a["ret20"][i] for a in rows)
        btc=arrays["BTC/USDT"]
        regime.append("BULL" if btc["close"][i]>btc["ema200"][i] and above>=.60 and med>0
                      else "BEAR" if btc["close"][i]<btc["ema200"][i] and above<=.40 and med<0
                      else "MIXED")
    return arrays,regime


def replay(arrays,regime,start,end,mode):
    """No lookahead: signals at close i, execution at open i+1."""
    capital=10000.0
    peak=capital
    dd=0.0
    positions={}
    pending={}
    trades=[]
    reject={"correlation_capacity":0,"invalid_stop_gap":0}
    times=arrays[SYMBOLS[0]]["time"]
    for i in range(start,end):
        # Execute prior close's exits at next open, before new entries.
        for sym in list(positions):
            p=positions[sym]
            a=arrays[sym]
            if p["exit_next"]:
                price=a["open"][i]
                pnl=p["notional"]*(price/p["entry"]-1)*p["side"]-2*COST_SIDE*p["notional"]
                capital+=pnl
                trades.append({"symbol":sym,"entry_time":p["time"],"exit_time":times[i],
                               "side":p["side"],"pnl":pnl,"return_pct":pnl/p["notional"]*100,
                               "reason":"channel"})
                del positions[sym]
        # Freeze candidates at previous close. Never exceed 3 positions.
        for sym in sorted(pending):
            if sym in positions:
                continue
            if len(positions)>=3:
                reject["correlation_capacity"]+=1
                continue
            side,stop=pending[sym]
            a=arrays[sym]
            entry=a["open"][i]
            dist=(entry-stop)*side
            if entry<=0 or dist<=0:
                reject["invalid_stop_gap"]+=1
                continue
            equity=max(capital,0)
            current_gross=sum(p["notional"] for p in positions.values())
            notional=min(equity*.01*entry/dist,equity, max(0,2*equity-current_gross))
            if notional<=0:
                reject["correlation_capacity"]+=1
                continue
            positions[sym]={"side":side,"stop":stop,"entry":entry,
                            "time":times[i],"notional":notional,"exit_next":False}
        pending={}
        # Stops on current candle. Intrabar gaps are worse than stop.
        for sym in list(positions):
            p=positions[sym];a=arrays[sym];s=p["side"];stop=p["stop"]
            hit=a["low"][i]<=stop if s==1 else a["high"][i]>=stop
            if hit:
                price=min(stop,a["open"][i]) if s==1 else max(stop,a["open"][i])
                pnl=p["notional"]*(price/p["entry"]-1)*s-2*COST_SIDE*p["notional"]
                capital+=pnl
                trades.append({"symbol":sym,"entry_time":p["time"],"exit_time":times[i],
                               "side":s,"pnl":pnl,"return_pct":pnl/p["notional"]*100,
                               "reason":"stop"})
                del positions[sym]
        if capital<=0:
            raise ValueError("NONPOSITIVE_EQUITY")
        peak=max(peak,capital)
        dd=max(dd,1-capital/peak)
        if i==end-1:
            break
        # Use only closed candle i for next candle's decisions.
        for sym in SYMBOLS:
            a=arrays[sym];c=a["close"][i]
            if sym in positions:
                p=positions[sym]
                p["exit_next"]=c<a["lower10"][i] if p["side"]==1 else c>a["upper10"][i]
                continue
            up=c>a["upper20"][i]
            down=c<a["lower20"][i]
            allowed_long=mode!="short_only_donchian" and (mode=="no_regime_donchian" or regime[i]=="BULL")
            allowed_short=mode!="regime_donchian" or regime[i]=="BEAR"
            if up and allowed_long:
                pending[sym]=(1,c-2*a["atr"][i])
            elif down and allowed_short:
                pending[sym]=(-1,c+2*a["atr"][i])
    # Flat-at-end valuation at last bar close, including two-sided costs.
    for sym,p in positions.items():
        exit_price=arrays[sym]["close"][end-1]
        pnl=p["notional"]*(exit_price/p["entry"]-1)*p["side"]-2*COST_SIDE*p["notional"]
        capital+=pnl
        trades.append({"symbol":sym,"entry_time":p["time"],"exit_time":times[end-1],
                       "side":p["side"],"pnl":pnl,"return_pct":pnl/p["notional"]*100,
                       "reason":"split_end_mark"})
    peak=max(peak,capital)
    dd=max(dd,1-capital/peak)
    wins=sum(t["pnl"]>0 for t in trades)
    return {"end_equity_usdt":round(capital,2),"net_return_pct":round((capital/10000-1)*100,3),
            "max_realized_drawdown_pct":round(dd*100,3),"trades":len(trades),
            "entry_time_cohorts":len(set(t["entry_time"] for t in trades)),
            "win_rate_pct":round(wins/len(trades)*100,2) if trades else None,
            "long_trades":sum(t["side"]==1 for t in trades),
            "short_trades":sum(t["side"]==-1 for t in trades),
            "stop_exits":sum(t["reason"]=="stop" for t in trades),
            "capacity_rejections":reject}


def decision(holdout):
    a=holdout["regime_donchian"]
    b=holdout["no_regime_donchian"]
    c=holdout["short_only_donchian"]
    if a["entry_time_cohorts"]<MIN_COHORTS:
        return "INCONCLUSIVE_INSUFFICIENT_INDEPENDENT_ENTRIES"
    if a["net_return_pct"]<=0 or a["net_return_pct"]<=max(b["net_return_pct"],c["net_return_pct"]):
        return "REJECT_NO_AFTER_COST_ADVANTAGE_VS_SIMPLE_BASELINES"
    if a["max_realized_drawdown_pct"]>=20:
        return "REJECT_DRAWDOWN_TOO_HIGH"
    return "RESEARCH_CANDIDATE_ONLY_NOT_LIVE_APPROVED"


def evaluate(frames):
    arrays,regime=features(frames)
    n=len(regime)
    cut=int(n*TRAIN_SHARE)
    if cut<700 or n-cut<500:
        raise ValueError("INSUFFICIENT_TRAIN_HOLDOUT_BARS")
    splits={"train":(WARMUP,cut),"holdout":(cut+20,n)}
    results={}
    for split,(start,end) in splits.items():
        results[split]={mode:replay(arrays,regime,start,end,mode)
                        for mode in CONTRACT["models"]}
    return {"contract":CONTRACT,"start_utc":pd.to_datetime(arrays[SYMBOLS[0]]["time"][0],unit="ms",utc=True).isoformat(),
            "end_utc":pd.to_datetime(arrays[SYMBOLS[0]]["time"][-1],unit="ms",utc=True).isoformat(),
            "train_end_utc":pd.to_datetime(arrays[SYMBOLS[0]]["time"][cut-1],unit="ms",utc=True).isoformat(),
            "market_regime_bars":{r:regime.count(r) for r in ("BULL","BEAR","MIXED")},
            "results":results,"verdict":decision(results["holdout"]),
            "limitations":["12 older assets instead of the prospective 25",
                           "fixed survivor universe and no delisted assets",
                           "funding/borrow omitted: performance optimistic",
                           "OHLC stop intrabar only, gap conservative; no orderbook/slippage stress",
                           "realized-drawdown only, not mark-to-market intraday",
                           "past-only signals, no regime lookahead; split positions reset"]}


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    now=int(time.time()*1000)
    boundary=now//TF*TF
    frames={sym:get_bybit_data_before(sym,"4h",boundary,total_limit=BARS)
            for sym in SYMBOLS}
    print(json.dumps(evaluate(frames),sort_keys=True))


if __name__=="__main__":
    main()
