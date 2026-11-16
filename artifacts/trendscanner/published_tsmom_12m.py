"""Pre-registered, published 12-month time-series momentum family on crypto.

Moskowitz, Ooi & Pedersen (2012), JFE 104:228-250, DOI 10.1016/j.jfineco.2011.11.003
Original paper: sign of past 12-month excess futures return, 1-month holding,
inverse ex-ante volatility sizing. Here: spot-like price-return proxy on 12
Bybit USDT perpetuals, 365 completed daily closes, 30-day holding,
60-day trailing realised volatility, 12% notional portfolio-vol budget
with 15% per-asset and 80% gross exposure caps. NOT exact replication:
no futures excess-return series, monthly calendar convention approximation,
funding/borrow/margin absent, capped risk; explanatory benchmark, not
a proven crypto strategy. No optimization; no executable orders.

Research history has been examined repeatedly: 'later' is NOT blind OOS.
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


def fetch_public_with_transient_retry(symbol,boundary):
    """Retry only temporary Bybit retCode 10016; never mask data integrity errors."""
    for attempt in range(5):
        try:
            return get_bybit_data_before(symbol,"4h",boundary,total_limit=BARS)
        except ValueError as error:
            if str(error)!="BYBIT_PUBLIC_API_ERROR:10016" or attempt==4:
                raise
            time.sleep(5*(attempt+1))
    raise AssertionError("UNREACHABLE")

TF=14_400_000
BARS=9000
SYMBOLS=("BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT","XRP/USDT",
         "DOGE/USDT","ADA/USDT","LINK/USDT","LTC/USDT","BCH/USDT",
         "AVAX/USDT","DOT/USDT")
MODES=("tsmom_long_short","tsmom_long_only","passive_long")
LOOKBACK_DAYS=365
VOL_DAYS=60
HOLD_DAYS=30
COST_SIDE=.0008
ANNUAL_TARGET=.12
ASSET_CAP=.15
GROSS_CAP=.80
CONTRACT={
  "citation":"Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012, DOI 10.1016/j.jfineco.2011.11.003",
  "published_core":"past 12-month return sign; one-month holding; inverse volatility sizing",
  "adaptation":"365 completed daily close-to-close days; rebalance every 30 days at next daily open; 60-day ex ante log-return std; target nominal portfolio volatility 12%, 15% asset and 80% total gross limits",
  "comparators":"same trend rule LONG-only and constant 80%-gross equal-weight passive long",
  "timeframe":"4h Bybit USDT linear perpetual OHLC resampled to complete UTC days",
  "execution":"signals use only yesterday's completed daily closes, rebalance at today's open; old positions carry overnight open gaps; mark-to-market at daily close",
  "costs":"8bp traded notional per side including modeled fee+slippage, plus no funding or spread regime stress",
  "risk":"unlevered <=0.8x gross, no trading/instrument-level liquidation modeling, no stops",
  "validation":"all historical epochs previously investigated, this is exploratory; no deployment before untouched forward observations"
}


def to_daily(frames):
    if set(frames)!=set(SYMBOLS):raise ValueError("SYMBOLS_MISMATCH")
    ref=frames[SYMBOLS[0]]["time"].astype("int64").tolist()
    if len(ref)!=BARS or any(b-a!=TF for a,b in zip(ref,ref[1:])):
        raise ValueError("NONCONSECUTIVE_4H")
    if ref[0]%(TF*6)!=0:raise ValueError("NON_UTC_DAY_START")
    result={}
    for sym in SYMBOLS:
        f=frames[sym]
        if f["time"].astype("int64").tolist()!=ref:raise ValueError("TIME_MISMATCH")
        op=f["open"].astype(float).tolist()
        cl=f["close"].astype(float).tolist()
        n=BARS//6
        result[sym]={"time":[ref[i*6] for i in range(n)],
                     "open":[op[i*6] for i in range(n)],
                     "close":[cl[i*6+5] for i in range(n)]}
    return result


def weights_at_previous_close(data,asof,mode):
    if mode not in MODES:raise ValueError("BAD_MODE")
    if asof<LOOKBACK_DAYS or asof<VOL_DAYS:return {s:0. for s in SYMBOLS}
    if mode=="passive_long":
        return {s:GROSS_CAP/len(SYMBOLS) for s in SYMBOLS}
    proposed={}
    for sym in SYMBOLS:
        closes=data[sym]["close"]
        direction=1 if closes[asof]>closes[asof-LOOKBACK_DAYS] else -1
        if mode=="tsmom_long_only" and direction<0:direction=0
        rets=[math.log(closes[k]/closes[k-1]) for k in range(asof-VOL_DAYS+1,asof+1)]
        sigma=statistics.stdev(rets)*math.sqrt(365)
        weight=min(ASSET_CAP,ANNUAL_TARGET/(max(sigma,1e-6)*math.sqrt(len(SYMBOLS))))
        proposed[sym]=direction*weight
    gross=sum(abs(z) for z in proposed.values())
    if gross>GROSS_CAP:
        proposed={s:w*GROSS_CAP/gross for s,w in proposed.items()}
    return proposed


def replay(data,mode,start,end):
    if start<=LOOKBACK_DAYS or end>len(data[SYMBOLS[0]]["time"]):
        raise ValueError("BAD_WINDOW")
    equity=10000.
    peak=equity
    maxdd=0.
    qty={s:0. for s in SYMBOLS}
    turns=[]
    daily=[]
    rebalances=0
    for i in range(start,end):
        # Prior position holds overnight. No "close-only" gap is skipped.
        for sym in SYMBOLS:
            equity+=qty[sym]*(data[sym]["open"][i]-data[sym]["close"][i-1])
        if (i-start)%HOLD_DAYS==0:
            w=weights_at_previous_close(data,i-1,mode)
            # Target contracts at today's OPEN based only on prior close info.
            # Sell/buy notionals are computed from open-marked portfolio value.
            target={sym:equity*w[sym]/data[sym]["open"][i] for sym in SYMBOLS}
            gross_trade=sum(abs((target[sym]-qty[sym])*data[sym]["open"][i]) for sym in SYMBOLS)
            equity-=gross_trade*COST_SIDE
            qty=target
            turns.append(gross_trade/10000.)
            rebalances+=1
        for sym in SYMBOLS:
            equity+=qty[sym]*(data[sym]["close"][i]-data[sym]["open"][i])
        if equity<=0:raise ValueError("NEGATIVE_EQUITY")
        peak=max(peak,equity)
        maxdd=max(maxdd,1-equity/peak)
        daily.append(equity)
    # Close at last finished bar for comparable realised gain and exposure.
    liquidation=sum(abs(qty[sym]*data[sym]["close"][end-1]) for sym in SYMBOLS)*COST_SIDE
    equity-=liquidation
    maxdd=max(maxdd,1-equity/peak)
    if daily:daily[-1]=equity
    changes=[daily[k]/daily[k-1]-1 for k in range(1,len(daily))]
    ann_vol=statistics.stdev(changes)*math.sqrt(365)*100 if len(changes)>2 else None
    return {"net_return_pct":round(100*(equity/10000-1),3),
            "annualized_return_pct":round(100*((equity/10000)**(365/(end-start))-1),3),
            "max_marked_drawdown_pct":round(maxdd*100,3),
            "annualized_realized_vol_pct":round(ann_vol,3) if ann_vol is not None else None,
            "trading_days":end-start,"rebalances":rebalances,
            "total_rebalance_turnover_initial_equity":round(sum(turns),4),
            "liquidation_included":True,
            "fee_bps_per_side":round(COST_SIDE*10000,2)}


def evaluate(frames):
    data=to_daily(frames)
    N=len(data[SYMBOLS[0]]["time"])
    cut=int(N*.6)
    train_start=LOOKBACK_DAYS+VOL_DAYS+1
    windows={"train":(train_start,cut),"later":(cut+20,N)}
    results={}
    for period,(start,end) in windows.items():
        results[period]={mode:replay(data,mode,start,end) for mode in MODES}
    return {"contract":CONTRACT,
       "range":{"start_utc":pd.to_datetime(data[SYMBOLS[0]]["time"][0],unit="ms",utc=True).isoformat(),
                "train_end_utc":pd.to_datetime(data[SYMBOLS[0]]["time"][cut-1],unit="ms",utc=True).isoformat(),
                "later_end_utc":pd.to_datetime(data[SYMBOLS[0]]["time"][-1],unit="ms",utc=True).isoformat()},
       "results":results,
       "status":"RESEARCH_ONLY_REQUIRE_BLIND_FORWARD_AND_FUNDING_VALIDATION"}


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//(TF*6)*(TF*6)
    frames={s:fetch_public_with_transient_retry(s,boundary) for s in SYMBOLS}
    print(json.dumps(evaluate(frames),sort_keys=True))

if __name__=="__main__":main()
