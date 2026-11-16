"""Frozen comparative research: RSI2 SHORT, original RSI2 LONG+one-add, combined.

NOT trading code. Twelve Bybit USDT perps, daily bars assembled from public
4H trade OHLC, same 9000 candles as original LONG research.
Short mirror: RSI(2)>95 and close<SMA200 -> next-day OPEN short,
exit when close<SMA5 (next-day OPEN) or after 10 daily holding bars.
Shorts NEVER averaged up. Longs use previously tested 20d max holding,
ONE 10%-equity addition when price drops >=5% from first entry (next open
also below threshold), and recovery close>SMA5 -> next-open exit.
Compare long_add20, short10, hybrid (both signals compete for the same
3 slots). Each initial position <=20% equity notional, added long<=30%
equity single-symbol and combined gross<=60% on order placement.
Fee stress 8bps and 16bps per side. No funding, short borrow, mark-price
liquidation, contract rounding or delisting modeling.
All signals use fully CLOSED UTC daily bars and orders at next open.
This sample was repeatedly researched before; NOT a blind OOS.
"""
from __future__ import annotations
import json,math,statistics,sys,time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import rsi2_one_add_down as long_model
from prospective_market_data import get_bybit_data_before

MODES=("long_add20","short10","hybrid")
COSTS=(.0008,.0016)
CAP=.20
ADD_CAP=.10
PER_SYMBOL_CAP=.30
GROSS_CAP=.60
SLOTS=3
PROTOCOL={
"hypothesis":"RSI2 shorts after overheated bearish-context rallies enhance long mean reversion without unacceptable drawdown",
"long":"RSI2<5, close>SMA200, SMA5 recovery or 20 days, one 10% buy at 5% further dip",
"short":"RSI2>95, close<SMA200, exit when close<SMA5 or after 10 days, NO SHORT AVERAGING",
"hybrid":"LONG and SHORT candidates compete for same 3 slots and 60% gross, more extreme RSI2 deviation first",
"execution":"completed daily close signal, trade at next UTC daily open, full overnight gaps modeled",
"fees":"8bps each side and 16bps each side stress; no funding/borrow or liquidation costs",
"capital":"initial 20% equity per trade, up to 10% LONG add, single symbol <=30% equity, entry gross <=60% equity",
"validation":"60% chronological train /20d embargo /40% later, already reused history not fresh blind OOS",
"risk":"shorts have unbounded upside price risk, no stop, 10-day time cap, not suitable for deployment without failure stress"
}

def direction_candidates(data,i,positions,mode):
    rows=[]
    for sym in base.SYMBOLS:
        if sym in positions:continue
        a=data[sym]
        close=a["close"][i]
        rsi=a["rsi"][i]
        if mode!="short10" and close>a["sma200"][i] and rsi<5:
            rows.append((rsi,sym,1))
        if mode!="long_add20" and close<a["sma200"][i] and rsi>95:
            rows.append((100-rsi,sym,-1))
    rows.sort()
    return [(sym,side) for _,sym,side in rows]

