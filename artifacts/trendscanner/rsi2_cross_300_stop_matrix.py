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

def simulate(data,start,end,stop,allow_add,slots):
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
    for i in range(start,end):
        # Overnight mark-to-open. All orders execute only after prior closed daily signal.
        for s,p in positions.items():
            equity+=p["qty"]*(data[s]["open"][i]-data[s]["close"][i-1])
        if equity<=0:
            liquidated=True;equity=0.;break
        def close(s,price,reason,from_open=False):
            nonlocal equity,total_fees
            p=positions.pop(s)
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
            if stop is not None and data[s]["open"][i]<=positions[s]["first"]*(1-stop):
                close(s,data[s]["open"][i],"gap_stop")
        for s in sorted(next_exit):
            if s in positions:close(s,data[s]["open"][i],"signal_exit")
        next_exit=set()
        for s in sorted(next_add):
            if s not in positions or not allow_add:continue
            p=positions[s];price=data[s]["open"][i]
            if p["added"] or price>p["first"]*.95:continue
            gross=sum(q["qty"]*data[k]["open"][i] for k,q in positions.items())
            capacity=max(0,slots*(MARGIN+ADD_MARGIN)*LEVERAGE*equity-gross)
            per_cap=max(0,(MARGIN+ADD_MARGIN)*equity*LEVERAGE-p["qty"]*price)
            value=min(ADD_MARGIN*LEVERAGE*equity,capacity,per_cap)
            if value<=0:continue
            fee=value*FEE
            equity-=fee;total_fees+=fee
            p["qty"]+=value/price;p["cost_basis"]+=value;p["entry_fee"]+=fee;p["added"]=True
        next_add=set()
        for s in next_entry:
            if len(positions)>=slots or s in positions:continue
            price=data[s]["open"][i]
            if not math.isfinite(price) or price<=0:continue
            gross=sum(q["qty"]*data[k]["open"][i] for k,q in positions.items())
            capacity=max(0,slots*(MARGIN+ADD_MARGIN)*LEVERAGE*equity-gross)
            value=min(MARGIN*LEVERAGE*equity,capacity)
            if value<=0:continue
            fee=value*FEE
            equity-=fee;total_fees+=fee
            positions[s]={"qty":value/price,"first":price,"cost_basis":value,
                          "entry_fee":fee,"added":False,"opened":i}
        next_entry=[]
        # Stop triggered by actual aggregated 4h candle minima, not merely daily close.
        # Low is pessimistic if stop and rebound occurred within same daily bar.
        for s in sorted(list(positions)):
            p=positions[s]
            if stop is not None and data[s]["low"][i]<=p["first"]*(1-stop):
                close(s,p["first"]*(1-stop),"intraday_stop",from_open=True)
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
        free=max(0,slots-len(positions)+len(next_exit))
        missed_capacity+=max(0,len(available)-free)
        next_entry=available[:free]
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
            "synthetic_cross_liquidation":liquidated}

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
        cut=int(len(times)*.6)
        windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
        grid={}
        for slots in (3,5):
            for add in (False,True):
                for stop in (None,.03,.05,.08):
                    label=f"slots{slots}_stop{round(stop*100) if stop is not None else 'none'}_add{int(add)}"
                    grid[label]={name:simulate(data,start,end,stop,add,slots)
                                for name,(start,end) in windows.items()}
        print(json.dumps({"kind":"RSI2 historical stop matrix, research only",
            "account_usdt":300,"margin_mode":"cross proxy","per_entry_margin_pct":5,
            "per_add_margin_pct":2.5,"leverage":10,"max_open_positions":[3,5],
            "stops_on_price_pct":[None,3,5,8],"fee_per_side_bps":16,
            "hypothetical_daily_funding_bps":1,"universe":selected,
            "data_end_utc":datetime.fromtimestamp(boundary/1000,timezone.utc).isoformat(),
            "warnings":["Stop is simulated from past daily OHLC low, not Bybit MarkPrice orderbook or exact sequence",
                        "Liquidation proxy does NOT reproduce Bybit cross account, maintenance tiers, fee reserves",
                        "Current symbol selection introduces survivorship bias; previously researched dates are not blind",
                        "Minimum order quantities, funding history and actual gap liquidity are not modeled"],
            "results":grid},sort_keys=True,allow_nan=False))
    finally:base.SYMBOLS=before

if __name__=="__main__":main()
