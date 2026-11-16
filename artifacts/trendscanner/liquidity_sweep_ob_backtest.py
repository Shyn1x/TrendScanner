"""Frozen public-only 4H crypto liquidity-sweep / order-block historical test.

All decisions use CLOSED candles, with next candle open for entry.
OHLC candles cannot establish actual resting liquidity or institutional orders:
"order block" is a mechanical candle-body proxy, not observed order flow.
No credentials, database writes, or order execution.
"""
from __future__ import annotations
import json
import math
import os
import statistics
import time
from pathlib import Path
import sys
import pandas as pd

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from prospective_market_data import get_bybit_data_before

TF=14_400_000
BARS=4800
SYMBOLS=("BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT",
         "XRP/USDT","DOGE/USDT","ADA/USDT","LINK/USDT",
         "LTC/USDT","BCH/USDT","AVAX/USDT","DOT/USDT")
MODES=("ema_pullback","sweep","sweep_ob")
FEE_SLIP_SIDE=.0008
LOOKBACK_SWEEP=10
LOOKBACK_OB=8
HOLD_BARS=6
TRAIN_SHARE=.60
WARMUP=250
CONTRACT={
    "market":"Bybit linear perpetual trade OHLC, public only","timeframe":"4h",
    "symbols":SYMBOLS,"bars":BARS,
    "direction":"asset EMA50 > EMA200: LONG, EMA50 < EMA200: SHORT",
    "ema_pullback":"touch EMA20; candle close back above/below EMA20 with bullish/bearish body",
    "sweep":"wick breaches previous 10-bar low/high; close recaptures prior level with reversal body",
    "sweep_ob":"same sweep plus retest of prior opposite-body candle with subsequent 0.5ATR close displacement before the sweep",
    "ob_definition":"bullish [low,open] of prior bearish candle followed by a close > its high +0.5ATR; bearish [open,high] of prior bullish candle followed by close < its low -0.5ATR; sweep wick revisits zone",
    "entry":"next bar open only; deterministic symbol sort for capacity priority",
    "stop":"2x ATR14 from signal close; stop at worst of stop or bar open for gaps",
    "exit":"stop first or 6 closed 4H candles, exit on close of sixth candle",
    "cost":"0.08% fees/slippage each side; funding not included",
    "risk":"1% current equity nominal risk to initial stop; 1x equity cap per asset, max 3 positions, 2x aggregate notional",
    "split":"first 60% training, last 40% untouched chronological holdout; 20-bar embargo, flat reset",
    "decision":"candidate only if train positive, holdout >=3%, beats both comparators, <=20% max marked drawdown, >=30 holdout entry-time cohorts"
}


