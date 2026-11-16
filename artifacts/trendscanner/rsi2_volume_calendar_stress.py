"""Calendar-year and worst-BTC-30d stress test of published RSI2 LONG volume gate.

Independent per-year ROI comes from a CONTINUOUS portfolio replay per earlier
frozen historical segment, rather than restarting/re-optimising a strategy
every January. Year-end equity is marked at daily CLOSE; only terminal
portfolio liquidation fee is included on segment's last day.
The largest non-overlapping BTC 30-day drops are selected by BTC PRICE ONLY,
not by strategy results. This is not an OOS holdout; 2022-26 reused before.
Uses completed public Bybit USDT perp trade candles, 8 and 16 bp per side;
no funding, liquidation, delisting, gap execution validation.
"""
import json,sys,time,math,statistics
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_entry_crash_filters as original
import rsi2_one_add_down as baseline
import connors_rsi2_research as base
from prospective_market_data import get_bybit_data_before
MODES=("baseline","volume")
COSTS=(.0008,.0016)
ADD_DROP=original.ADD_DROP
ADD_SIZE=original.ADD_SIZE
PER_SYMBOL_CAP=original.PER_SYMBOL_CAP
GROSS_CAP=original.GROSS_CAP
entry_candidates=original.entry_candidates
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


def daily_with_volumes(frames):
    data,times=base.daily_features(frames)
    for s in base.SYMBOLS:
        vals=frames[s]["volume"].astype(float).tolist()
        data[s]["volume"]=[sum(vals[i:i+6]) for i in range(0,len(vals),6)]
    return data,times

def yearly_stats(curve,times,start):
    groups=defaultdict(list)
    for idx,eq in enumerate(curve):
        year=datetime.fromtimestamp(times[start+idx]/1000,timezone.utc).year
        groups[year].append((idx,eq))
    output={}
    last=10000.
    for year,points in sorted(groups.items()):
        end=points[-1][1]
        peak=last
        drawdown=0.
        for _,val in points:
            peak=max(peak,val)
            drawdown=max(drawdown,100*(1-val/peak))
        output[str(year)]={"days":len(points),
            "calendar_piece_net_pct":round(100*(end/last-1),3),
            "within_piece_drawdown_pct":round(drawdown,3),
            "open_equity":round(last,2),"close_equity":round(end,2)}
        last=end
    return output

def top_drawdowns(data,start,end,times,window=30,count=4):
    btc=data["BTC/USDT"]["close"]
    candidates=[]
    # Window end is inclusive; 30 days difference.
    for j in range(start+window,end):
        drop=100*(btc[j]/btc[j-window]-1)
        candidates.append((drop,j-window,j))
    selected=[]
    for drop,a,b in sorted(candidates):
        if all(b<x or a>y for _,x,y in selected):
            selected.append((drop,a,b))
            if len(selected)==count:break
    return [{"btc_30d_pct":round(d,2),
             "start":datetime.fromtimestamp(times[a]/1000,timezone.utc).date().isoformat(),
             "end":datetime.fromtimestamp(times[b]/1000,timezone.utc).date().isoformat(),
             "start_idx":a,"end_idx":b} for d,a,b in selected]

def evaluate(frames):
    data,times=daily_with_volumes(frames)
    cut=int(len(times)*.6)
    windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    results={}
    for fee in COSTS:
        key=str(int(fee*10000));results[key]={}
        for period,(start,end) in windows.items():
            modes={m:replay(data,m,start,end,fee) for m in MODES}
            original_run=original.replay(data,"baseline",start,end,fee)
            original_volume=original.replay(data,"volume",start,end,fee)
            prior=baseline.replay(data,"add20",start,end,fee)
            for m,ref in (("baseline",original_run),("volume",original_volume)):
                for k in ("net_return_pct","annualized_return_pct","max_dd_pct",
                          "closed_trades","completed_additions","new_entries"):
                    if abs(modes[m][k]-ref[k])>.012:raise AssertionError("PARITY_FAILED:"+m+":"+k)
            if abs(modes["baseline"]["net_return_pct"]-prior["net_return_pct"])>.012:
                raise AssertionError("PR51_BASELINE_PARITY_FAILED")
            yearly={m:yearly_stats(modes[m]["daily_equity_final"],times,start)
                    for m in MODES}
            years=sorted(set(yearly["baseline"])|set(yearly["volume"]))
            byyear={y:{"baseline":yearly["baseline"][y],
                       "volume":yearly["volume"][y],
                       "relative_change_pp":round(yearly["volume"][y]["calendar_piece_net_pct"]-
                                                  yearly["baseline"][y]["calendar_piece_net_pct"],3)}
                    for y in years}
            worst=top_drawdowns(data,start,end,times)
            stress=[]
            for w in worst:
                a=w["start_idx"]-start
                b=w["end_idx"]-start
                # ROI from CLOSE of start date to CLOSE of end date.
                x=modes["baseline"]["daily_equity_unliquidated"]
                v=modes["volume"]["daily_equity_unliquidated"]
                stress.append({"start":w["start"],"end":w["end"],
                    "btc_30d_pct":w["btc_30d_pct"],
                    "base_portfolio_30d_pct":round(100*(x[b]/x[a]-1),3),
                    "volume_portfolio_30d_pct":round(100*(v[b]/v[a]-1),3)})
            results[key][period]={
                "summary":{m:{k:v for k,v in modes[m].items() if not k.startswith("daily_equity")}
                           for m in MODES},
                "calendar_years":byyear,
                "four_worst_nonoverlap_btc_30d_periods":stress}
    return {"method":"continuous strategy replay per frozen research period; year-close marked equity; worst nonoverlapping 30d BTC drops selected without strategy outcomes",
      "results":results,
      "sample_start":datetime.fromtimestamp(times[0]/1000,timezone.utc).date().isoformat(),
      "sample_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).date().isoformat(),
      "baseline_parity":"PASS",
      "limitations":"NOT blind OOS; not a forward causal test. Year partial windows not annualized; 30-day drawdowns use daily closes and do not represent true intraday exchange liquidations. Funding absent."}
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
