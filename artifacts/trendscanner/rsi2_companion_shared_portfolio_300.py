"""Research-only shared-wallet 4H simulation: RSI2 daily + 4H trend breakout.

No real orders. Signal on closed bar, next 4H open; same asset never has
two strategy owners. One shared cash/equity book and shared stop-risk limit.
Stop priority over target within same 4H bar; no fake intrabar sequencing.
"""
import json, math, statistics, time, sys
from pathlib import Path
from datetime import datetime, timezone
import connors_rsi2_research as base
import rsi2_100_symbol_research as universe
import rsi2_utilization_loss_diagnostic as diagnostic
import rsi2_adaptive_cross_300 as adaptive
import companion_4h_breakout_research_300 as companion
from strategy_conflict_guard import Position, Proposal, select_entries

FEE=.0016
FUNDING_4H=.0001/6
START=300.
MAX_SYMBOLS=3
MAX_RISK=.03
MAX_GROSS_MULT=1.5

def run(prices,daily,signals4h,start,end,enable_rsi=True,enable_breakout=True):
    """Indices are 4H; returns full ledger for independent audit."""
    if not(0<=start<end<=len(next(iter(prices.values()))["close"])):
        raise ValueError("BAD_WINDOW")
    cash=START
    peak=START
    max_dd=0.
    positions={}
    next_entries=[]
    next_exits=set()
    cooldown={}
    ledger=[]
    fees=0.
    funding=0.
    counters={"rsi_signals":0,"breakout_signals":0,"blocked_owned":0,
              "blocked_capacity":0,"blocked_risk":0,"accepted_rsi":0,
              "accepted_breakout":0}
    liquidated=False
    max_gross=0.
    for i in range(start,end):
        # Equity tracks mark-to-market from previous close to current open.
        for sym,p in positions.items():
            cash+=p["qty"]*(prices[sym]["open"][i]-prices[sym]["close"][i-1])
        if cash<=0:
            liquidated=True;cash=0.;break

        def close(sym,px,reason,intra=False):
            nonlocal cash,fees
            p=positions.pop(sym)
            if intra:
                cash+=p["qty"]*(px-prices[sym]["open"][i])
            fee=FEE*abs(p["qty"]*px)
            cash-=fee
            fees+=fee
            pnl=p["qty"]*(px-p["entry"])-p["entry_fee"]-fee
            ledger.append({"symbol":sym,"owner":p["owner"],"opened_idx":p["opened"],
                           "closed_idx":i,"exit_reason":reason,
                           "net_usdt":round(pnl,5),"entry":round(p["entry"],8),
                           "exit":round(px,8)})
            if reason in ("gap_stop","intrabar_stop"):
                cooldown[sym]=i+6*5

        # Stop-at-open is conservative for an overnight gap.
        for sym in sorted(list(positions)):
            if prices[sym]["open"][i]<=positions[sym]["stop"]:
                close(sym,prices[sym]["open"][i],"gap_stop")
        for sym in sorted(next_exits):
            if sym in positions:
                close(sym,prices[sym]["open"][i],"hold_expired")
        next_exits=set()

        # Signals are created only on completed prior 4H bar. RSI2 daily
        # signals are generated only after 4H UTC day closes (index mod6=5).
        # If simultaneous, rank owned RSI2 first; pre-existing position wins.
        candidates=[]
        for owner,sym,stop_mult,target_r,hold_days in next_entries:
            if sym in positions:
                counters["blocked_owned"]+=1;continue
            px=prices[sym]["open"][i]
            atr=daily[sym]["atr14"][(i-1)//6]
            if not companion.finite(px,atr) or px<=0 or atr<=0:continue
            stop=max(1e-12,px-stop_mult*atr)
            if stop>=px:continue
            per_notional=(1+FEE)-(stop/px)*(1-FEE)
            risk_room=min(.01*cash, MAX_RISK*cash-sum(
                 max(0.,p["qty"]*(p["entry"]*(1+FEE)-p["stop"]*(1-FEE)))
                 for p in positions.values()))
            existing_gross=sum(p["qty"]*prices[s]["open"][i] for s,p in positions.items())
            gross_room=max(0.,MAX_GROSS_MULT*cash-existing_gross)
            size=min(.5*cash,gross_room,risk_room/per_notional if per_notional>0 else 0.)
            if size<=1e-8:continue
            candidates.append((owner,sym,px,stop,size,per_notional,target_r,hold_days))
        next_entries=[]
        proposals=[Proposal(sym,owner,"LONG",size*per_notional,size)
                   for owner,sym,px,stop,size,unit,target,hold in candidates]
        owned=[Position(sym,p["owner"],
                    max(0.,p["qty"]*(p["entry"]*(1+FEE)-p["stop"]*(1-FEE))),
                    p["qty"]*prices[sym]["open"][i])
               for sym,p in positions.items()]
        accepted,rejected=select_entries(owned,proposals,cash)
        for prop,why in rejected:
            if why in ("MAX_SYMBOL_SLOTS","MAX_NOTIONAL"):counters["blocked_capacity"]+=1
            elif why=="SYMBOL_ALREADY_OWNED":counters["blocked_owned"]+=1
            else:counters["blocked_risk"]+=1
        # A proposal match is unique by (owner,symbol).
        mapping={(owner,sym): (px,stop,size,target_r,hold_days)
                 for owner,sym,px,stop,size,unit,target_r,hold_days in candidates}
        for prop in accepted:
            px,stop,size,target,hold=mapping[(prop.owner,prop.symbol)]
            fee=size*FEE;fees+=fee;cash-=fee
            positions[prop.symbol]={"owner":prop.owner,"entry":px,
                 "entry_fee":fee,"qty":size/px,"stop":stop,
                 "target":px+target*(px-stop),"opened":i,"hold_bars":hold*6}
            counters["accepted_rsi" if prop.owner=="RSI2_1D" else "accepted_breakout"]+=1

        # Stop wins if both stop and target touched inside bar.
        for sym in sorted(list(positions)):
            p=positions[sym]
            if prices[sym]["low"][i]<=p["stop"]:
                close(sym,p["stop"],"intrabar_stop",intra=True)
            elif prices[sym]["high"][i]>=p["target"]:
                close(sym,p["target"],"take_profit",intra=True)

        # Intraday drawdown proxy includes simultaneous bar lows.
        stressed=cash+sum(p["qty"]*(prices[s]["low"][i]-prices[s]["open"][i])
                          for s,p in positions.items())
        notional_low=sum(p["qty"]*prices[s]["low"][i] for s,p in positions.items())
        if stressed<=.005*notional_low:
            liquidated=True;cash=0.;max_dd=1.;break

        for sym,p in positions.items():
            cash+=p["qty"]*(prices[sym]["close"][i]-prices[sym]["open"][i])
        gross=sum(p["qty"]*prices[s]["close"][i] for s,p in positions.items())
        max_gross=max(max_gross,gross/cash if cash>0 else float("inf"))
        pay=gross*FUNDING_4H
        funding+=pay;cash-=pay
        if cash<=0:
            liquidated=True;cash=0.;max_dd=1.;break
        peak=max(peak,cash)
        max_dd=max(max_dd,1-cash/peak)
        if i==end-1:break

        # Schedule owner-specific expiration for next 4H open.
        for sym,p in positions.items():
            if i-p["opened"]+1>=p["hold_bars"]:
                next_exits.add(sym)

        day=i//6 if i%6==5 else i//6-1
        if day<base.WARMUP:continue
        # 1D signal at final 4H close, no daily lookahead; all ranked by RSI2.
        if enable_rsi and i%6==5:
            ranked=[]
            for sym in base.SYMBOLS:
                if sym in positions:continue
                d=daily[sym]
                if not companion.finite(d["rsi"][day],d["close"][day],d["sma200"][day],d["atr14"][day]):continue
                if d["rsi"][day]>=3 or d["close"][day]<=d["sma200"][day]:continue
                if not diagnostic.entry_filter(daily,sym,day,"volume_btc_high_vol"):continue
                if i<=cooldown.get(sym,-1):continue
                ranked.append((d["rsi"][day],sym))
            ranked.sort()
            counters["rsi_signals"]+=len(ranked)
            next_entries.extend(("RSI2_1D",sym,3.,3.,20) for _,sym in ranked)

        if enable_breakout:
            ranked=[]
            for sym in base.SYMBOLS:
                if sym in positions:continue
                d=daily[sym]; b=signals4h[sym]
                if not companion.finite(b["rsi"][i],d["sma200"][day],d["close"][day],d["atr14"][day]):continue
                if b["rsi"][i]>=1. or d["close"][day]<=d["sma200"][day]:continue
                if not diagnostic.entry_filter(daily,sym,day,"volume_btc_high_vol"):continue
                if i<=cooldown.get(sym,-1):continue
                ranked.append((b["rsi"][i],sym))
            ranked.sort()
            counters["breakout_signals"]+=len(ranked)
            next_entries.extend(("COMPANION_4H",sym,2.,2.,5) for _,sym in ranked)

    if not liquidated:
        for sym in sorted(list(positions)):
            close(sym,prices[sym]["close"][end-1],"window_end",intra=False)
        max_dd=max(max_dd,1-cash/peak)
    stats={}
    for owner in ("RSI2_1D","COMPANION_4H"):
        group=[x for x in ledger if x["owner"]==owner]
        stats[owner]={"trades":len(group),"net_trade_pnl_usdt":round(sum(x["net_usdt"] for x in group),3),
                      "wins":sum(x["net_usdt"]>0 for x in group)}
    return {"net_return_pct":round(100*(cash/START-1),3),"max_dd_pct":round(100*max_dd,3),
            "end_balance_usdt":round(cash,3),"trades":len(ledger),
            "total_fees_usdt":round(fees,3),"funding_usdt":round(funding,3),
            "liquidated_proxy":liquidated,"max_gross_equity_mult":round(max_gross,3),
            "by_strategy":stats,"selection":counters,"trade_ledger":ledger}

def main():
    if universe.market.MARKET_SOURCE!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    snapshot=json.loads((Path(__file__).parent/"rsi2_frozen_universe_20261117.json").read_text())
    chosen=tuple(snapshot["symbols"])
    boundary=int(datetime.fromisoformat(snapshot["boundary_utc"].replace("Z","+00:00")).timestamp()*1000)
    frames={}
    for sym in chosen:
        for attempt in range(3):
            try:
                raw=universe.fetch_partial(sym,boundary)
                frames[sym]=universe.pad(raw,boundary)
                break
            except Exception as e:
                if attempt==2:
                    print(json.dumps({"missing":sym,"reason":str(e)[:180]}),file=sys.stderr)
                    raise
                time.sleep(2*(attempt+1))
    old=base.SYMBOLS
    try:
        base.SYMBOLS=chosen
        daily,times=diagnostic.prepare(frames)
        adaptive.attach_atr(daily,frames)
        price4h={sym:{k:frames[sym][k].astype(float).tolist()
                       for k in ("open","high","low","close")} for sym in chosen}
        breakout=companion.build_4h(frames,daily,30)
        cut=int(len(times)*.60)
        windows={"train":(base.WARMUP+1,cut),
                 "later":(cut+20,len(times))}
        result={}
        for label,(lo,hi) in windows.items():
            result[label]={}
            for name,rs,bk in (("rsi2_only",True,False),
                               ("breakout_only",False,True),
                               ("combined",True,True)):
                result[label][name]=run(price4h,daily,breakout,lo*6,hi*6,rs,bk)
        print(json.dumps({"study":"Shared-wallet RSI2 1D and breakout 4H portfolio; exact frozen 100 Bybit symbols and 2026-11-17 boundary",
            "run_at_utc":datetime.now(timezone.utc).isoformat(),"periods":result,
            "risk":{"per_position_pct":1,"max_aggregate_stop_risk_pct":3,
                    "max_positions":3,"gross_notional_equity_cap":1.5,
                    "priority":"RSI2_1D over COMPANION_4H; existing owner never replaced"},
            "fees_bps_per_side":16,"funding_bps_per_day":1,
            "limits":["No historical funding or real mark price liquidation",
                      "Historical data reused; survivor selection; no true OOS",
                      "Portfolio model uses simultaneous 4H OHLC stop-first execution"],
            "universe":chosen},sort_keys=True,allow_nan=False))
    finally:
        base.SYMBOLS=old

if __name__=="__main__":main()
