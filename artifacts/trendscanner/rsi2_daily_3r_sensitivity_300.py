"""Research-only 4h vs 1d RSI2 comparison on the same 100 current Bybit crypto perps.

All entries use closed-bar RSI2<5 and next-bar open, plus prior completed daily
SMA200 trend and volume/BTC regime gate. Both modes use same DAILY ATR14 2.5x
stop, 20-day maximum hold, 5-day stop cooldown and one 2.5% margin add.
4h uses SMA30 exit (~5 days); 1d uses SMA5. This is NOT a Bybit execution
replica or independent out-of-sample validation. No trading/database access.
"""
import json,math,statistics
from datetime import datetime,timezone
import pandas as pd
import connors_rsi2_research as base
import rsi2_100_symbol_research as universe
import rsi2_utilization_loss_diagnostic as diagnostic
import rsi2_adaptive_cross_300 as adaptive

START=300.
FEE=.0016
FUNDING_DAY=.0001
LEVERAGE=10.
ENTRY_MARGIN=.05
ADD_MARGIN=.025
STOP_MULT=2.5
MAX_RISK_TOTAL=.06
COOLDOWN_DAYS=5
MAX_HOLD_DAYS=20

def build_4h(frames,daily):
    out={}
    for s in base.SYMBOLS:
        frame=frames[s]
        close=frame["close"].astype(float).reset_index(drop=True)
        prices=close.diff()
        gain=prices.clip(lower=0).ewm(alpha=.5,adjust=False,min_periods=3).mean()
        loss=(-prices.clip(upper=0)).ewm(alpha=.5,adjust=False,min_periods=3).mean()
        denom=gain+loss
        rsi=(100*gain/denom).mask(denom==0,50).fillna(50)
        out[s]={"open":frame["open"].astype(float).tolist(),
                "high":frame["high"].astype(float).tolist(),
                "low":frame["low"].astype(float).tolist(),
                "close":close.tolist(),
                "rsi":rsi.tolist(),
                "exit_sma":close.rolling(30,min_periods=30).mean().tolist()}
    return out

def build_daily(data):
    return {s:{"open":data[s]["open"],"high":data[s]["high"],"low":data[s]["low"],
                "close":data[s]["close"],"rsi":data[s]["rsi"],
                "exit_sma":data[s]["sma5"]} for s in base.SYMBOLS}

def finite(*items):
    return all(isinstance(x,(float,int)) and math.isfinite(x) for x in items)

def open_stop_risk(positions, prices, i):
    """Total modeled stop loss for open positions at the specified bar open."""
    return sum(max(0.,p["qty"]*(prices[s]["open"][i]-p["stop"]))
               for s,p in positions.items())


def worst_net_loss_at_stop(position):
    """Loss from original cost basis if filled at stop, incl. estimated exit fee.
    A gap/slippage may exceed this modeled risk.
    """
    return max(0.,position["cost_basis"]+position["entry_fee"]
               -position["qty"]*position["stop"]*(1.-FEE))

def allowed_add_notional(p,px,account_equity,other_stop_risk,portfolio_risk_cap):
    """Maximum new notional without increasing lifetime worst stop loss
    beyond original risk_cap, incl. additional entry and exit fees.
    """
    if px<=p["stop"] or account_equity<=0:return 0.
    existing=worst_net_loss_at_stop(p)
    room=min(p["risk_cap"]-existing,
             portfolio_risk_cap*account_equity-existing-other_stop_risk)
    unit_loss=(1.+FEE)-(p["stop"]/px)*(1.-FEE)
    return max(0.,room/unit_loss) if unit_loss>0 else 0.

