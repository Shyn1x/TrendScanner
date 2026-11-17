"""Research-only RSI2 stop/add/slot matrix. Never places orders.
Current top-100 market universe is survivor-biased; this is exploration not validation.
$300 entire Cross equity; 5% initial margin at 10x, at most 3 or 5 slots.
Stop on INTRADAY daily low, with gap-open execution and stop priority over adds.
This is a conservative OHLC proxy, NOT actual Bybit mark-price/liquidation paths.
"""
import json,math,time,statistics
from datetime import datetime,timezone
import connors_rsi2_research as base
import rsi2_100_symbol_research as universe
import rsi2_utilization_loss_diagnostic as diagnostic

START_BALANCE=300.
LEVERAGE=10.
MARGIN=.05
ADD_MARGIN=.025
FEE=.0016
FUNDING=.0001
MAINTENANCE=.005

def daily_atr(data,symbol,n=14):
    a=data[symbol]
    out=[float("nan")]*len(a["close"])
    tr=[]
    for j in range(len(a["close"])):
        hi=a["high"][j];lo=a["low"][j];prev=a["close"][j-1] if j else float("nan")
        tr.append(max(hi-lo,abs(hi-prev),abs(lo-prev)) if all(math.isfinite(x) for x in (hi,lo,prev)) else float("nan"))
        if j>=n and all(math.isfinite(x) for x in tr[j-n+1:j+1]):
            out[j]=sum(tr[j-n+1:j+1])/n
    return out

def attach_atr(data,frames):
    for s in base.SYMBOLS:
        prices=frames[s]["high"].astype(float).tolist()
        data[s]["high"]=[max(prices[k:k+6]) for k in range(0,len(prices),6) if len(prices[k:k+6])==6]
        data[s]["atr14"]=daily_atr(data,s)

