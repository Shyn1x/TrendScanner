"""Research-only controlled test of withholding 5%-dip addition below SMA200.

Exact copy of PR51 portfolio logic with ONE preregistered gate: when the
completed daily close already falls below its trailing SMA200, do NOT
schedule the single additional LONG buy. Prior entry/exit and max holding
remain unchanged. A trade already open still exits by prior rules.
Baseline add20 is asserted numerically identical to PR51.
No orders or VPS runtime changes. All historical intervals reused;
NOT blind OOS. Funding and gap slippage caveats still apply.
"""
import json,sys,time,math,statistics
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_one_add_down as baseline
import connors_rsi2_research as base
from prospective_market_data import get_bybit_data_before
COSTS=(.0008,.0016)
ADD_DROP=baseline.ADD_DROP
ADD_SIZE=baseline.ADD_SIZE
PER_SYMBOL_CAP=baseline.PER_SYMBOL_CAP
GROSS_CAP=baseline.GROSS_CAP

def replay(data,mode,start,end,cost):
    if mode not in ("add20","guarded_add20") or start<=base.WARMUP or not 0<=cost<.1:
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
            max_days=10 if mode=="base10" else 20
            if a["close"][i]>a["sma5"][i] or holding_days>=max_days:
                pending_exits.add(s)
            elif (mode in ("add20","guarded_add20") and not p["added"]
                  and a["close"][i]<=p["first_entry"]*(1-ADD_DROP)
                  and (mode!="guarded_add20" or a["close"][i]>a["sma200"][i])):
                pending_adds.append(s)
        free=max(0,base.SLOTS-len(positions)+len(pending_exits))
        pending_entries=[s for s in entry_candidates(data,i,positions)
                         if s not in pending_exits][:free]
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
       "completed_additions":additions,
       "blocked_additions":dict(blocked_adds),
       "mean_gross_exposure_pct":round(100*statistics.mean(exposures),2),
       "annualized_vol_pct":round(statistics.stdev(equity_returns)*math.sqrt(365)*100,3)
          if len(equity_returns)>2 else None,
       "days":n,"cost_bps_per_side":round(cost*1e4)
    }


def evaluate(frames):
    data,times=base.daily_features(frames)
    cut=int(len(times)*.6)
    splits={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    results={}
    for cost in COSTS:
        key=str(int(cost*1e4))
        results[key]={}
        for period,(start,end) in splits.items():
            orig=baseline.replay(data,"add20",start,end,cost)
            control=replay(data,"add20",start,end,cost)
            guarded=replay(data,"guarded_add20",start,end,cost)
            for f in ("net_return_pct","annualized_return_pct","max_dd_pct",
                      "closed_trades","completed_additions"):
                if abs(orig[f]-control[f])>.012:raise AssertionError("ORIGINAL_PARITY_FAILED:"+f)
            results[key][period]={"unchanged_add20":control,"trend_guarded_add20":guarded,
                 "annualized_difference_pp":round(guarded["annualized_return_pct"]-control["annualized_return_pct"],3),
                 "drawdown_difference_pp":round(guarded["max_dd_pct"]-control["max_dd_pct"],3)}
    return {"hypothesis":"Only add after 5% price dip IF daily signal close remains above trailing SMA200",
            "signal_timing":"All decision information previous completed UTC daily close; fill next UTC daily OPEN",
            "history_already_inspected":True,
            "risk":"May miss the profitable deep-rebound buys; added-vs-nonadded diagnostic not causal",
            "verdict_rule":"Accept only if risk reduces and return improvement persists across both intervals and fee stress; then still demand fresh prospective sample",
            "results":results,"reference_parity":"PASS",
            "limitations":"Funding, liquidation, survivorship and gap-aware execution absent"}
def main():
    import os
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":raise SystemExit("BYBIT_SOURCE_REQUIRED")
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
