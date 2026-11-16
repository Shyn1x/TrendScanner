"""Frozen daily Connors RSI(2) mean-reversion adaptation to 12 Bybit perps.

Reference: StockCharts ChartSchool, RSI(2) by Larry Connors:
https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2
Stocks-based rules: LONG only if daily CLOSE>SMA200 and RSI2<5 or <10,
exit on close > SMA5. We use next-day OPEN for both entry and exit,
and a 10-day maximum holding as a *declared crypto risk adaptation*.
The original no-stop trading rule is NOT an endorsement for crypto.
12 fixed instruments, no funding, delisting or liquidation modeling.
No orders, DB mutations, secrets or demo dependencies.
"""
import json,math,os,statistics,sys,time
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prospective_market_data import get_bybit_data_before
SYMBOLS=("BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT","XRP/USDT","DOGE/USDT",
"ADA/USDT","LINK/USDT","LTC/USDT","BCH/USDT","AVAX/USDT","DOT/USDT")
TF=14_400_000
BARS=9000
DAY=TF*6
COST=.0008
CAP=.2
SLOTS=3
EXIT_DAYS=10
WARMUP=250
MODES=("rsi5","rsi10","btc_60pct_hold")
CONTRACT={
"origin":"Larry Connors RSI2 stock-market mean reversion: 200-day trend filter, RSI2 <5 or <10, exit above SMA5",
"adaptation":"12 Bybit USDT linear perpetual trade prices. 10-day max hold added for crypto. 20% equity per position, max 3 concurrent, 60% gross.",
"entry":"signal AFTER completed UTC daily close; LONG on next UTC day open",
"exit":"when completed close>SMA5 or held 10 complete daily candles, exit NEXT day open; forced last-bar close settlement",
"source":"https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2",
"cost":"8bp traded notional EACH side inclusive of hypothetical fees+slippage; funding absent",
"benchmark":"60% of portfolio invested in BTC from period start, rest zero-yield cash",
"selection":"when more candidates than slots, select smallest trailing RSI2, then fixed alphabetic symbol",
"frozen":"compare RSI5 and RSI10 only; 60% train, 20-day embargo, remainder later; no parameter tuning",
"warning":"research-only; historical later segment already influenced other research, NOT untouched OOS"
}

def daily_features(frames):
    if set(frames)!=set(SYMBOLS):raise ValueError("BAD_UNIVERSE")
    reference=frames[SYMBOLS[0]]["time"].astype("int64").tolist()
    if len(reference)!=BARS or reference[0]%DAY or any(y-x!=TF for x,y in zip(reference,reference[1:])):
        raise ValueError("BAD_BAR_GRID")
    data={}
    for symbol in SYMBOLS:
        f=frames[symbol]
        if f["time"].astype("int64").tolist()!=reference:raise ValueError("BAD_ALIGN")
        opens=f["open"].astype(float).to_numpy()
        closes=f["close"].astype(float).to_numpy()
        daily_open=pd.Series(opens[::6])
        daily_close=pd.Series(closes[5::6])
        diff=daily_close.diff()
        gains=diff.clip(lower=0).ewm(alpha=.5,adjust=False,min_periods=3).mean()
        losses=(-diff.clip(upper=0)).ewm(alpha=.5,adjust=False,min_periods=3).mean()
        rsi=100*gains/(gains+losses)
        rsi=rsi.mask((gains+losses)==0,50).fillna(50)
        data[symbol]={"open":daily_open.tolist(),"close":daily_close.tolist(),
            "rsi":rsi.tolist(),
            "sma200":daily_close.rolling(200).mean().tolist(),
            "sma5":daily_close.rolling(5).mean().tolist()}
    return data,[reference[k] for k in range(0,BARS,6)]

def entry_candidates(data,i,mode,positions):
    if mode=="btc_60pct_hold":return []
    threshold=5 if mode=="rsi5" else 10
    result=[]
    for symbol in SYMBOLS:
        if symbol in positions:continue
        a=data[symbol]
        if a["close"][i]>a["sma200"][i] and a["rsi"][i]<threshold:
            result.append((a["rsi"][i],symbol))
    result.sort()
    return [sym for _,sym in result]