def simulate_adaptive(data,start,end,stop_mode,allow_add,slots,cooldown,confirm,risk_cap=.015,total_risk_cap=.06):
    equity=START_BALANCE
    peak=equity
    worst_dd=0.
    positions={}
    next_entry=[]
    next_exit=set()
    next_add=set()
    ledger=[]
    daily_exposure=[]
    active_days=0
    total_fees=0.
    total_funding=0.
    max_positions=0
    max_exposure=0.
    missed_capacity=0
    liquidated=False
    blocked_until={}
    stopped=0
    rejected_cooldown=0
    rejected_confirm=0
    rejected_risk=0
    max_risk_fraction=0.
    for i in range(start,end):
        # Overnight mark-to-open. All orders execute only after prior closed daily signal.
        for s,p in positions.items():
            equity+=p["qty"]*(data[s]["open"][i]-data[s]["close"][i-1])
        if equity<=0:
            liquidated=True;equity=0.;break
        def close(s,price,reason,from_open=False):
            nonlocal equity,total_fees
            p=positions.pop(s)
            if reason in ("gap_stop","intraday_stop"):
                blocked_until[s]=i+cooldown
            if from_open:equity+=p["qty"]*(price-data[s]["open"][i])
            fee=p["qty"]*price*FEE
            equity-=fee
            total_fees+=fee
            pnl=p["qty"]*price-p["cost_basis"]-p["entry_fee"]-fee
            ledger.append({"symbol":s,"pnl":round(pnl,5),"reason":reason,"added":p["added"],
                           "opened_idx":p["opened"],"closed_idx":i,
                           "entry":round(p["first"],8),"exit":round(price,8)})
        # Overnight stop gaps take priority. Real gaps can exceed modeled slippage.
        for s in sorted(list(positions)):
            if positions[s]["stop_px"] is not None and data[s]["open"][i]<=positions[s]["stop_px"]:
                close(s,data[s]["open"][i],"gap_stop")
        for s in sorted(next_exit):
            if s in positions:close(s,data[s]["open"][i],"signal_exit")
        next_exit=set()
        for s in sorted(next_add):
            if s not in positions or not allow_add:continue
            p=positions[s];price=data[s]["open"][i]
            if p["added"] or price>p["first"]*.95:continue
            # A stop remains anchored to pre-add entry; never average the stop down.
            if p["stop_px"] is not None:
                remaining=total_risk_cap*equity-sum(max(0,q["qty"]*(data[k]["open"][i]-q["stop_px"]))
                    for k,q in positions.items() if q["stop_px"] is not None)
                if remaining<=0:continue
            gross=sum(q["qty"]*data[k]["open"][i] for k,q in positions.items())
            capacity=max(0,slots*(MARGIN+ADD_MARGIN)*LEVERAGE*equity-gross)
            per_cap=max(0,(MARGIN+ADD_MARGIN)*equity*LEVERAGE-p["qty"]*price)
            value=min(ADD_MARGIN*LEVERAGE*equity,capacity,per_cap)
            if p["stop_px"] is not None:
                value=min(value,max(0,remaining*price/(price-p["stop_px"])) if price>p["stop_px"] else 0)
            if value<=0:continue
            fee=value*FEE
            equity-=fee;total_fees+=fee
            p["qty"]+=value/price;p["cost_basis"]+=value;p["entry_fee"]+=fee;p["added"]=True
        next_add=set()
        for s,signal_atr in next_entry:
            if len(positions)>=slots or s in positions:continue
            price=data[s]["open"][i]
            if not math.isfinite(price) or price<=0:continue
            gross=sum(q["qty"]*data[k]["open"][i] for k,q in positions.items())
            capacity=max(0,slots*(MARGIN+ADD_MARGIN)*LEVERAGE*equity-gross)
            value=min(MARGIN*LEVERAGE*equity,capacity)
            stop_px=(price*.95 if stop_mode=="pct5" else
                price-stop_mode*signal_atr if isinstance(stop_mode,float) and math.isfinite(signal_atr)
                else None)
            if stop_px is not None:
                stop_px=max(.00000001,stop_px)
                stop_gap=max(0.,price-stop_px)
                current_risk=sum(max(0,q["qty"]*(data[k]["open"][i]-q["stop_px"]))
                    for k,q in positions.items() if q["stop_px"] is not None)
                budget=min(risk_cap*equity,total_risk_cap*equity-current_risk)
                if budget<=0 or stop_gap<=0:
                    rejected_risk+=1;continue
                value=min(value,budget*price/stop_gap)
            if value<=0:continue
            fee=value*FEE
            equity-=fee;total_fees+=fee
            positions[s]={"qty":value/price,"first":price,"cost_basis":value,
                          "entry_fee":fee,"added":False,"opened":i,"stop_px":stop_px}
        next_entry=[]
        # Stop triggered by actual aggregated 4h candle minima, not merely daily close.
        # Low is pessimistic if stop and rebound occurred within same daily bar.
        for s in sorted(list(positions)):
            p=positions[s]
            if p["stop_px"] is not None and data[s]["low"][i]<=p["stop_px"]:
                close(s,p["stop_px"],"intraday_stop",from_open=True)
        # Mark surviving positions to simultaneous low for a cross-margin danger flag.
        stressed=equity+sum(p["qty"]*(data[s]["low"][i]-data[s]["open"][i])
                            for s,p in positions.items())
        notional_low=sum(p["qty"]*data[s]["low"][i] for s,p in positions.items())
        if stressed<=MAINTENANCE*notional_low:
            liquidated=True;equity=0.;worst_dd=1.;break
        # Daily close mark for remaining positions.
        for s,p in positions.items():
            equity+=p["qty"]*(data[s]["close"][i]-data[s]["open"][i])
        notional=sum(p["qty"]*data[s]["close"][i] for s,p in positions.items())
        funding=FUNDING*notional
        equity-=funding;total_funding+=funding
        if equity<=0:liquidated=True;equity=0.;worst_dd=1.;break
        max_positions=max(max_positions,len(positions))
        active_days+=int(bool(positions))
        daily_exposure.append(notional/equity)
        max_exposure=max(max_exposure,notional/equity)
        peak=max(peak,equity);worst_dd=max(worst_dd,1-equity/peak)
        if i==end-1:break
        for s,p in positions.items():
            a=data[s]
            if a["close"][i]>a["sma5"][i] or i-p["opened"]+1>=20:
                next_exit.add(s)
            elif allow_add and not p["added"] and a["close"][i]<=p["first"]*.95:
                next_add.add(s)
        available=[s for s in base.entry_candidates(data,i,"rsi5",positions) if s not in next_exit
                   and diagnostic.entry_filter(data,s,i,"volume_btc_high_vol")]
        filtered=[]
        for s in available:
            if i<=blocked_until.get(s,-1):
                rejected_cooldown+=1;continue
            if confirm and not (data[s]["close"][i]>data[s]["close"][i-1]):
                rejected_confirm+=1;continue
            atr=data[s]["atr14"][i]
            if isinstance(stop_mode,float) and not math.isfinite(atr):continue
            filtered.append((s,atr))
        free=max(0,slots-len(positions)+len(next_exit))
        missed_capacity+=max(0,len(filtered)-free)
        next_entry=filtered[:free]
    if not liquidated:
        for s in sorted(list(positions)):
            close(s,data[s]["close"][end-1],"window_end")
        peak=max(peak,equity)
        worst_dd=max(worst_dd,1-equity/peak)
    losers=sorted((x for x in ledger if x["pnl"]<0),key=lambda x:x["pnl"])
    return {"return_pct":round(100*(equity/START_BALANCE-1),3),
            "final_balance":round(equity,3),"max_dd_pct":round(worst_dd*100,3),
            "trades":len(ledger),"losers":len(losers),
            "win_rate_pct":round(100*sum(x["pnl"]>0 for x in ledger)/len(ledger),2) if ledger else None,
            "worst_losses":losers[:10],"loss_by_reason":{reason:round(sum(x["pnl"] for x in losers if x["reason"]==reason),3) for reason in set(x["reason"] for x in losers)},
            "days_with_positions":active_days,"days_without_positions":(end-start)-active_days,
            "missed_due_to_slots":missed_capacity,"max_positions_used":max_positions,
            "mean_notional_exposure_pct":round(100*statistics.mean(daily_exposure),2) if daily_exposure else 0.,
            "max_notional_exposure_pct":round(100*max_exposure,2),
            "fees_usdt":round(total_fees,4),"hypothetical_funding_usdt":round(total_funding,4),
            "cooldown_rejections":rejected_cooldown,"confirmation_rejections":rejected_confirm,
            "risk_rejections":rejected_risk,"synthetic_cross_liquidation":liquidated}