def make_features(frames):
    if set(frames)!=set(SYMBOLS):
        raise ValueError("UNIVERSE_MISMATCH")
    times=frames[SYMBOLS[0]]["time"].astype("int64").tolist()
    if len(times)!=BARS or any(y-x!=TF for x,y in zip(times,times[1:])):
        raise ValueError("INVALID_BASE_CANDLES")
    out={}
    for sym in SYMBOLS:
        f=frames[sym]
        if f["time"].astype("int64").tolist()!=times:
            raise ValueError("MISALIGNED_CANDLES")
        close=f["close"].astype(float)
        high=f["high"].astype(float)
        low=f["low"].astype(float)
        opened=f["open"].astype(float)
        prev=close.shift()
        tr=pd.concat([(high-low),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1)
        out[sym]={
            "time":times,"open":opened.tolist(),"high":high.tolist(),"low":low.tolist(),
            "close":close.tolist(),"ema20":close.ewm(span=20,adjust=False).mean().tolist(),
            "ema50":close.ewm(span=50,adjust=False).mean().tolist(),
            "ema200":close.ewm(span=200,adjust=False).mean().tolist(),
            "atr":tr.rolling(14).mean().tolist(),
            "prev_low10":low.shift(1).rolling(LOOKBACK_SWEEP).min().tolist(),
            "prev_high10":high.shift(1).rolling(LOOKBACK_SWEEP).max().tolist()
        }
    return out


def signal(a,i,mode):
    """Pure signal from bars <= i, no information from entry bar i+1."""
    if i<WARMUP: return 0
    close,open_,low,high=[a[k][i] for k in ("close","open","low","high")]
    if not all(math.isfinite(x) for x in (close,open_,low,high,a["atr"][i])):
        return 0
    long=a["ema50"][i]>a["ema200"][i]
    short=a["ema50"][i]<a["ema200"][i]
    if mode=="ema_pullback":
        e=a["ema20"][i]
        if long and low<=e and close>e and close>open_: return 1
        if short and high>=e and close<e and close<open_: return -1
        return 0
    support,resistance=a["prev_low10"][i],a["prev_high10"][i]
    bullish=long and low<support and close>support and close>open_
    bearish=short and high>resistance and close<resistance and close<open_
    if not (bullish or bearish): return 0
    side=1 if bullish else -1
    if mode=="sweep":return side
    if mode!="sweep_ob":raise ValueError("UNKNOWN_MODE")
    # A proxy OB must be formed BEFORE the sweep, followed by a confirmed
    # impulse BEFORE the sweep. A mere opposite-colored candle is not enough.
    for j in range(i-2,i-LOOKBACK_OB-1,-1):
        op,cl,lo,hi=[a[k][j] for k in ("open","close","low","high")]
        if side==1 and cl<op:
            displaced=any(a["close"][k]>hi+0.5*a["atr"][j]
                          for k in range(j+1,i))
            if displaced and low<=op and high>=lo:return side
        if side==-1 and cl>op:
            displaced=any(a["close"][k]<lo-0.5*a["atr"][j]
                          for k in range(j+1,i))
            if displaced and high>=op and low<=hi:return side
    return 0


def replay(data,mode,start,end):
    capital=10000.
    positions={}
    pending={}
    cohort_entries=set()
    trades=[]
    rejections={"capacity":0,"bad_stop_or_gap":0}
    peak=capital
    max_dd=0.
    times=data[SYMBOLS[0]]["time"]
    for i in range(start,end):
        # Previous close -> current open. Each instrument at most once.
        for sym in sorted(pending):
            if sym in positions:continue
            if len(positions)>=3:
                rejections["capacity"]+=1;continue
            side,stop=pending[sym]
            a=data[sym];entry=a["open"][i]
            distance=(entry-stop)*side
            if entry<=0 or distance<=0:
                rejections["bad_stop_or_gap"]+=1;continue
            gross_used=sum(p["notional"] for p in positions.values())
            gross_cap=max(0,2*max(capital,0)-gross_used)
            notional=min(capital*.01*entry/distance,capital,gross_cap)
            if notional<=0:
                rejections["capacity"]+=1;continue
            positions[sym]={"side":side,"stop":stop,"entry":entry,"notional":notional,
                            "opened":i,"entry_time":times[i]}
            cohort_entries.add(times[i])
        pending={}
        # Existing positions: stop can fill intrabar, otherwise fixed 6-bar exit.
        for sym in list(positions):
            a=data[sym];p=positions[sym]
            side=p["side"];stop=p["stop"];op=a["open"][i]
            hit=a["low"][i]<=stop if side==1 else a["high"][i]>=stop
            timed=i-p["opened"]+1>=HOLD_BARS or i==end-1
            if hit or timed:
                price=(min(stop,op) if side==1 else max(stop,op)) if hit else a["close"][i]
                pnl=p["notional"]*side*(price/p["entry"]-1)-2*FEE_SLIP_SIDE*p["notional"]
                capital+=pnl
                trades.append({"entry_ms":p["entry_time"],"exit_ms":times[i],"side":side,
                               "symbol":sym,"pnl":pnl,"return_pct":pnl/p["notional"]*100,
                               "reason":"stop" if hit else ("split_end" if i==end-1 else "timed")})
                del positions[sym]
        marked=capital
        for sym,p in positions.items():
            price=data[sym]["close"][i]
            marked+=p["notional"]*p["side"]*(price/p["entry"]-1)-2*FEE_SLIP_SIDE*p["notional"]
        peak=max(peak,marked)
        max_dd=max(max_dd,1-marked/peak)
        if capital<=0:raise ValueError("EQUITY_EXHAUSTED")
        if i>=end-1:break
        for sym in SYMBOLS:
            if sym in positions:continue
            a=data[sym];side=signal(a,i,mode)
            if side:
                stop=a["close"][i]-side*2*a["atr"][i]
                pending[sym]=(side,stop)
    returns=[t["return_pct"] for t in trades]
    return {"net_return_pct":round((capital/10000-1)*100,3),
            "end_equity_usdt":round(capital,2),"max_marked_drawdown_pct":round(max_dd*100,3),
            "trades":len(trades),"entry_time_cohorts":len(cohort_entries),
            "win_rate_pct":round(sum(t["pnl"]>0 for t in trades)/len(trades)*100,2) if trades else None,
            "mean_trade_net_pct":round(statistics.mean(returns),4) if returns else None,
            "long_trades":sum(t["side"]==1 for t in trades),
            "short_trades":sum(t["side"]==-1 for t in trades),
            "stopped":sum(t["reason"]=="stop" for t in trades),
            "rejections":rejections}


def verdict(train,hold):
    for mode in MODES:
        if hold[mode]["entry_time_cohorts"]<30:return "INCONCLUSIVE_FEW_HOLDOUT_COHORTS"
    ob=hold["sweep_ob"]
    if (train["sweep_ob"]["net_return_pct"]<=0 or ob["net_return_pct"]<3
        or ob["net_return_pct"]<=max(hold["sweep"]["net_return_pct"],hold["ema_pullback"]["net_return_pct"])
        or ob["max_marked_drawdown_pct"]>20):
        return "REJECT_ORDER_BLOCK_ADDS_NO_ROBUST_ADVANTAGE"
    return "RESEARCH_CANDIDATE_ONLY_REQUIRES_REPLICATION"


def evaluate(frames):
    d=make_features(frames)
    split=int(BARS*TRAIN_SHARE)
    result={}
    for label,(start,end) in {"train":(WARMUP,split),
                              "holdout":(split+20,BARS)}.items():
        result[label]={mode:replay(d,mode,start,end) for mode in MODES}
    return {"contract":CONTRACT,
            "start_utc":pd.to_datetime(d[SYMBOLS[0]]["time"][0],unit="ms",utc=True).isoformat(),
            "end_utc":pd.to_datetime(d[SYMBOLS[0]]["time"][-1],unit="ms",utc=True).isoformat(),
            "train_end_utc":pd.to_datetime(d[SYMBOLS[0]]["time"][split-1],unit="ms",utc=True).isoformat(),
            "results":result,"verdict":verdict(result["train"],result["holdout"]),
            "limitations":["Order blocks and liquidity inferred from candles only, not actual orders",
                           "4H signal/entry; no 1H execution confirmation",
                           "Funding omitted; results optimistic for perpetuals",
                           "Fixed 12-symbol survivor universe; excludes delistings",
                           "Stop fills conservative for gaps but historical intrabar path unknown",
                           "No walk-forward parameter search; parameters frozen before holdout"]}


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//TF*TF
    frames={s:get_bybit_data_before(s,"4h",boundary,total_limit=BARS) for s in SYMBOLS}
    print(json.dumps(evaluate(frames),sort_keys=True))


if __name__=="__main__":main()
