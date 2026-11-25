"""Research-only shared-wallet 4H simulation: RSI2 daily + 4H trend breakout.

No real orders. Signal on closed bar, next 4H open; same asset never has
two strategy owners. One shared cash/equity book and shared stop-risk limit.
Stop priority over target within same 4H bar; no fake intrabar sequencing.
"""
import json, math, statistics, time, sys
import pandas as pd
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

def make_signal_family(frames,kind):
    """Closed 4H bar signal; entry always on the following 4H open."""
    if kind in ("breakout30","breakout60"):
        return companion.build_4h(frames,{},int(kind.replace("breakout","")))
    if kind not in ("ema_cross","pullback_reclaim","range_breakout"):
        raise ValueError("UNKNOWN_FAMILY")
    output={}
    for sym in base.SYMBOLS:
        f=frames[sym]
        close=f["close"].astype(float).reset_index(drop=True)
        high=f["high"].astype(float).reset_index(drop=True)
        low=f["low"].astype(float).reset_index(drop=True)
        ema20=close.ewm(span=20,adjust=False,min_periods=50).mean()
        ema50=close.ewm(span=50,adjust=False,min_periods=80).mean()
        if kind=="ema_cross":
            event=(ema20>ema50)&(ema20.shift(1)<=ema50.shift(1))&(close>ema20)
            score=-(ema20/ema50-1.)
        elif kind=="pullback_reclaim":
            # Reclaim 20EMA after a completed prior 4H dip; trend remains rising.
            event=(close>ema20)&(close.shift(1)<ema20.shift(1))&(ema20>ema50)&(ema20>ema20.shift(6))
            score=-(close/ema20-1.)
        else:
            # 20-bar Donchian high AFTER a volatility contraction, completed data.
            upper=high.rolling(20,min_periods=20).max().shift(1)
            old_range=(high.rolling(20,min_periods=20).max()-
                       low.rolling(20,min_periods=20).min()).shift(1)
            longer_range=(high.rolling(80,min_periods=80).max()-
                          low.rolling(80,min_periods=80).min()).shift(1)
            compressed=(old_range/close.shift(1))<(longer_range/close.shift(1))*.45
            event=(close>upper)&(close.shift(1)<=upper.shift(1))&compressed
            score=-(close/upper-1.)
        output[sym]={"rsi":score.where(event,50.).fillna(50.).tolist()}
    return output


def external_source_signals(frames,kind):
    """4H adaptations of public Freqtrade/TrendRider 1H entry families.

    NOT exact ports: no TA-Lib ADX, volume, OBV, external FNG/positions,
    dynamic strategy ROI or original informative 1H BTC filters. Signals use
    completed 4H bar and next 4H open; all share research stop/TP model.
    """
    names=("tr_pullback","tr_ema50_bounce","tr_rsi_bounce",
           "tr_ema_cross","tr_bb_bounce","tr_macd_cross",
           "ft_rsi_revert","ft_macd_bull","ft_bb_revert","ft_ema_trend")
    if kind not in names:raise ValueError("UNKNOWN_EXTERNAL_ADAPTATION")
    result={}
    for sym in base.SYMBOLS:
        f=frames[sym]
        c=f["close"].astype(float).reset_index(drop=True)
        o=f["open"].astype(float).reset_index(drop=True)
        h=f["high"].astype(float).reset_index(drop=True)
        l=f["low"].astype(float).reset_index(drop=True)
        diff=c.diff()
        gain=diff.clip(lower=0).ewm(alpha=1/16,adjust=False,min_periods=16).mean()
        loss=(-diff.clip(upper=0)).ewm(alpha=1/16,adjust=False,min_periods=16).mean()
        rsi=100*gain/(gain+loss)
        e9=c.ewm(span=9,adjust=False,min_periods=30).mean()
        e16=c.ewm(span=16,adjust=False,min_periods=30).mean()
        e21=c.ewm(span=21,adjust=False,min_periods=30).mean()
        e50=c.ewm(span=50,adjust=False,min_periods=80).mean()
        e200=c.ewm(span=200,adjust=False,min_periods=210).mean()
        macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
        hist=macd-macd.ewm(span=9,adjust=False).mean()
        mid=c.rolling(20,min_periods=20).mean()
        std=c.rolling(20,min_periods=20).std()
        lower=mid-2*std
        trend=(c>e200)&(e50>e200)
        if kind=="tr_pullback":
            event=trend&(l<=1.02*e21)&(c>e21)&(c>o)&rsi.between(30,65)&(rsi<70)
        elif kind=="tr_ema50_bounce":
            event=trend&(l<=1.01*e50)&(c>e50)&(c>o)&rsi.between(30,50)&(hist>hist.shift(1))
        elif kind=="tr_rsi_bounce":
            event=(c>e200)&(rsi.shift(1)<35)&(rsi>35)&(c>lower)&(c>o)
        elif kind=="tr_ema_cross":
            event=(e9>e16)&(e9.shift(1)<=e16.shift(1))&rsi.between(40,75)&(c>e200)
        elif kind=="tr_bb_bounce":
            event=(c<=lower*1.005)&(c>o)&(rsi<45)
        elif kind=="tr_macd_cross":
            event=(hist>0)&(hist.shift(1)<=0)&(c>e50)&(c>e200)&rsi.between(40,60)
        elif kind=="ft_rsi_revert":
            event=(rsi.shift(1)<30)&(rsi>=30)&(c>e200)
        elif kind=="ft_macd_bull":
            event=(macd>0)&(macd.shift(1)<=0)&(c>e200)
        elif kind=="ft_bb_revert":
            event=(c.shift(1)<lower.shift(1))&(c>lower)&(c>e200)
        else:
            event=(e21>e50)&(e21.shift(1)<=e50.shift(1))&(c>e200)
        # Score deterministic, higher relative RSI strength first.
        score=-(rsi/100.)
        result[sym]={"rsi":score.where(event,50.).fillna(50.).tolist()}
    return result


