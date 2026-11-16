"""Read-only 1x 2x 3x 5x 10x notional leverage stress of RSI2 LONG, no orders. Daily low all assets simultaneous conservative synthetic 0.5% maintenance proxy; equity zero on breach. Not Bybit liquidations. No funding history: 1bp/day notional funding sensitivity only; historical reused, not blind."""
import json,sys,time,math,statistics
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import rsi2_volume_causal_regimes as reference
import rsi2_volume_calendar_stress as loader
from prospective_market_data import get_bybit_data_before
MODES=("volume_btc_high_vol",)
ADD_DROP=reference.ADD_DROP
ADD_SIZE=reference.ADD_SIZE
PER_SYMBOL_CAP=reference.PER_SYMBOL_CAP
GROSS_CAP=reference.GROSS_CAP
entry_candidates=reference.entry_candidates
entry_filter=reference.entry_filter
def replay(data,mode,start,end,cost,lev=1,funding_daily=0,entry_rsi=5,filter_enabled=True,allow_add=True,holding_limit=20):
    if mode not in MODES or start<=base.WARMUP or not 0<=cost<.1 or entry_rsi not in (5,10,15):
        raise ValueError("BAD_MODEL")
    capital=10000.
    liquidation_idx=None
    funding_total=0.
    worst_intraday_equity=10000.
    max_gross_pct=0.
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
    days_with_positions=0
    days_full=0
    days_candidates=0
    daily_exposures=[]
    ledger=[]
    skipped_signal_capacity=0
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
            ledger.append({'symbol':s,'opened_day':p['opened'],'closed_day':i,'net_pnl':round(pnl,4),'added':p['added'],'holding_days':i-p['opened'],'entry_price':p['first_entry'],'exit_price':px,'reason':p.get('exit_reason','unknown')})
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
            size=min(ADD_SIZE*lev*capital,
                max(0,PER_SYMBOL_CAP*lev*capital-existing),
                max(0,GROSS_CAP*lev*capital-used))
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
            size=min(base.CAP*lev*capital,max(0,GROSS_CAP*lev*capital-used))
            if size<=1e-9:continue
            px=data[s]["open"][i]
            fee=cost*size
            capital-=fee
            positions[s]={"qty":size/px,"cost_basis":size,"entry_fees":fee,
                "opened":i,"first_entry":px,"added":False}
            original_fills+=1
        pending_entries=[]
        # Same-day move from actual OPEN to completed CLOSE.
        if positions:
            stressed=capital+sum(p["qty"]*(data[s]["low"][i]-data[s]["open"][i]) for s,p in positions.items())
            low_gross=sum(p["qty"]*data[s]["low"][i] for s,p in positions.items())
            worst_intraday_equity=min(worst_intraday_equity,stressed)
            if stressed<=.005*low_gross:
                capital=0.
                liquidation_idx=i
                break
        for s,p in positions.items():
            capital+=p["qty"]*(data[s]["close"][i]-data[s]["open"][i])
        today_gross=sum(p["qty"]*data[s]["close"][i] for s,p in positions.items())
        funding_payment=funding_daily*today_gross
        capital-=funding_payment
        funding_total+=funding_payment
        if capital<=0:raise ValueError("EQUITY_EXHAUSTED")
        gross=sum(p["qty"]*data[s]["close"][i] for s,p in positions.items())
        exposures.append(gross/capital)
        daily_exposures.append(gross/capital)
        days_with_positions+=bool(positions)
        days_full+=len(positions)>=base.SLOTS
        max_gross_pct=max(max_gross_pct,100*gross/capital)
        peak=max(peak,capital)
        dd=max(dd,1-capital/peak)
        daily_equity.append(capital)
        if i==end-1:break
        for s,p in positions.items():
            a=data[s]
            holding_days=i-p["opened"]+1
            max_days=holding_limit
            if a["close"][i]>a["sma5"][i] or holding_days>=max_days:
                p["exit_reason"]="sma5_rebound" if a["close"][i]>a["sma5"][i] else "time_limit"
                pending_exits.add(s)
            elif (allow_add and not p["added"]
                  and a["close"][i]<=p["first_entry"]*(1-ADD_DROP)):
                pending_adds.append(s)
        free=max(0,base.SLOTS-len(positions)+len(pending_exits))
        # Closed-candle RSI threshold; preserve SMA200 and lowest-RSI priority.
        candidates=[s for s in base.entry_candidates(data,i,"rsi5" if entry_rsi==5 else "rsi10",positions)
                         if s not in pending_exits] if entry_rsi!=15 else [s for _,s in sorted((data[s]["rsi"][i],s) for s in base.SYMBOLS if s not in positions and data[s]["close"][i]>data[s]["sma200"][i] and data[s]["rsi"][i]<15) if s not in pending_exits]
        days_candidates+=bool(candidates)
        eligible=[s for s in candidates if (not filter_enabled or entry_filter(data,s,i,mode))]
        rejected_by_filter+=len(candidates)-len(eligible)
        skipped_signal_capacity+=max(0,len(eligible)-free)
        pending_entries=eligible[:free]
    # End-of-split forced liquidation, same convention for every model.
    for s,p in ([] if liquidation_idx is not None else positions.items()):
        px=data[s]["close"][end-1]
        fee=p["qty"]*px*cost
        capital-=fee
        pnl=p["qty"]*px-p["cost_basis"]-p["entry_fees"]-fee
        winners.append(pnl)
        ledger.append({"symbol":s,"opened_day":p["opened"],"closed_day":end-1,"net_pnl":round(pnl,4),"added":p["added"],"holding_days":end-p["opened"],"entry_price":p["first_entry"],"exit_price":px,"reason":"window_end"})
        holding.append(end-p["opened"])
    dd=max(dd,1-capital/peak)
    n=end-start
    equity_returns=[daily_equity[k]/daily_equity[k-1]-1
                    for k in range(1,len(daily_equity))]
    return {
       "ledger":ledger,
       "days_with_positions":days_with_positions,
       "days_no_positions":(end-start)-days_with_positions,
       "days_three_positions":days_full,
       "days_any_candidate":days_candidates,
       "skipped_candidate_capacity":skipped_signal_capacity,
       "median_exposure_pct":round(100*statistics.median(daily_exposures),3) if daily_exposures else None,
       "daily_equity_unliquidated":daily_equity,
       "daily_equity_final":daily_equity[:-1]+[capital],
       "leverage":lev,
       "liquidation_idx":liquidation_idx,
       "max_gross_pct":round(max_gross_pct,2),
       "worst_synchronous_intraday_equity":round(worst_intraday_equity,2),
       "funding_paid_usdt":round(funding_total,2),
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



def prepare(frames):
    data,times=loader.daily_with_volumes(frames)
    data["_regime"]=reference.calc_btc_regime(data)
    for symbol in base.SYMBOLS:
        vals=frames[symbol]["low"].astype(float).tolist()
        data[symbol]["low"]=[min(vals[i:i+6]) for i in range(0,len(vals),6)]
    return data,times

def evaluate(frames):
    data,times=prepare(frames)
    cut=int(len(times)*.6)
    windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    original=(base.CAP, globals()["ADD_SIZE"], globals()["PER_SYMBOL_CAP"], globals()["GROSS_CAP"])
    scenarios=[
        ("baseline_5pct_10x",.05,5,True,True,20),
        ("no_volume_gate",.05,5,False,True,20),
        ("rsi10_with_gate",.05,10,True,True,20),
        ("rsi10_no_gate",.05,10,False,True,20),
        ("no_averaging",.05,5,True,False,20),
        ("hold_10_days",.05,5,True,True,10),
        ("margin_7pct",.07,5,True,True,20),
    ]
    out={}
    try:
        for name,margin,threshold,gate,add,hold in scenarios:
            base.CAP=margin
            globals()["ADD_SIZE"]=margin/2
            globals()["PER_SYMBOL_CAP"]=margin*1.5
            globals()["GROSS_CAP"]=margin*4.5
            out[name]={}
            for period,(start,end) in windows.items():
                stat=replay(data,"volume_btc_high_vol",start,end,.0016,10,.0001,
                            entry_rsi=threshold,filter_enabled=gate,
                            allow_add=add,holding_limit=hold)
                ledger=stat.pop("ledger")
                negatives=[r for r in ledger if r["net_pnl"]<0]
                positives=[r for r in ledger if r["net_pnl"]>0]
                stat["losing_trades"]=len(negatives)
                stat["loss_by_exit_reason"]={reason:round(sum(r["net_pnl"] for r in negatives if r["reason"]==reason),3)
                                            for reason in set(r["reason"] for r in negatives)}
                stat["winning_pnl"]=round(sum(r["net_pnl"] for r in positives),2)
                stat["losing_pnl"]=round(sum(r["net_pnl"] for r in negatives),2)
                stat["worst_losers"]=sorted(negatives,key=lambda r:r["net_pnl"])[:12]
                stat["all_losers"]=negatives
                stat.pop("daily_equity_unliquidated",None)
                stat.pop("daily_equity_final",None)
                out[name][period]=stat
    finally:
        base.CAP,globals()["ADD_SIZE"],globals()["PER_SYMBOL_CAP"],globals()["GROSS_CAP"]=original
    return {"research":"RSI2 5pct 10x cross proxy: capital utilization and trade-loss diagnostics",
            "historical_periods":"train and previously inspected later, NOT independent holdout",
            "costs":"16bps per side and hypothetical 1bp per day funding",
            "limitations":"Daily close signals/next-day open, reused researched period, synthetic low-based cross liquidation, no actual mark price, no historical funding; small balance minimum order constraints not modeled",
            "results":out,"data_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).date().isoformat()}

def main():
    import os
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//base.DAY*base.DAY
    frames={}
    for sym in base.SYMBOLS:
        for attempt in range(5):
            try:
                frames[sym]=get_bybit_data_before(sym,"4h",boundary,total_limit=base.BARS)
                break
            except ValueError as exc:
                if str(exc)!="BYBIT_PUBLIC_API_ERROR:10016" or attempt==4:raise
                time.sleep(5*(attempt+1))
    print(json.dumps(evaluate(frames),sort_keys=True))
if __name__=="__main__":main()
