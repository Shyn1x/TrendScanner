"""Read-only completed-calendar-level research. NOT authentic Bybit mark-price liquidation."""
import json, math, statistics, time
from datetime import datetime, timezone
from collections import defaultdict
import connors_rsi2_research as base
from prospective_market_data import get_bybit_data_before

FEE=.0016
FUND=.0001
LEV=10
MARGIN=.05
MAX_POS=3
ADD_DROP=.05
MAX_DAYS=20
TOUCH=.015

def get_data(frames):
    data, times=base.daily_features(frames)
    for s in base.SYMBOLS:
        raw=frames[s]
        for field,agg in (("low",min),("high",max)):
            v=raw[field].astype(float).tolist()
            data[s][field]=[agg(v[i:i+6]) for i in range(0,len(v),6)]
    dates=[datetime.fromtimestamp(t/1000,timezone.utc) for t in times]
    return data,dates

def levels(data,dates,kind):
    """Last FULL UTC calendar week or month, or prior closed daily candle."""
    if kind=="day":
        return {s:[None]+[(data[s]["low"][i-1],data[s]["high"][i-1]) for i in range(1,len(dates))]
                for s in base.SYMBOLS}
    def bucket(d):
        if kind=="week":
            iso=d.isocalendar()
            return (iso.year,iso.week)
        return (d.year,d.month)
    keys=[bucket(d) for d in dates]
    all_levels={}
    for s in base.SYMBOLS:
        buckets=defaultdict(list)
        for i,k in enumerate(keys):
            buckets[k].append(i)
        prior={}
        for k,idx in buckets.items():
            prior[k]=(min(data[s]["low"][i] for i in idx),
                      max(data[s]["high"][i] for i in idx))
        completed=[]
        seen=None
        arr=[]
        for k in keys:
            if k!=seen:
                if seen is not None:
                    completed.append(prior[seen])
                seen=k
            arr.append(completed[-1] if completed else None)
        all_levels[s]=arr
    return all_levels