def replay(data,mode,start,end):
    if mode not in MODES or start<=WARMUP or end>len(data[SYMBOLS[0]]["open"]):
        raise ValueError("BAD_REPLAY")
    capital=10000.
    positions={}
    pending_exits=set()
    pending_entries=[]
    equity_path=[]
    peak=capital
    dd=0.
    traded=0
    wins=0
    held_days=[]
    winners=[]
    exposure=[]
    for i in range(start,end):
        # Existing positions are marked from yesterday's close to today's open.
        for s,p in positions.items():
            capital+=p["qty"]*(data[s]["open"][i]-data[s]["close"][i-1])
        for s in sorted(pending_exits):
            p=positions.pop(s)
            gross=p["qty"]*data[s]["open"][i]
            capital-=COST*gross
            gain=p["qty"]*(data[s]["open"][i]-p["entry"])-COST*(p["qty"]*p["entry"]+gross)
            winners.append(gain)
            held_days.append(i-p["opened"])
            traded+=1
        pending_exits=set()
        if mode=="btc_60pct_hold" and i==start:
            amount=.6*capital
            q=amount/data["BTC/USDT"]["open"][i]
            capital-=COST*amount
            positions["BTC/USDT"]={"qty":q,"entry":data["BTC/USDT"]["open"][i],"opened":i}
            traded+=1
        for s in pending_entries:
            if s in positions or len(positions)>=SLOTS:continue
            amount=min(.2*capital,max(0,.6*capital-sum(p["qty"]*data[z]["open"][i] for z,p in positions.items())))
            if amount<.000001:continue
            q=amount/data[s]["open"][i]
            capital-=COST*amount
            positions[s]={"qty":q,"entry":data[s]["open"][i],"opened":i}
            traded+=1
        pending_entries=[]
        for s,p in positions.items():
            capital+=p["qty"]*(data[s]["close"][i]-data[s]["open"][i])
        if capital<=0:raise ValueError("NEGATIVE_EQUITY")
        gross=sum(abs(p["qty"]*data[s]["close"][i]) for s,p in positions.items())
        exposure.append(gross/capital)
        peak=max(peak,capital)
        dd=max(dd,1-capital/peak)
        equity_path.append(capital)
        if i==end-1:break
        if mode!="btc_60pct_hold":
            for s,p in positions.items():
                if data[s]["close"][i]>data[s]["sma5"][i] or (i-p["opened"]+1)>=EXIT_DAYS:
                    pending_exits.add(s)
            candidates=entry_candidates(data,i,mode,positions)
            free=max(0,SLOTS-len(positions)+len(pending_exits))
            pending_entries=[s for s in candidates if s not in pending_exits][:free]
    for s,p in positions.items():
        gross=p["qty"]*data[s]["close"][end-1]
        capital-=COST*gross
        gain=p["qty"]*(data[s]["close"][end-1]-p["entry"])-COST*(p["qty"]*p["entry"]+gross)
        winners.append(gain)
        held_days.append(end-p["opened"])
        traded+=1
    dd=max(dd,1-capital/peak)
    changes=[equity_path[j]/equity_path[j-1]-1 for j in range(1,len(equity_path))]
    return {"return_pct":round((capital/10000-1)*100,3),
      "annualized_pct":round(((capital/10000)**(365/(end-start))-1)*100,3),
      "max_drawdown_pct":round(dd*100,3),
      "trades_closed":len(winners),
      "win_rate_pct":round(100*sum(g>0 for g in winners)/len(winners),2) if winners else None,
      "median_holding_days":round(statistics.median(held_days),2) if held_days else None,
      "average_gross_exposure_pct":round(100*statistics.mean(exposure),2),
      "annualized_vol_pct":round(statistics.stdev(changes)*math.sqrt(365)*100,3) if len(changes)>3 else None,
      "days":end-start}
def evaluate(frames):
    data,times=daily_features(frames)
    cut=int(len(times)*.6)
    splits={"train":(WARMUP+1,cut),"later":(cut+20,len(times))}
    return {"contract":CONTRACT,
        "start_utc":datetime.fromtimestamp(times[0]/1000,timezone.utc).isoformat(),
        "last_utc":datetime.fromtimestamp(times[-1]/1000,timezone.utc).isoformat(),
        "train_end_utc":datetime.fromtimestamp(times[cut-1]/1000,timezone.utc).isoformat(),
        "results":{name:{mode:replay(data,mode,a,b) for mode in MODES} for name,(a,b) in splits.items()},
        "validation":"EXPLORATORY_ONLY_NO_FUNDING_OR_BLIND_HOLDOUT"}
def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//DAY*DAY
    frames={}
    for s in SYMBOLS:
        for attempt in range(5):
            try:
                frames[s]=get_bybit_data_before(s,"4h",boundary,total_limit=BARS)
                break
            except ValueError as e:
                if str(e)!="BYBIT_PUBLIC_API_ERROR:10016" or attempt==4:raise
                time.sleep(5*(attempt+1))
    print(json.dumps(evaluate(frames),sort_keys=True))
if __name__=="__main__":main()
