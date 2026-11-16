"""Exploratory causal regime-gated selling volume filter for RSI2 LONG.

All three volume-gating variants use CLOSED information on the signal day.
Declare ex ante (no threshold searches):
  base: unchanged RSI2 capped LONG.
  volume: always reject negative candle on 1.5x prev 20d volume.
  volume_btc_below_sma50: gate volume ONLY if BTC close < BTC 50d SMA.
  volume_btc_high_vol: gate volume ONLY if BTC prior 30 daily log returns
      realised standard deviation exceeds the median of the prior 120
      rolling 30d volatility observations (all ending on signal day).
No lookahead. Identical original daily open entry/exit/add path and fees.
Regime switches only affect NEW entry eligibility, NEVER positions or adds.
Backtest 2023-26 epochs have been examined repeatedly: not blind OOS.
Perpetual funding, leverage liquidation and spread drift NOT modeled.
"""
import json,sys,time,math,statistics
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_entry_crash_filters as original
import rsi2_one_add_down as baseline
import connors_rsi2_research as base
import rsi2_volume_calendar_stress as previous
from prospective_market_data import get_bybit_data_before
MODES=("baseline","volume","volume_btc_below_sma50","volume_btc_high_vol")
COSTS=(.0008,.0016)
ADD_DROP=original.ADD_DROP
ADD_SIZE=original.ADD_SIZE
PER_SYMBOL_CAP=original.PER_SYMBOL_CAP
GROSS_CAP=original.GROSS_CAP
entry_candidates=original.entry_candidates

def calc_btc_regime(data):
    btc=data["BTC/USDT"]["close"]
    n=len(btc)
    sma50=[None]*n
    vol30=[None]*n
    highvol=[False]*n
    below50=[False]*n
    logreturns=[None]+[math.log(btc[i]/btc[i-1]) for i in range(1,n)]
    for i in range(50,n):
        sma50[i]=sum(btc[i-49:i+1])/50
        below50[i]=btc[i]<sma50[i]
    for i in range(30,n):
        vol30[i]=statistics.stdev(logreturns[i-29:i+1])
    for i in range(150,n):
        past=[vol30[k] for k in range(i-120,i) if vol30[k] is not None]
        highvol[i]=vol30[i]>statistics.median(past)
    return {"below_sma50":below50,"high_vol":highvol}

def entry_filter(data,s,i,mode):
    if mode=="baseline":return True
    reg=data["_regime"]
    if mode=="volume_btc_below_sma50" and not reg["below_sma50"][i]:
        return True
    if mode=="volume_btc_high_vol" and not reg["high_vol"][i]:
        return True
    return original.entry_filter(data,s,i,"volume")