def replay(data,mode,start,end,cost):
    if mode not in MODES or start<=base.WARMUP or end>len(data[base.SYMBOLS[0]]["open"]):
        raise ValueError("BAD_WINDOW")
    if cost not in COSTS:raise ValueError("UNDECLARED_FEE")
    eq=10000.
    positions={}
    pending_exits=set()
    pending_entries=[]
    pending_adds=[]
    peak=eq
    maxdd=0.
    returns=[]
    closed=[]
    hold=[]
    exposures=[]
    entry_counts=Counter()
    addition_count=0
    skipped=Counter()
    # Q is positive number of coin units, side=-1 short and +1 long.
    for i in range(start,end):
        for s,p in positions.items():
            eq+=p["side"]*p["q"]*(data[s]["open"][i]-data[s]["close"][i-1])
        for s in sorted(pending_exits):
            p=positions.pop(s)
            price=data[s]["open"][i]
            exit_fee=p["q"]*price*cost
            eq-=exit_fee
            pnl=p["side"]*(p["q"]*price-p["cost_basis"])-p["entry_fees"]-exit_fee
            closed.append((p["side"],pnl))
            hold.append(i-p["opened"])
        pending_exits=set()
        for s in sorted(pending_adds):
            if s not in positions:continue
            p=positions[s]
            px=data[s]["open"][i]
            if p["side"]<0:raise AssertionError("UNSAFE_SHORT_AVERAGING")
            if p["added"] or px>p["first_entry"]*.95:
                skipped["recovered_before_add"]+=1
                continue
            used=sum(q["q"]*data[k]["open"][i] for k,q in positions.items())
            current=p["q"]*px
            size=min(ADD_CAP*eq,max(0,PER_SYMBOL_CAP*eq-current),
                     max(0,GROSS_CAP*eq-used))
            if size<=1e-9:
                skipped["capacity"]+=1
                continue
            p["q"]+=size/px
            p["cost_basis"]+=size
            p["entry_fees"]+=cost*size
            p["added"]=True
            eq-=cost*size
            addition_count+=1
        pending_adds=[]
        for s,side in pending_entries:
            if s in positions or len(positions)>=SLOTS:
                skipped["slots"]+=1
                continue
            used=sum(p["q"]*data[k]["open"][i] for k,p in positions.items())
            size=min(CAP*eq,max(0,GROSS_CAP*eq-used))
            if size<=1e-9:
                skipped["gross"]+=1
                continue
            px=data[s]["open"][i]
            eq-=cost*size
            positions[s]={"side":side,"q":size/px,"first_entry":px,
                          "opened":i,"cost_basis":size,"entry_fees":cost*size,
                          "added":False}
            entry_counts["long" if side==1 else "short"]+=1
        pending_entries=[]
        for s,p in positions.items():
            eq+=p["side"]*p["q"]*(data[s]["close"][i]-data[s]["open"][i])
        if eq<=0:raise ValueError("PORTFOLIO_EQUITY_EXHAUSTED")
        gross=sum(p["q"]*data[s]["close"][i] for s,p in positions.items())
        exposures.append(gross/eq)
        peak=max(peak,eq)
        maxdd=max(maxdd,1-eq/peak)
        returns.append(eq)
        if i==end-1:break
        for s,p in positions.items():
            a=data[s]
            age=i-p["opened"]+1
            is_long=p["side"]==1
            exit_on_reversion=(a["close"][i]>a["sma5"][i] if is_long
                               else a["close"][i]<a["sma5"][i])
            maxdays=20 if is_long else 10
            if exit_on_reversion or age>=maxdays:
                pending_exits.add(s)
            elif is_long and not p["added"] and a["close"][i]<=p["first_entry"]*.95:
                pending_adds.append(s)
        free=max(0,SLOTS-len(positions)+len(pending_exits))
        pending_entries=[(s,side) for s,side in direction_candidates(data,i,positions,mode)
                         if s not in pending_exits][:free]
    for s,p in positions.items():
        px=data[s]["close"][end-1]
        fee=p["q"]*px*cost
        eq-=fee
        pnl=p["side"]*(p["q"]*px-p["cost_basis"])-p["entry_fees"]-fee
        closed.append((p["side"],pnl))
        hold.append(end-p["opened"])
    maxdd=max(maxdd,1-eq/peak)
    n=end-start
    ret=[returns[k]/returns[k-1]-1 for k in range(1,len(returns))]
    return {"annualized_pct":round(100*((eq/10000)**(365/n)-1),3),
        "return_pct":round(100*(eq/10000-1),3),
        "max_dd_pct":round(maxdd*100,3),
        "closed_trades":len(closed),
        "long_closed":sum(s==1 for s,p in closed),
        "short_closed":sum(s==-1 for s,p in closed),
        "win_rate_pct":round(100*sum(p>0 for s,p in closed)/len(closed),2) if closed else None,
        "short_win_pct":round(100*sum(p>0 for s,p in closed if s==-1)/sum(s==-1 for s,p in closed),2) if any(s==-1 for s,p in closed) else None,
        "long_win_pct":round(100*sum(p>0 for s,p in closed if s==1)/sum(s==1 for s,p in closed),2) if any(s==1 for s,p in closed) else None,
        "long_entries":entry_counts["long"],"short_entries":entry_counts["short"],
        "one_time_long_adds":addition_count,
        "median_holding_days":statistics.median(hold) if hold else None,
        "mean_gross_pct":round(100*statistics.mean(exposures),2),
        "max_daily_gross_pct":round(100*max(exposures),2),
        "vol_annualized_pct":round(statistics.stdev(ret)*math.sqrt(365)*100,3) if len(ret)>2 else None,
        "skips":dict(skipped),"fee_bps_side":int(cost*10000),"days":n}

def evaluate(frames):
    data,times=base.daily_features(frames)
    cut=int(len(times)*.60)
    windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    result={}
    for fee in COSTS:
        key=str(int(fee*10000))
        result[key]={period:{mode:replay(data,mode,s,e,fee) for mode in MODES}
                     for period,(s,e) in windows.items()}
    # Full original long strategy parity: same signal and capital accounting.
    for period,(s,e) in windows.items():
        prev=long_model.replay(data,"add20",s,e,.0008)
        cur=result["8"][period]["long_add20"]
        for old,new in (("net_return_pct","return_pct"),
                        ("annualized_return_pct","annualized_pct"),
                        ("max_dd_pct","max_dd_pct"),
                        ("closed_trades","closed_trades"),
                        ("completed_additions","one_time_long_adds")):
            if abs(prev[old]-cur[new])>.015:
                raise AssertionError("LONG_BASELINE_PARITY_FAILED:"+period+":"+old)
    return {
        "protocol":PROTOCOL,"results":result,
        "range":{"start":datetime.fromtimestamp(times[0]/1000,timezone.utc).isoformat(),
                 "train_end":datetime.fromtimestamp(times[cut-1]/1000,timezone.utc).isoformat(),
                 "later_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).isoformat()},
        "historical_only_not_independent":True,
        "verdict":"REJECT_IF_SHORT_LOSES_OR_HYBRID_DOES_NOT_BEAT_LONG_ON_RETURN_AND_DRAWDOWN"
    }

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