def simulate(prices,daily,start,end,mode,slots,risk_budget,add_enabled=False,exit_policy="3R",
             entry_rsi=5.,stop_atr=2.5,hold_days=20):
    if exit_policy not in ("sma","2R","3R","partial_1R_3R"):raise ValueError("BAD_EXIT_POLICY")
    if not 0<entry_rsi<50 or not 0<stop_atr<=10 or not 0<hold_days<=60:raise ValueError("BAD_RESEARCH_PARAMETERS")
    units=6 if mode=="4h" else 1
    risk_total=MAX_RISK_TOTAL
    wallet=START
    peak=START
    dd=0.
    positions={}
    to_enter=[]
    to_exit=set()
    to_add=set()
    cooldown={}
    reasons={}
    ledger=[]
    total_fees=0.
    total_funding=0.
    active=0
    exposure_sum=0.
    max_exposure=0.
    gross_worst=0.
    missed_slots=0
    rejected_risk=0
    rejected_cooldown=0
    signals=0
    added_count=0
    liquidation=False
    liquidated_at=None


    for i in range(start,end):
        # Previous close to current open, including gaps.
        for s,p in positions.items():
            wallet+=p["qty"]*(prices[s]["open"][i]-prices[s]["close"][i-1])
        if wallet<=0:
            wallet=0.;liquidation=True;liquidated_at=i;break

        def settle(s,px,why,from_open=False):
            nonlocal wallet,total_fees
            p=positions.pop(s)
            if from_open:
                wallet+=p["qty"]*(px-prices[s]["open"][i])
            exit_fee=p["qty"]*px*FEE
            total_fees+=exit_fee
            wallet-=exit_fee
            pnl=p["realized_partial"]+p["qty"]*px-p["cost_basis"]-p["entry_fee"]-exit_fee
            ledger.append({"symbol":s,"net_usdt":round(pnl,5),
                           "reason":why,"added":p["added"],"bars_held":i-p["opened"],
                           "risk_usdt":round(p["initial_risk"],5),
                           "realized_R":round(pnl/p["initial_risk"],5) if p["initial_risk"]>0 else None,
                           "entry":round(p["first"],8),"exit":round(px,8),
                           "stop":round(p["stop"],8),
                           "partial_fills":p["partial_count"],
                           "opened_idx":p["opened"],"closed_idx":i})
            reasons[why]=reasons.get(why,0)+1
            if why in ("gap_stop","intrabar_stop"):
                cooldown[s]=i+COOLDOWN_DAYS*units

        def take_partial(s,px):
            """Realise half of CURRENT qty at 1R, leave rest at frozen 3R target."""
            nonlocal wallet,total_fees
            p=positions[s]
            q=p["qty"]*.5
            open_px=prices[s]["open"][i]
            fee=q*px*FEE
            wallet+=q*(px-open_px)-fee
            total_fees+=fee
            realized=q*px-.5*p["cost_basis"]-.5*p["entry_fee"]-fee
            p["realized_partial"]+=realized
            p["qty"]-=q
            p["cost_basis"]*=.5
            p["entry_fee"]*=.5
            p["partial_count"]+=1
            p["partial_done"]=True

        # Orders created on completed previous bar.
        for s in sorted(list(positions)):
            if prices[s]["open"][i]<=positions[s]["stop"]:
                settle(s,prices[s]["open"][i],"gap_stop")
        for s in sorted(to_exit):
            if s in positions:settle(s,prices[s]["open"][i],"signal_exit")
        to_exit=set()
        # Averages obey a frozen stop and the same whole-account risk cap.
        for s in sorted(to_add):
            if not add_enabled or s not in positions:continue
            p=positions[s]
            px=prices[s]["open"][i]
            if p["added"] or px>p["first"]*.95 or px<=p["stop"]:continue
            used=sum(q["qty"]*prices[k]["open"][i] for k,q in positions.items())
            room=max(0.,slots*(ENTRY_MARGIN+ADD_MARGIN)*LEVERAGE*wallet-used)
            symbol_room=max(0.,(ENTRY_MARGIN+ADD_MARGIN)*LEVERAGE*wallet-p["qty"]*px)
            other_risk=sum(worst_net_loss_at_stop(q)
                           for k,q in positions.items() if k!=s)
            risk_room=allowed_add_notional(p,px,wallet,other_risk,risk_total)
            qty_value=min(ADD_MARGIN*LEVERAGE*wallet,room,symbol_room,risk_room)
            if qty_value<=0:continue
            fee=qty_value*FEE
            wallet-=fee;total_fees+=fee
            p["qty"]+=qty_value/px;p["cost_basis"]+=qty_value
            p["entry_fee"]+=fee;p["added"]=True
            added_count+=1
        to_add=set()
        for s,atr in to_enter:
            if s in positions or len(positions)>=slots:continue
            px=prices[s]["open"][i]
            if not finite(px,atr) or px<=0 or atr<=0:continue
            fixed_stop=max(1e-12,px-stop_atr*atr)
            if fixed_stop>=px:continue
            # Stop distance may widen on 4h gap. Size from actual next-bar open.
            used=sum(q["qty"]*prices[k]["open"][i] for k,q in positions.items())
            room=max(0.,slots*(ENTRY_MARGIN+ADD_MARGIN)*LEVERAGE*wallet-used)
            current_risk=sum(worst_net_loss_at_stop(q) for q in positions.values())
            budget=min(risk_budget*wallet,risk_total*wallet-current_risk)
            if budget<=0:
                rejected_risk+=1;continue
            stop_risk_per_notional=(1.+FEE)-(fixed_stop/px)*(1.-FEE)
            value=min(ENTRY_MARGIN*LEVERAGE*wallet,
                      room,budget/stop_risk_per_notional)
            if value<=0:
                rejected_risk+=1;continue
            fee=value*FEE
            wallet-=fee;total_fees+=fee
            positions[s]={"first":px,"qty":value/px,"stop":fixed_stop,
                          "risk_cap":budget,
                          "realized_partial":0.,"partial_count":0,"partial_done":False,
                          "target_1R":px+(px-fixed_stop),
                          "target_2R":px+2*(px-fixed_stop),
                          "target_3R":px+3*(px-fixed_stop),
                          "cost_basis":value,"entry_fee":fee,
                          "opened":i,"added":False,"initial_risk":value*(px-fixed_stop)/px,
                          "tp":None}
        to_enter=[]

        # Enforce stop FIRST when both stop and take-profit are inside the same bar.
        # Do not assume an unknowable OHLC intrabar ordering in our favour.
        for s in sorted(list(positions)):
            p=positions[s]
            if prices[s]["low"][i]<=p["stop"]:
                settle(s,p["stop"],"intrabar_stop",from_open=True)
                continue
            if exit_policy=="2R" and prices[s]["high"][i]>=p["target_2R"]:
                settle(s,p["target_2R"],"take_profit_2R",from_open=True)
            elif exit_policy=="3R" and prices[s]["high"][i]>=p["target_3R"]:
                settle(s,p["target_3R"],"take_profit_3R",from_open=True)
            elif exit_policy=="partial_1R_3R":
                if prices[s]["high"][i]>=p["target_3R"]:
                    # Conservative 1R partial then 3R remainder, same bar.
                    if not p["partial_done"]:
                        take_partial(s,p["target_1R"])
                    settle(s,p["target_3R"],"partial_then_3R",from_open=True)
                elif not p["partial_done"] and prices[s]["high"][i]>=p["target_1R"]:
                    take_partial(s,p["target_1R"])

        # Conservative simultaneous-low cross maintenance proxy (not Bybit).
        stressed=wallet+sum(p["qty"]*(prices[s]["low"][i]-prices[s]["open"][i])
                            for s,p in positions.items())
        low_notional=sum(p["qty"]*prices[s]["low"][i] for s,p in positions.items())
        if stressed<=.005*low_notional:
            wallet=0.;dd=1.;liquidation=True;liquidated_at=i;break

        for s,p in positions.items():
            wallet+=p["qty"]*(prices[s]["close"][i]-prices[s]["open"][i])
        if wallet<=0:
            wallet=0.;dd=1.;liquidation=True;liquidated_at=i;break
        gross=sum(p["qty"]*prices[s]["close"][i] for s,p in positions.items())
        payment=gross*FUNDING_DAY/units
        total_funding+=payment
        wallet-=payment
        if wallet<=0:
            wallet=0.;dd=1.;liquidation=True;liquidated_at=i;break
        current_exposure=gross/wallet
        gross_worst=max(gross_worst,gross)
        exposure_sum+=current_exposure
        max_exposure=max(max_exposure,current_exposure)
        active+=bool(positions)
        peak=max(peak,wallet)
        dd=max(dd,1-wallet/peak)
        if i==end-1:break

        for s,p in positions.items():
            recent=prices[s]
            if (exit_policy=="sma" and
                finite(recent["close"][i],recent["exit_sma"][i]) and
                recent["close"][i]>recent["exit_sma"][i]) or i-p["opened"]+1>=hold_days*units:
                to_exit.add(s)
            elif add_enabled and not p["added"] and recent["close"][i]<=p["first"]*.95:
                to_add.add(s)

        # The day index is the most recent COMPLETED UTC day at signal close.
        day=i//6 if mode=="4h" and i%6==5 else (i//6-1 if mode=="4h" else i)
        signal_rows=[]
        if day>=base.WARMUP:
            for s in base.SYMBOLS:
                if s in positions or s in to_exit:continue
                d=daily[s]; p=prices[s]
                if not finite(p["rsi"][i],p["close"][i],d["sma200"][day],d["close"][day],d["atr14"][day]):continue
                if p["rsi"][i]>=entry_rsi or d["close"][day]<=d["sma200"][day]:continue
                if not diagnostic.entry_filter(daily,s,day,"volume_btc_high_vol"):continue
                signal_rows.append((p["rsi"][i],s,d["atr14"][day]))
        signal_rows.sort()
        signals+=len(signal_rows)
        eligible=[]
        for _,s,atr in signal_rows:
            if i<=cooldown.get(s,-1):
                rejected_cooldown+=1;continue
            eligible.append((s,atr))
        free=max(0,slots-len(positions)+len(to_exit))
        missed_slots+=max(0,len(eligible)-free)
        to_enter=eligible[:free]

    if not liquidation:
        for s in sorted(list(positions)):
            settle(s,prices[s]["close"][end-1],"window_end")
        dd=max(dd,1-wallet/peak)
    losers=sorted((x for x in ledger if x["net_usdt"]<0),key=lambda x:x["net_usdt"])
    winners_r=[x["realized_R"] for x in ledger if x["realized_R"] is not None and x["realized_R"]>0]
    losers_r=[x["realized_R"] for x in ledger if x["realized_R"] is not None and x["realized_R"]<0]
    return {"net_return_pct":round(100*(wallet/START-1),3),
        "end_balance_usdt":round(wallet,3),
        "max_dd_pct":round(100*dd,3),
        "trades":len(ledger),"losers":len(losers),
        "wins":sum(x["net_usdt"]>0 for x in ledger),
        "active_days":round(active/units,2),
        "avg_notional_exposure_pct":round(100*exposure_sum/(end-start),3),
        "peak_notional_exposure_pct":round(100*max_exposure,3),
        "signals":signals,"skipped_slot_candidates":missed_slots,
        "rejected_cooldown":rejected_cooldown,"rejected_risk":rejected_risk,
        "adds":added_count,"exit_reasons":reasons,
        "fees_usdt":round(total_fees,4),
        "hypothetical_funding_usdt":round(total_funding,4),
        "liquidated_proxy":liquidation,
        "liquidated_at_bar":liquidated_at,
        "worst_losers":losers[:8],"trade_ledger":ledger,
        "avg_winner_R":round(statistics.mean(winners_r),4) if winners_r else None,
        "avg_loser_R":round(statistics.mean(losers_r),4) if losers_r else None,
        "realized_reward_risk_ratio":round(statistics.mean(winners_r)/abs(statistics.mean(losers_r)),4) if winners_r and losers_r else None}