def replay(data,mode,start,end,cost):
    if mode not in MODES or start<=base.WARMUP or not 0<=cost<.1:
        raise ValueError("BAD_MODEL")
    capital=10000.
    positions={}
    pending_exits=set()
    pending_entries=[]
    pending_adds=[]
    peak=capital
    dd=0.
    winners=[]
    holding=[]
    daily_equity=[]
    exposures=[]
    original_fills=0
    rejected_by_filter=0
    additions=0
    blocked_adds=Counter()
    premature_exit=0
    for i in range(start,end):
        # Mark every open holding through overnight price gap before any order.
        for s,p in positions.items():
            capital+=p["qty"]*(data[s]["open"][i]-data[s]["close"][i-1])
        # Previously signaled exits execute before any adds or new buys.
        for s in sorted(pending_exits):
            p=positions.pop(s)
            px=data[s]["open"][i]
            gross=p["qty"]*px
            fee=cost*gross
            capital-=fee
            pnl=p["qty"]*px-p["cost_basis"]-p["entry_fees"]-fee
            winners.append(pnl)
            holding.append(i-p["opened"])
        pending_exits=set()
        # Execute yesterday's one-time add only if price remains low.
        # Keep caps at CURRENT open equity, never use margin leverage.
        for s in sorted(pending_adds):
            if s not in positions:
                blocked_adds["exited"]+=1
                continue
            p=positions[s]
            px=data[s]["open"][i]
            if p["added"] or px>p["first_entry"]*(1-ADD_DROP):
                blocked_adds["gap_rebound_or_already_added"]+=1
                continue
            used=sum(q["qty"]*data[k]["open"][i] for k,q in positions.items())
            existing=p["qty"]*px
            size=min(ADD_SIZE*capital,
                max(0,PER_SYMBOL_CAP*capital-existing),
                max(0,GROSS_CAP*capital-used))
            if size<=1e-9:
                blocked_adds["capacity"]+=1
                continue
            fee=cost*size
            capital-=fee
            p["qty"]+=size/px
            p["cost_basis"]+=size
            p["entry_fees"]+=fee
            p["added"]=True
            additions+=1
        pending_adds=[]
        for s in pending_entries:
            if s in positions or len(positions)>=base.SLOTS:continue
            used=sum(p["qty"]*data[k]["open"][i] for k,p in positions.items())
            size=min(base.CAP*capital,max(0,GROSS_CAP*capital-used))
            if size<=1e-9:continue
            px=data[s]["open"][i]
            fee=cost*size
            capital-=fee
            positions[s]={"qty":size/px,"cost_basis":size,"entry_fees":fee,
                "opened":i,"first_entry":px,"added":False}
            original_fills+=1
        pending_entries=[]
        # Same-day move from actual OPEN to completed CLOSE.
        for s,p in positions.items():
            capital+=p["qty"]*(data[s]["close"][i]-data[s]["open"][i])
        if capital<=0:raise ValueError("EQUITY_EXHAUSTED")
        gross=sum(p["qty"]*data[s]["close"][i] for s,p in positions.items())
        exposures.append(gross/capital)
        peak=max(peak,capital)
        dd=max(dd,1-capital/peak)
        daily_equity.append(capital)
        if i==end-1:break
        for s,p in positions.items():
            a=data[s]
            holding_days=i-p["opened"]+1
            max_days=20
            if a["close"][i]>a["sma5"][i] or holding_days>=max_days:
                pending_exits.add(s)
            elif (not p["added"]
                  and a["close"][i]<=p["first_entry"]*(1-ADD_DROP)):
                pending_adds.append(s)
        free=max(0,base.SLOTS-len(positions)+len(pending_exits))
        candidates=[s for s in entry_candidates(data,i,positions)
                         if s not in pending_exits]
        rejected_by_filter+=sum(not entry_filter(data,s,i,mode) for s in candidates)
        pending_entries=[s for s in candidates if entry_filter(data,s,i,mode)][:free]
    # End-of-split forced liquidation, same convention for every model.
    for s,p in positions.items():
        px=data[s]["close"][end-1]
        fee=p["qty"]*px*cost
        capital-=fee
        pnl=p["qty"]*px-p["cost_basis"]-p["entry_fees"]-fee
        winners.append(pnl)
        holding.append(end-p["opened"])
    dd=max(dd,1-capital/peak)
    n=end-start
    equity_returns=[daily_equity[k]/daily_equity[k-1]-1
                    for k in range(1,len(daily_equity))]
    return {
       "daily_equity_unliquidated":daily_equity,
       "daily_equity_final":daily_equity[:-1]+[capital],
       "net_return_pct":round((capital/10000-1)*100,3),
       "annualized_return_pct":round(((capital/10000)**(365/n)-1)*100,3),
       "max_dd_pct":round(dd*100,3),
       "closed_trades":len(winners),
       "win_rate_pct":round(100*sum(p>0 for p in winners)/len(winners),2) if winners else None,
       "median_holding_days":statistics.median(holding) if holding else None,
       "new_entries":original_fills,
       "candidate_rejections":rejected_by_filter,
       "completed_additions":additions,
       "blocked_additions":dict(blocked_adds),
       "mean_gross_exposure_pct":round(100*statistics.mean(exposures),2),
       "annualized_vol_pct":round(statistics.stdev(equity_returns)*math.sqrt(365)*100,3)
          if len(equity_returns)>2 else None,
       "days":n,"cost_bps_per_side":round(cost*1e4)
    }


def yearly_comparison(modes,times,start):
    years={m:previous.yearly_stats(modes[m]["daily_equity_final"],times,start)
           for m in MODES}
    return {y:{m:years[m].get(y,{}) for m in MODES}
            for y in sorted(years["baseline"])}

def evaluate(frames):
    data,times=previous.daily_with_volumes(frames)
    data["_regime"]=calc_btc_regime(data)
    cut=int(len(times)*.6)
    splits={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    results={}
    for cost in COSTS:
        c=str(int(cost*10000))
        results[c]={}
        for period,(start,end) in splits.items():
            modes={m:replay(data,m,start,end,cost) for m in MODES}
            for mode in ("baseline","volume"):
                old=previous.replay(data,mode,start,end,cost)
                for key in ("net_return_pct","annualized_return_pct","max_dd_pct","closed_trades","completed_additions","new_entries"):
                    if abs(old[key]-modes[mode][key])>.012:
                        raise AssertionError("PARITY_ERROR:"+period+":"+mode+":"+key)
            results[c][period]={
                "portfolio":{m:{k:v for k,v in modes[m].items() if not k.startswith("daily_equity")}
                    for m in MODES},
                "calendar_years":yearly_comparison(modes,times,start)}
    return {
      "hypotheses":{
        "below_sma50":"Apply high-selling-volume entry filter only if last completed BTC close is below its trailing 50d SMA",
        "high_vol":"Apply high-selling-volume entry filter only if BTC 30d realised daily return volatility exceeds median previous 120 rolling 30d volatility observations"},
      "comparators":"unchanged RSI2 one-add LONG, always-volume filter, and two distinct fixed regime gated versions",
      "results":results,
      "source_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).date().isoformat(),
      "baseline_parity":"PASS",
      "limitations":"This is exploratory repeated historical data, NOT blind OOS. No funding, delisting, liquidation or mark-price tick simulations."}

def main():
    import os
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//base.DAY*base.DAY
    frames={}
    for s in base.SYMBOLS:
        for attempt in range(5):
            try:
                frames[s]=get_bybit_data_before(s,"4h",boundary,total_limit=base.BARS)
                break
            except ValueError as e:
                if str(e)!="BYBIT_PUBLIC_API_ERROR:10016" or attempt==4:raise
                time.sleep(5*(attempt+1))
    print(json.dumps(evaluate(frames),sort_keys=True))
if __name__=="__main__":main()
