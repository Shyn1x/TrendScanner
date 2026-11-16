"""RSI2 LONG fixed crash-entry filters: public OHLCV and baseline parity.

Pre-registered non-optimized screening hypotheses, tested separately and
as a combined overly restrictive control:
  btc5: reject a new entry if BTC daily close is >5% below 5 days ago.
  coin5: reject if candidate 5-day price return falls below -10%.
  volume: reject if signal day return <0 AND day's traded base volume
          >1.5x mean of the PRECEDING 20 fully closed days.
All features computed only at prior UTC daily close and trade at next open.
This is retrospective exploratory research on already reused history:
does not qualify as blind OOS. Future approval requires new data and
execution costs including funding, spread and liquidation stress.
"""
import json,sys,time,math,statistics
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_one_add_down as baseline
import connors_rsi2_research as base
from prospective_market_data import get_bybit_data_before

MODES=("baseline","btc5","coin5","volume","all3")
COSTS=(.0008,.0016)
ADD_DROP=baseline.ADD_DROP
ADD_SIZE=baseline.ADD_SIZE
PER_SYMBOL_CAP=baseline.PER_SYMBOL_CAP
GROSS_CAP=baseline.GROSS_CAP
entry_candidates=baseline.entry_candidates

def entry_filter(data,s,i,mode):
    if mode=="baseline":return True
    btc=data["BTC/USDT"]["close"]
    coin=data[s]["close"]
    btc_crash=btc[i]<.95*btc[i-5]
    coin_crash=coin[i]<.9*coin[i-5]
    day_volume=data[s]["volume"][i]
    prior_volumes=data[s]["volume"][i-20:i]
    sell_vol_spike=(coin[i]<coin[i-1]
                     and day_volume>1.5*sum(prior_volumes)/len(prior_volumes))
    return not ((mode in ("btc5","all3") and btc_crash)
                or (mode in ("coin5","all3") and coin_crash)
                or (mode in ("volume","all3") and sell_vol_spike))

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


def daily_with_volumes(frames):
    data,times=base.daily_features(frames)
    for s in base.SYMBOLS:
        vals=frames[s]["volume"].astype(float).tolist()
        data[s]["volume"]=[sum(vals[i:i+6]) for i in range(0,len(vals),6)]
    return data,times

def evaluate(frames):
    data,times=daily_with_volumes(frames)
    cut=int(len(times)*.6)
    windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    out={}
    for fee in COSTS:
        key=str(int(fee*1e4))
        out[key]={}
        for period,(start,end) in windows.items():
            result={mode:replay(data,mode,start,end,fee) for mode in MODES}
            orig=baseline.replay(data,"add20",start,end,fee)
            for a,b in [("net_return_pct","net_return_pct"),("annualized_return_pct","annualized_return_pct"),
                        ("max_dd_pct","max_dd_pct"),("closed_trades","closed_trades"),
                        ("completed_additions","completed_additions")]:
                if abs(orig[a]-result["baseline"][b])>.012:
                    raise AssertionError("BASELINE_PARITY_FAILED:"+period+":"+a)
            out[key][period]=result
    return {"research":"Entry crash filters for RSI2 LONG with capped one-add",
      "predeclared_thresholds":{"BTC_5d_return":"< -5%", "coin_5d_return":"< -10%",
          "selling_volume":"negative signal-day return and volume>1.5x previous 20 complete days"},
      "scope":"new entries only; all original exits, position size, limited additional buy unchanged",
      "cut_utc":datetime.fromtimestamp(times[cut-1]/1000,timezone.utc).isoformat(),
      "data_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).isoformat(),
      "results":out,"parity":"PASS",
      "interpretation":"Retrospective filter comparison is susceptible to multiple testing; NOT prospective validation. Funding and liquidation absent."}

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
