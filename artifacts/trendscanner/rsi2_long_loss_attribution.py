"""Read-only RSI2 LONG loss diagnosis with exact baseline portfolio parity."""
import json,sys,time,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import rsi2_one_add_down as model
import connors_rsi2_research as base
from prospective_market_data import get_bybit_data_before

def analyze(data,times,start,end,fee):
    equity=10000.
    positions={}
    pending_entries=[]
    pending_exits=set()
    pending_adds=[]
    max_equity=equity
    dd=0.
    ledger=[]
    adds=0
    for i in range(start,end):
        for s,p in positions.items():
            equity+=p["qty"]*(data[s]["open"][i]-data[s]["close"][i-1])
        for s in sorted(pending_exits):
            p=positions.pop(s)
            px=data[s]["open"][i]
            exitfee=p["qty"]*px*fee
            equity-=exitfee
            record(ledger,s,p,px,exitfee,i,times,p["exit_reason"])
        pending_exits=set()
        for s in sorted(pending_adds):
            if s not in positions:continue
            p=positions[s]
            px=data[s]["open"][i]
            if p["added"] or px>p["entry"]*.95:continue
            used=sum(q["qty"]*data[k]["open"][i] for k,q in positions.items())
            existing=p["qty"]*px
            size=min(.1*equity,max(0,.3*equity-existing),max(0,.6*equity-used))
            if size<=1e-9:continue
            p["qty"]+=size/px
            p["cost_basis"]+=size
            p["entryfee"]+=fee*size
            p["added"]=True
            equity-=fee*size
            adds+=1
        pending_adds=[]
        for s in pending_entries:
            if s in positions or len(positions)>=3:continue
            used=sum(p["qty"]*data[k]["open"][i] for k,p in positions.items())
            size=min(.2*equity,max(0,.6*equity-used))
            if size<=1e-9:continue
            px=data[s]["open"][i]
            equity-=fee*size
            positions[s]={"qty":size/px,"entry":px,"cost_basis":size,
                          "entryfee":fee*size,"added":False,"opened":i,
                          "worst":px,"first_day_pct":None,"exit_reason":"unknown"}
        pending_entries=[]
        for s,p in positions.items():
            close=data[s]["close"][i]
            equity+=p["qty"]*(close-data[s]["open"][i])
            p["worst"]=min(p["worst"],close)
            if p["first_day_pct"] is None:
                p["first_day_pct"]=100*(close/p["entry"]-1)
        if equity<=0:raise ValueError("EQUITY_EXHAUSTED")
        max_equity=max(max_equity,equity)
        dd=max(dd,1-equity/max_equity)
        if i==end-1:break
        for s,p in positions.items():
            a=data[s]
            age=i-p["opened"]+1
            if a["close"][i]>a["sma5"][i] or age>=20:
                pending_exits.add(s)
                p["exit_reason"]="sma5_rebound" if a["close"][i]>a["sma5"][i] else "20d_limit"
            elif not p["added"] and a["close"][i]<=p["entry"]*.95:
                pending_adds.append(s)
        free=max(0,3-len(positions)+len(pending_exits))
        pending_entries=[s for s in base.entry_candidates(data,i,"rsi5",positions)
                         if s not in pending_exits][:free]
    for s,p in positions.items():
        px=data[s]["close"][end-1]
        f=p["qty"]*px*fee
        equity-=f
        record(ledger,s,p,px,f,end-1,times,"end_of_window")
    dd=max(dd,1-equity/max_equity)
    expected=model.replay(data,"add20",start,end,fee)
    if (abs(expected["net_return_pct"]-100*(equity/10000-1))>.012
        or abs(expected["max_dd_pct"]-100*dd)>.012
        or expected["closed_trades"]!=len(ledger)
        or expected["completed_additions"]!=adds):
        raise AssertionError("ORIGINAL_MODEL_PARITY_MISMATCH")
    return expected,ledger

def record(out,s,p,px,fee,i,times,reason):
    gross=p["qty"]*px-p["cost_basis"]
    net=gross-p["entryfee"]-fee
    out.append({"symbol":s,"exit_year":datetime.fromtimestamp(times[i]/1000,timezone.utc).year,
                "net":net,"gross":gross,"fee":p["entryfee"]+fee,
                "first_day_pct":p["first_day_pct"],
                "worst_daily_close_pct":100*(p["worst"]/p["entry"]-1),
                "added":p["added"],"days_held":i-p["opened"],"reason":reason})

def summarize(rows):
    def sample(group):
        losers=[r for r in group if r["net"]<0]
        if not group:return {"trades":0}
        return {"trades":len(group),"losers":len(losers),
                "net_pnl_usdt":round(sum(r["net"] for r in group),2),
                "gross_losses_usdt":round(sum(r["net"] for r in losers),2),
                "worst_loss_usdt":round(min(r["net"] for r in group),2),
                "fees_turning_gross_win_to_net_loss":sum(r["gross"]>=0 for r in losers),
                "losers_negative_day_one":sum(r["first_day_pct"] is not None and r["first_day_pct"]<0 for r in losers),
                "losers_worst_close_below_minus_5pct":sum(r["worst_daily_close_pct"]<=-5 for r in losers),
                "losers_worst_close_below_minus_10pct":sum(r["worst_daily_close_pct"]<=-10 for r in losers)}
    group_year=defaultdict(list)
    group_exit=defaultdict(list)
    group_sym=defaultdict(list)
    for r in rows:
        group_year[str(r["exit_year"])].append(r)
        group_exit[r["reason"]].append(r)
        group_sym[r["symbol"]].append(r)
    return {"total":sample(rows),
            "with_add":sample([r for r in rows if r["added"]]),
            "without_add":sample([r for r in rows if not r["added"]]),
            "exit_year":{k:sample(v) for k,v in sorted(group_year.items())},
            "exit_reason":{k:sample(v) for k,v in sorted(group_exit.items())},
            "by_symbol":{k:sample(v) for k,v in sorted(group_sym.items())}}

def evaluate(frames):
    data,times=base.daily_features(frames)
    cut=int(len(times)*.60)
    splits={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    results={}
    for f in (.0008,.0016):
        results[str(int(f*1e4))]={}
        for name,(start,end) in splits.items():
            metrics,trades=analyze(data,times,start,end,f)
            results[str(int(f*1e4))][name]={"portfolio":metrics,
                                             "losses":summarize(trades)}
    return {"research":"LONG RSI2 one-add historical losses",
            "baseline_parity":"PASS",
            "results":results,
            "limitations":"Close-only adverse move, not intraday; reason buckets overlap; per-year exit buckets not annual portfolio performance; added vs no-add is selected sample, not causal; later period NOT independent; no funding."}

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