def run(prices,daily,signals4h,start,end,enable_rsi=True,enable_breakout=True,
        max_companion=3,companion_risk_pct=1.,companion_target=2.,
        companion_hold_days=5):
    """Shared account, same fills/risk rules across signal families."""
    if not 0<=max_companion<=3 or not 0<companion_risk_pct<=1:
        raise ValueError("BAD_COMPANION_POLICY")
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
        for owner,sym,stop_mult,target_r,hold_days,score in next_entries:
            if sym in positions:
                counters["blocked_owned"]+=1;continue
            px=prices[sym]["open"][i]
            atr=daily[sym]["atr14"][(i-1)//6]
            if not companion.finite(px,atr) or px<=0 or atr<=0:continue
            stop=max(1e-12,px-stop_mult*atr)
            if stop>=px:continue
            per_notional=(1+FEE)-(stop/px)*(1-FEE)
            risk_room=min((.01 if owner=="RSI2_1D" else .01*companion_risk_pct)*cash,
                          MAX_RISK*cash-sum(
                 max(0.,p["qty"]*(p["entry"]*(1+FEE)-p["stop"]*(1-FEE)))
                 for p in positions.values()))
            existing_gross=sum(p["qty"]*prices[s]["open"][i] for s,p in positions.items())
            gross_room=max(0.,MAX_GROSS_MULT*cash-existing_gross)
            size=min(.5*cash,gross_room,risk_room/per_notional if per_notional>0 else 0.)
            if size<=1e-8:continue
            candidates.append((owner,sym,px,stop,size,per_notional,target_r,hold_days,score))
        next_entries=[]
        proposals=[Proposal(sym,owner,"LONG",size*per_notional,size,score)
                   for owner,sym,px,stop,size,unit,target,hold,score in candidates]
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
                 for owner,sym,px,stop,size,unit,target_r,hold_days,score in candidates}
        companion_open=sum(p["owner"]=="COMPANION_4H" for p in positions.values())
        for prop in accepted:
            if prop.owner=="COMPANION_4H":
                if companion_open>=max_companion:
                    counters["blocked_capacity"]+=1
                    continue
                companion_open+=1
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
            next_entries.extend(("RSI2_1D",sym,3.,3.,20,rank) for rank,sym in ranked)

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
            next_entries.extend(("COMPANION_4H",sym,2.,companion_target,companion_hold_days,rank) for rank,sym in ranked)

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
        # A single fetch and feature preparation feeds the entire batch.
        families={kind:make_signal_family(frames,kind) for kind in
                  ("breakout30","breakout60","ema_cross","pullback_reclaim","range_breakout")}
        cut=int(len(times)*.60)
        windows={"train":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
        # Original-source adaptations on 4H. Explicitly NOT native 1H Freqtrade backtests.
        for kind in ("tr_pullback","tr_ema50_bounce","tr_rsi_bounce",
                     "tr_ema_cross","tr_bb_bounce","tr_macd_cross",
                     "ft_rsi_revert","ft_macd_bull","ft_bb_revert","ft_ema_trend"):
            families[kind]=external_source_signals(frames,kind)
        scenarios=[("rsi2_only",None,0,1.,2.,5),
                   ("breakout30_only","breakout30",3,1.,2.,5),
                   ("combined_unrestricted","breakout30",3,1.,2.,5)]
        # Family x dedicated companion slots x fixed risk size = 30 new comparisons.
        for kind in families:
            for companion_slots in (1,2):
                for risk_fraction in (.5,1.):
                    for target_r in (2.,3.):
                        # 5 types x 2 companion capacity x 2 risk x 2 targets = 40
                        name=f"{kind}_slots{companion_slots}_risk{risk_fraction:g}_tp{target_r:g}R"
                        scenarios.append((name,kind,companion_slots,risk_fraction,target_r,5))
        result={}
        for label,(lo,hi) in windows.items():
            result[label]={}
            for name,kind,nslots,risk_fraction,target_r,hold in scenarios:
                result[label][name]=run(price4h,daily,
                    families[kind] if kind else families["breakout30"],lo*6,hi*6,
                    enable_rsi=name!="breakout30_only",
                    enable_breakout=name!="rsi2_only",
                    max_companion=nslots,companion_risk_pct=risk_fraction,
                    companion_target=target_r,companion_hold_days=hold)
        print(json.dumps({"study":"123-case 4H strategy adaptations (including 10 Freqtrade/TrendRider-inspired signal families), frozen Bybit 100, research only",
            "run_at_utc":datetime.now(timezone.utc).isoformat(),"periods":result,
            "risk":{"per_position_pct":1,"max_aggregate_stop_risk_pct":3,
                    "max_positions":3,"gross_notional_equity_cap":1.5,
                    "priority":"RSI2_1D over COMPANION_4H; max 1/2 companion slots; existing owner never replaced"},
            "fees_bps_per_side":16,"funding_bps_per_day":1,
            "limits":["No historical funding or real mark price liquidation",
                      "Historical data reused; survivor selection; no true OOS",
                      "Portfolio model uses simultaneous 4H OHLC stop-first execution"],
            "universe":chosen,"source_repos":["https://github.com/darkvolg/Trading","https://github.com/freqtrade/freqtrade-strategies"],"source_fidelity":"conceptual 4H signal adaptations, not original 1H/native Freqtrade runs; original risk, volume, ADX, ROI not reproduced","scenarios":[x[0] for x in scenarios]},sort_keys=True,allow_nan=False))
    finally:
        base.SYMBOLS=old

if __name__=="__main__":main()