def main():
    if universe.market.MARKET_SOURCE!="bybit_linear":raise SystemExit("BYBIT_SOURCE_REQUIRED")
    frames,rank,excluded,lengths,boundary=universe.load()
    chosen=tuple(list(universe.ORIGINAL_12)+[s for s in rank if s in frames and s not in universe.ORIGINAL_12])[:100]
    if len(chosen)!=100 or any(s not in frames for s in universe.ORIGINAL_12):
        raise SystemExit("INSUFFICIENT_100_ELIGIBLE")
    original=base.SYMBOLS
    try:
        base.SYMBOLS=chosen
        daily,times=diagnostic.prepare({s:frames[s] for s in chosen})
        adaptive.attach_atr(daily,{s:frames[s] for s in chosen})
        price_1d=build_daily(daily)
        cut=int(len(times)*.60)
        windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
        results={}
        # Retained baseline: RSI<5, stop 2.5 ATR, max hold 20 days.
        # All runs have 3 positions, no add, 1% budget, full 3R target.
        for rsi in (3.,5.,8.,12.):
            for atr in (2.,2.5,3.):
                for hold in (10,20,30):
                    key=f"1d_3R_rsi{rsi:g}_atr{atr:g}_hold{hold}"
                    results[key]={period:simulate(price_1d,daily,a,b,"1d",3,.01,
                                            add_enabled=False,exit_policy="3R",
                                            entry_rsi=rsi,stop_atr=atr,hold_days=hold)
                                  for period,(a,b) in windows.items()}
        print(json.dumps({"study":"RSI2 daily 3R sensitivity: threshold x ATR stop x max hold; fixed 1% risk",
            "account_usdt":300,"cross_margin_proxy":True,"leverage":10,
            "initial_margin_pct":5,"add_margin_pct":2.5,"max_positions":[3,5],
            "atr_daily_stop_mult":[2.,2.5,3.],"entry_rsi_threshold":[3.,5.,8.,12.],"max_hold_days":[10,20,30],"risk_per_entry_pct":[1],
            "aggregate_stop_risk_pct":6,"cooldown_days":5,
            "entry":"daily RSI2 threshold 3/5/8/12, completed daily SMA200 trend and volume/BTC filter",
            "exit":"3R target only (no SMA), max hold 10/20/30 days; three slots, no averaging",
            "fee_bps_side":16,"hypothetical_funding_bps_daily":1,
            "selection":"current top turnover, fixed original 12; survivorship bias",
            "periods":"historical re-used train/later, NOT independent OOS",
            "end_utc":datetime.fromtimestamp(boundary/1000,timezone.utc).isoformat(),
            "universe":chosen,"results":results,
            "limitations":["Initial and post-add lifetime loss to stop includes entry and expected exit trading fees; gaps, execution slippage and funding can exceed nominal risk budget","Realized R based on original at-entry risk including full trade after add and partial profits", "Stop wins both-hit ambiguity","simulated bar low stops, not real Bybit MarkPrice stop or order execution",
                "simultaneous low liquidation proxy not actual Bybit cross",
                "historical funding, liquidity, min-order rounding and delistings excluded",
                "high-frequency 4h RSI may churn; fee assumptions are hypothetical",
                "current surviving top100 selection creates survivorship bias"]},
                sort_keys=True,allow_nan=False))
    finally:
        base.SYMBOLS=original
if __name__=="__main__":main()