def replay(data,dates,kind,side_mode,start,end):
    levs=levels(data,dates,kind)
    equity=10000.
    peak=equity
    max_dd=0.
    max_gross=0.
    liq_date=None
    positions={}
    pending_entries=[]
    pending_exits=set()
    trades=0
    wins=0
    adds=0
    fee_paid=0.
    funding_paid=0.
    for i in range(start,end):
        # Overnight changes first; pending exits execute at this day's OPEN.
        for s,p in positions.items():
            equity+=p["qty"]*(data[s]["open"][i]-data[s]["close"][i-1])
        for s in list(pending_exits):
            if s not in positions:
                continue
            p=positions.pop(s)
            px=data[s]["open"][i]
            fee=abs(p["qty"])*px*FEE
            equity-=fee
            fee_paid+=fee
            trade_pnl=p["qty"]*px-p["basis"]-p["fees"]-fee
            wins+=trade_pnl>0
            trades+=1
        pending_exits.clear()
        for s,side,level in pending_entries:
            if s in positions or len(positions)>=MAX_POS:
                continue
            px=data[s]["open"][i]
            if not (level>0 and abs(px/level-1)<=TOUCH):
                continue
            gross=sum(abs(p["qty"])*data[k]["open"][i] for k,p in positions.items())
            size=min(MARGIN*LEV*equity,max(0,MAX_POS*MARGIN*LEV*1.5*equity-gross))
            if size<=0:continue
            qty=(size/px)*side
            fee=size*FEE
            equity-=fee
            fee_paid+=fee
            positions[s]={"qty":qty,"basis":qty*px,"fees":fee,"open":i,
                          "entry":px,"added":False}
        pending_entries=[]
        # Conservative synchronous daily extreme for cross-account proxy.
        stressed=equity
        for s,p in positions.items():
            worst=data[s]["low"][i] if p["qty"]>0 else data[s]["high"][i]
            stressed+=p["qty"]*(worst-data[s]["open"][i])
        gross_extreme=sum(abs(p["qty"])*(data[s]["low"][i] if p["qty"]>0 else data[s]["high"][i])
                          for s,p in positions.items())
        if stressed<=.005*gross_extreme:
            liq_date=dates[i].date().isoformat()
            equity=0.
            max_dd=1.
            break
        for s,p in positions.items():
            equity+=p["qty"]*(data[s]["close"][i]-data[s]["open"][i])
        gross=sum(abs(p["qty"])*data[s]["close"][i] for s,p in positions.items())
        funding=gross*FUND
        equity-=funding
        funding_paid+=funding
        if equity<=0:
            liq_date=dates[i].date().isoformat()
            equity=0.
            max_dd=1.
            break
        max_gross=max(max_gross,100*gross/equity)
        peak=max(peak,equity)
        max_dd=max(max_dd,1-equity/peak)
        if i==end-1:break
        # Yesterday's close creates signals for tomorrow's open only.
        for s,p in positions.items():
            px=data[s]["close"][i]
            lv=levs[s][i]
            target=lv[1] if p["qty"]>0 else (lv[0] if lv else None)
            if kind=="day":
                target=lv[1] if (lv and p["qty"]>0) else (lv[0] if lv else None)
            holding=i-p["open"]+1
            reached=target is not None and (px>=target if p["qty"]>0 else px<=target)
            stopped=(px<=p["entry"]*.92 if p["qty"]>0 else px>=p["entry"]*1.08)
            if reached or stopped or holding>=MAX_DAYS:
                pending_exits.add(s)
            elif not p["added"]:
                adverse=(px<=p["entry"]*(1-ADD_DROP) if p["qty"]>0
                         else px>=p["entry"]*(1+ADD_DROP))
                if adverse:
                    used=sum(abs(q["qty"])*data[k]["close"][i] for k,q in positions.items())
                    size=min(MARGIN*.5*LEV*equity,
                             max(0,MAX_POS*MARGIN*LEV*1.5*equity-used))
                    if size>0:
                        fee=size*FEE
                        qty=(size/px)*(1 if p["qty"]>0 else -1)
                        p["qty"]+=qty
                        p["basis"]+=qty*px
                        p["fees"]+=fee
                        p["added"]=True
                        equity-=fee
                        fee_paid+=fee
                        adds+=1
        candidates=[]
        for s in base.SYMBOLS:
            if s in positions or s in pending_exits:continue
            lv=levs[s][i]
            if lv is None:continue
            low,high=lv
            px=data[s]["close"][i]
            if low<=0 or high<=low:continue
            if side_mode in ("long","both") and abs(px/low-1)<=TOUCH:
                candidates.append((abs(px/low-1),s,1,low))
            if side_mode in ("short","both") and abs(px/high-1)<=TOUCH:
                candidates.append((abs(px/high-1),s,-1,high))
        candidates.sort()
        selected=set()
        for _,s,side,level in candidates:
            if s not in selected:
                selected.add(s)
                pending_entries.append((s,side,level))
                if len(pending_entries)>=MAX_POS-len(positions)+len(pending_exits):
                    break
    if liq_date is None:
        for s,p in positions.items():
            px=data[s]["close"][end-1]
            fee=abs(p["qty"])*px*FEE
            equity-=fee
            fee_paid+=fee
            wins+=(p["qty"]*px-p["basis"]-p["fees"]-fee)>0
            trades+=1
    return {"return_pct":round((equity/10000-1)*100,3),
            "max_drawdown_pct":round(max_dd*100,3),
            "closed_trades":trades,"win_rate_pct":round(100*wins/trades,2) if trades else None,
            "adds":adds,"max_gross_exposure_pct":round(max_gross,2),
            "liquidation_proxy_date":liq_date,
            "fees_usdt":round(fee_paid,2),"hypothetical_funding_usdt":round(funding_paid,2),
            "days":end-start}

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
    data,dates=get_data(frames)
    cut=int(len(dates)*.6)
    windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(dates))}
    results={}
    for kind in ("week","month","day"):
        results[kind]={}
        for side in (("long","short","both") if kind=="day" else ("long",)):
            results[kind][side]={period:replay(data,dates,kind,side,*span)
                                 for period,span in windows.items()}
    print(json.dumps({"source_end":dates[-1].date().isoformat(),
        "research":"completed UTC calendar support/resistance (week/month) and previous UTC daily high/low",
        "assumptions":"5% initial margin, 10x, cross account proxy, 3 slots, single half-margin add, daily-close signals executed next daily open if still within 1.5% of level, max 20 days, 8% close-based adverse exit, 16bps per side, hypothetical 1bp/day funding",
        "caveats":"Daily bars from Bybit 4h; within-day high/low ordering unknown; not real Bybit Mark Price cross-liquidation, fees or funding; reused history is not blind OOS; level trading and exit signal rules are exploratory",
        "results":results},sort_keys=True))
if __name__=="__main__":main()
