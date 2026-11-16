"""Pre-registered test: does ONE limited average-down improve Connors RSI2?

Research-only, same 12 Bybit 4H-to-daily closes as existing Connors RSI2.
Three strategies:
  base10: original RSI2<5, SMA200 filter, exit >SMA5 or after 10 days
  hold20: identical position, exit >SMA5 or after 20 days
  add20: hold20, once only add 10%-of-equity notional when a completed
         DAILY close falls >=5% below original entry price, AND close<=SMA5.
         Fill at NEXT day OPEN only if price still <= 95% original entry.
Averaging down does NOT reset maximum 20-day holding clock. No stop;
loss can be substantial. Max 3 symbols, 60% combined gross exposure,
30% max in a single symbol after add. Not leverage.
Frozen fees: 8bps per notional side and stress 16bps per side;
funding, exchange liquidation and delistings omitted. Never orders.

Strict anti-leakage: signals at day i close; entry/add/exit only at day i+1 open.
The previously reused 2025-2026 period is not blind OOS.
"""
from __future__ import annotations
import json,math,statistics,sys,time
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
from prospective_market_data import get_bybit_data_before

MODES=("base10","hold20","add20")
COSTS=(.0008,.0016)
ADD_DROP=.05
ADD_SIZE=.10
PER_SYMBOL_CAP=.30
GROSS_CAP=.60
CONTRACT={
"hypothesis":"A single capped dip buy improves RSI2 trade quality versus time extension alone",
"baseline":"base10 duplicates Connors RSI2 RSI<5 long only, SMA200 filter, exit close>SMA5 or 10d",
"control":"hold20 changes only max holding to 20d, with NO additional buy",
"test":"add20 changes hold20 only by one extra buy if close<=95% of FIRST entry and not above SMA5, next open still<=95%, only once",
"risk":"initial 20% equity per symbol; addition <=10% equity, <=30% equity per symbol and <=60% portfolio gross, max 3 symbols",
"exit":"SMA5 recovery on completed daily close (next open) OR 10/20d from initial open; add never resets clock",
"cost_stress":"8 and 16 basis points EACH traded side; charges on adds and exits, omitted funding",
"integrity":"all signals use completed candles, no same-day open/close lookahead; history already reviewed",
"benchmark":"Base10 and hold20 share same execution model; BASE10 numerical parity with prior standalone RSI2 test enforced",
"disclaimer":"no liquidation, funding, bid/ask or 1m execution; historical screen only, NOT investment validation"
}

def entry_candidates(data,i,positions):
    return base.entry_candidates(data,i,"rsi5",positions)

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
            elif (mode=="add20" and not p["added"]
                  and a["close"][i]<=p["first_entry"]*(1-ADD_DROP)):
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
    r={}
    for cost in COSTS:
        key=str(int(cost*10000))
        r[key]={p:{m:replay(data,m,a,b,cost) for m in MODES}
                for p,(a,b) in splits.items()}
    # Baseline parity at original 8bps fee model: identical metrics to PR #50.
    for period,(a,b) in splits.items():
        ref=base.replay(data,"rsi5",a,b)
        actual=r["8"][period]["base10"]
        for left,right in (("return_pct","net_return_pct"),
                           ("annualized_pct","annualized_return_pct"),
                           ("max_drawdown_pct","max_dd_pct"),
                           ("trades_closed","closed_trades")):
            if abs(ref[left]-actual[right])>.012:
                raise AssertionError("BASELINE_PARITY_FAILED:"+period+":"+left)
    return {
      "protocol":CONTRACT,
      "periods_utc":{"start":datetime.fromtimestamp(times[0]/1000,timezone.utc).isoformat(),
                     "train_end":datetime.fromtimestamp(times[cut-1]/1000,timezone.utc).isoformat(),
                     "later_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).isoformat()},
      "results":r,
      "acceptance":"Reject any add-down variant whose improvement vanishes after fees, greater max DD or no improvement over hold20",
      "historical_only_not_blind":True}

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
