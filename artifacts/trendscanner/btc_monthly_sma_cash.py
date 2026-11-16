"""Research-only BTC monthly 10-month moving average tactical timing.

Published starting point: Mebane Faber (2007) A Quantitative Approach to
Tactical Asset Allocation; monthly price > trailing 10 monthly closes -> hold,
else cash. BTC crypto adaptation: 0% cash yield, USDt perpetual trade-price
proxy, no funding; charges 8bps per traded notional. Monthly close signals,
next UTC day open fills, no intramonth signals. Do not optimize.
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import sys
import math
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from prospective_market_data import get_bybit_data_before

TF=14_400_000
DAY=6*TF
BARS=9000
COST=.0008
MODES=("passive_btc","monthly_sma_full","monthly_sma_half")

def daily(frame):
    times=frame["time"].astype("int64").tolist()
    if len(times)!=BARS or times[0]%DAY:
        raise ValueError("PARTIAL_DAILY_WINDOW")
    if any(b-a!=TF for a,b in zip(times,times[1:])):
        raise ValueError("MISSING_CANDLE")
    opens=frame["open"].astype(float).tolist()
    closes=frame["close"].astype(float).tolist()
    return [{"ts":times[i],"open":opens[i],"close":closes[i+5]}
            for i in range(0,len(times),6)]

def month_signals(days):
    closes={}
    last_day_idx={}
    for i,d in enumerate(days):
        stamp=datetime.fromtimestamp(d["ts"]/1000,tz=timezone.utc)
        k=(stamp.year,stamp.month)
        closes[k]=d["close"]
        last_day_idx[k]=i
    month_keys=sorted(closes)
    signals={}
    for k in range(10,len(month_keys)):
        key=month_keys[k]
        i=last_day_idx[key]
        # Month k is closed only after its final daily candle.
        # Next opening (day i+1) is earliest investable entry.
        if i+1>=len(days):continue
        sma=sum(closes[v] for v in month_keys[k-9:k+1])/10
        signals[i+1]={"invested":closes[key]>sma,
                      "month":str(key),"sma":sma}
    return signals

def replay(days,signals,start,end,mode):
    if mode not in MODES:raise ValueError("INVALID_MODE")
    value=10000.
    units=0.
    peak=value
    max_dd=0.
    trades=0
    exposure=0.
    previous_close=None
    for i in range(start,end):
        d=days[i]
        price=d["open"]
        # PnL from previous close to current open.
        if previous_close is not None:
            value+=units*(price-previous_close)
        if mode=="passive_btc" and i==start:
            target=1.
        elif mode=="monthly_sma_full" and i in signals:
            target=1. if signals[i]["invested"] else 0.
        elif mode=="monthly_sma_half" and i in signals:
            target=.5 if signals[i]["invested"] else 0.
        else:target=None
        if target is not None:
            new_units=value*target/price
            turnover=abs(new_units-units)*price
            if turnover>1e-10:trades+=1
            value-=turnover*COST
            units=new_units
        value+=units*(d["close"]-price)
        previous_close=d["close"]
        peak=max(peak,value)
        max_dd=max(max_dd,1-value/peak)
        if value<=0:raise ValueError("NEGATIVE_EQUITY")
        exposure+=units*d["close"]/value
    # Termination fee for liquidation included.
    final_close=days[end-1]["close"]
    value-=abs(units)*final_close*COST
    max_dd=max(max_dd,1-value/peak)
    n=end-start
    return {"net_return_pct":round((value/10000-1)*100,3),
            "annualized_pct":round(((value/10000)**(365/n)-1)*100,3),
            "max_dd_pct":round(max_dd*100,3),
            "days":n,"trades":trades,
            "average_exposure_pct":round(exposure/n*100,2)}

def evaluate(frame):
    days=daily(frame)
    sig=month_signals(days)
    cut=int(len(days)*.60)
    # The first 10 months are used for signal warm-up.
    # Train start is on an observed decision boundary, not guessed from future.
    first=min(sig)
    start=first
    # Later window starts as an independent cash portfolio, and waits for next month-end
    # before deploying. This is deliberately not a continuous whole-history curve.
    windows={"train":(start,cut),"later":(cut,len(days)),
             "whole":(start,len(days))}
    return {"paper":"Faber (2007) monthly 10-month SMA, applied to BTC not original study asset",
            "contract":"BTC long/cash. Completed month end vs 10 closed month end SMA, next UTC day open. 8bps per side, cash 0%, no funding.",
            "risk_control":"Half-exposure control has same signal but <=50% BTC and remaining cash. Not parameter optimized.",
            "periods":{k:{mode:replay(days,sig,a,b,mode) for mode in MODES}
                       for k,(a,b) in windows.items()},
            "first_utc":datetime.fromtimestamp(days[0]["ts"]/1000,timezone.utc).isoformat(),
            "last_utc":datetime.fromtimestamp(days[-1]["ts"]/1000,timezone.utc).isoformat(),
            "limitations":"Already inspected years, NOT blind OOS; public Bybit perpetual trade prices do not include funding or delisting; cash earns 0%."}

def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//DAY*DAY
    for attempt in range(5):
        try:
            frame=get_bybit_data_before("BTC/USDT","4h",boundary,total_limit=BARS)
            break
        except ValueError as exc:
            if str(exc)!="BYBIT_PUBLIC_API_ERROR:10016" or attempt==4:raise
            time.sleep(5*(attempt+1))
    print(json.dumps(evaluate(frame),sort_keys=True))
if __name__=="__main__":main()
