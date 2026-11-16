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
def replay(data,mode,start,end,cost,lev=1,funding_daily=0):
    if mode not in MODES or start<=base.WARMUP or not 0<=cost<.1:
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
        max_gross_pct=max(max_gross_pct,100*gross/capital)
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
    for s,p in ([] if liquidation_idx is not None else positions.items()):
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
    out={}
    for fee in (.0008,.0016):
        key=str(int(fee*10000))
        out[key]={}
        for fund in (0,.0001):
            label="no_funding" if fund==0 else "hypothetical_1bp_daily_funding"
            out[key][label]={}
            for period,(start,end) in windows.items():
                per={}
                for lev in (1,2,3,5,10):
                    stat=replay(data,"volume_btc_high_vol",start,end,fee,lev,fund)
                    if lev==1 and fund==0:
                        old=reference.replay(data,"volume_btc_high_vol",start,end,fee)
                        for metric in ("net_return_pct","annualized_return_pct","max_dd_pct",
                                       "closed_trades","completed_additions","new_entries"):
                            if abs(old[metric]-stat[metric])>.012:
                                raise AssertionError("UNLEVERAGED_PARITY_FAILED:"+period+":"+metric)
                    liq=stat["liquidation_idx"]
                    stat["liquidation_date"]=(datetime.fromtimestamp(times[liq]/1000,timezone.utc).date().isoformat()
                                              if liq is not None else None)
                    per[str(lev)+"x"]={k:v for k,v in stat.items() if not k.startswith("daily_equity")}
                out[key][label][period]=per
    # Cross-margin comparison 1x/2x/3x/5x: 1/2/3% equity initial margin per trade.
    # Unlike the legacy 20%-equity-per-slot sizing, here 3 slots together use
    # at most 3 * (margin_pct * leverage) initial notional; one add per slot uses
    # half the initial margin, subject to exposure caps.
    original=(base.CAP, globals()["ADD_SIZE"], globals()["PER_SYMBOL_CAP"], globals()["GROSS_CAP"])
    cross_results={}
    try:
        for margin_pct in (.01,.02,.03):
            base.CAP=margin_pct
            globals()["ADD_SIZE"]=margin_pct/2
            globals()["PER_SYMBOL_CAP"]=margin_pct*1.5
            globals()["GROSS_CAP"]=margin_pct*4.5
            key=f"{int(margin_pct*100)}pct_margin_per_entry"
            cross_results[key]={}
            for leverage in (1,2,3,5):
                for cost in (.0008,.0016):
                    for funding_daily in (0,.0001):
                        for period,(start,end) in windows.items():
                            stat=replay(data,"volume_btc_high_vol",start,end,cost,leverage,funding_daily)
                            liq=stat["liquidation_idx"]
                            stat["liquidation_date"]=(datetime.fromtimestamp(times[liq]/1000,timezone.utc).date().isoformat()
                                                     if liq is not None else None)
                            cross_results[key][f"{leverage}x_{int(cost*1e4)}bps_{int(funding_daily*1e4)}bp_daily_{period}"]={
                                k:v for k,v in stat.items() if not k.startswith("daily_equity")}
    finally:
        base.CAP,globals()["ADD_SIZE"],globals()["PER_SYMBOL_CAP"],globals()["GROSS_CAP"]=original
    return {"model":"LONG RSI2 high-vol BTC volume filter with 1x,2x,3x,5x,10x scaled position notional",
            "capital":"10000 USDT for comparability, percentages apply equally to 300 USDT if perfect fractional fills; small-cap order mins may differ",
            "assumed_risk":"Initially 20% x leverage equity notional per symbol; 3 slots; gross initial cap 60% x leverage, one 10% x leverage addition after a 5% dip",
            "liquidation_model":"Synthetic conservative all daily lows at once; gross 0.5% maintenance, complete equity wiped upon trigger. Not authentic Bybit risk engine or mark-price simulation.",
            "funding":"No actual funding history; 1bp/day of held gross is separate hypothetical stress",
            "results":out,"cross_leverage_1_to_5x_1_to_3pct_margin":cross_results,"cross_assumptions":"Cross margin, 1x/2x/3x/5x, 3 slots max, 1/2/3% equity as initial margin per new trade; one add capped at half initial; total gross capped at 4.5*initial*leverage; account-wide equity maintenance proxy 0.5% of gross, synchronized daily lows (NOT Bybit exact Mark Price, MMR tiers or liquidation engine).","parity":"PASS",
            "data_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).date().isoformat(),
            "limitations":"Repeated researched historical splits, no blind forward test; no slippage beyond 8/16bps per side, no actual tiered isolated liquidation, no partial deleveraging or Bybit instrument minimum order sizes."}

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