def main():
    if universe.market.MARKET_SOURCE!="bybit_linear":raise SystemExit("BYBIT_SOURCE_REQUIRED")
    frames,ranking,excluded,lengths,boundary=universe.load()
    selected=tuple(list(universe.ORIGINAL_12)+[s for s in ranking if s in frames and s not in universe.ORIGINAL_12])[:100]
    if len(selected)!=100 or any(s not in frames for s in universe.ORIGINAL_12):
        raise SystemExit("INSUFFICIENT_UNIVERSE")
    before=base.SYMBOLS
    try:
        base.SYMBOLS=selected
        data,times=diagnostic.prepare({s:frames[s] for s in selected})
        attach_atr(data,{s:frames[s] for s in selected})
        cut=int(len(times)*.6)
        windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
        grid={}
        for slots in (3,5):
            for add in (False,True):
                for scheme in ("pct5",1.5,2.5):
                    for pause in (0,5):
                        for confirm in (False,True):
                            label=f"slots{slots}_{scheme}_add{int(add)}_cool{pause}_confirm{int(confirm)}"
                            grid[label]={name:simulate_adaptive(data,start,end,scheme,add,slots,pause,confirm)
                                         for name,(start,end) in windows.items()}
        print(json.dumps({"kind":"RSI2 adaptive ATR / cooldown / confirmation factorial matrix; research only",
            "account_usdt":300,"margin_mode":"cross proxy","per_entry_margin_pct":5,
            "per_add_margin_pct":2.5,"leverage":10,"max_open_positions":[3,5],
            "stop_schemes":["fixed_5pct","ATR14_1_5x","ATR14_2_5x"],"risk_per_trade_pct":1.5,"aggregate_stop_risk_pct":6,"cooldowns_days":[0,5],"confirmation":["none","daily close higher than prior daily close"],"fee_per_side_bps":16,
            "hypothetical_daily_funding_bps":1,"universe":selected,
            "data_end_utc":datetime.fromtimestamp(boundary/1000,timezone.utc).isoformat(),
            "warnings":["Stop is simulated from past daily OHLC low, not Bybit MarkPrice orderbook or exact sequence",
                        "Liquidation proxy does NOT reproduce Bybit cross account, maintenance tiers, fee reserves",
                        "Current symbol selection introduces survivorship bias; previously researched dates are not blind",
                        "Minimum order quantities, funding history and actual gap liquidity are not modeled","Fixed ATR14 from prior completed daily bar; stop based on trade candles, not mark price","Risk controls only limit modeled stop-loss distance; gaps and correlated simultaneous stops can exceed risk budget"],
            "results":grid},sort_keys=True,allow_nan=False))
    finally:base.SYMBOLS=before

if __name__=="__main__":main()
