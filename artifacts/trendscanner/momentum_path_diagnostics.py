"""Research-only forensic path analysis of frozen 4H momentum trades.

No strategy changes. Describes what happened AFTER an already logged trade:
first candle directional excursion, in-trade MFE/MAE, stop-hit recovery,
post-exit directional price move over six bars and hypothetical time exit.
These are ex-post diagnostics, never predictors or actionable entry features.
All runs on public historical Bybit OHLC; no secrets or orders.
"""
from __future__ import annotations
from collections import defaultdict
import json
import math
import os
import statistics
import time
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import momentum_loss_audit as base
from prospective_market_data import get_bybit_data_before

FOLLOW_BARS=6
DIRECTION_MAP={"LONG":1,"SHORT":-1}


def path_for_trade(trade, instrument, last_index):
    """Reconstruct trade's candle range without assuming high/low order within bar."""
    index_by_time={stamp:i for i,stamp in enumerate(instrument["time"])}
    entry_index=index_by_time[trade["entry_ms"]]
    exit_index=index_by_time[trade["exit_ms"]]
    if exit_index<entry_index or exit_index>last_index:
        raise ValueError("TRADE_OUTSIDE_SPLIT")
    side=DIRECTION_MAP[trade["direction"]]
    price=instrument["open"][entry_index]
    if price<=0:raise ValueError("BAD_ENTRY_PRICE")
    highs=instrument["high"][entry_index:exit_index+1]
    lows=instrument["low"][entry_index:exit_index+1]
    mfe=(max(highs)/price-1)*100 if side==1 else (1-min(lows)/price)*100
    mae=(min(lows)/price-1)*100 if side==1 else (1-max(highs)/price)*100
    first_close=(instrument["close"][entry_index]/price-1)*side*100
    # The true intrabar sequence is not observable from 4H OHLC.
    # For final stop bar, MFE/MAE use whole bar and can overstate
    # reachable excursion before the actual stop fill.
    after_end=min(exit_index+FOLLOW_BARS,last_index)
    future_available=after_end==exit_index+FOLLOW_BARS
    exit_close=instrument["close"][exit_index]
    future_return=(instrument["close"][after_end]/exit_close-1)*side*100 if future_available else None
    hypothetical_index=min(entry_index+base.HOLD_BARS-1,last_index)
    hypothetical_full_window=hypothetical_index==entry_index+base.HOLD_BARS-1
    # Counterfactual: ignore stop and hold through exactly 6 bars, at market
    # close of sixth bar. Not achievable without changing risk and drawdown.
    hypothetical_no_stop_return=(
        (instrument["close"][hypothetical_index]/price-1)*side*100
        -2*base.COST_SIDE*100
    ) if hypothetical_full_window else None
    return {
        "symbol":trade["symbol"],"direction":trade["direction"],
        "entry_ms":trade["entry_ms"],"exit_ms":trade["exit_ms"],
        "net_trade_pct":trade["net_trade_pct"],
        "pnl_usdt":trade["pnl_usdt"],"exit_reason":trade["exit_reason"],
        "is_loss":trade["net_trade_pct"]<0,
        "first_4h_close_pct":round(first_close,5),
        "first_4h_against":first_close<0,
        "mfe_intrabar_pct":round(mfe,5),
        "mae_intrabar_pct":round(mae,5),
        "had_gross_1pct_favorable":mfe>=1.,
        "post_exit_24h_pct":round(future_return,5) if future_return is not None else None,
        "post_exit_favorable":future_return>0 if future_return is not None else None,
        "hypothetical_no_stop_24h_net_pct":(
            round(hypothetical_no_stop_return,5)
            if hypothetical_no_stop_return is not None else None),
        "entry_features":{"trend_alignment":trade["trend_alignment"],
                          "recent_conflict":trade["recent_conflict"],
                          "volatility":trade["volatility"]}
    }


def summarize(rows):
    if not rows:return {"trades":0,"distinct_entry_times":0}
    average=lambda field:round(statistics.mean(r[field] for r in rows),4)
    futures=[r for r in rows if r["post_exit_24h_pct"] is not None]
    hypothetical=[r for r in rows if r["hypothetical_no_stop_24h_net_pct"] is not None]
    return {
        "trades":len(rows),
        "distinct_entry_times":len({r["entry_ms"] for r in rows}),
        "mean_net_trade_pct":average("net_trade_pct"),
        "mean_first_4h_close_pct":average("first_4h_close_pct"),
        "first_4h_against_rate_pct":round(100*sum(r["first_4h_against"] for r in rows)/len(rows),2),
        "mfe_1pct_rate_pct":round(100*sum(r["had_gross_1pct_favorable"] for r in rows)/len(rows),2),
        "mean_mfe_pct":average("mfe_intrabar_pct"),
        "mean_mae_pct":average("mae_intrabar_pct"),
        "post_exit_24h_available":len(futures),
        "post_exit_favorable_rate_pct":round(100*sum(r["post_exit_favorable"] for r in futures)/len(futures),2) if futures else None,
        "mean_post_exit_24h_pct":round(statistics.mean(r["post_exit_24h_pct"] for r in futures),4) if futures else None,
        "no_stop_24h_evaluable":len(hypothetical),
        "no_stop_24h_positive_rate_pct":round(100*sum(r["hypothetical_no_stop_24h_net_pct"]>0 for r in hypothetical)/len(hypothetical),2) if hypothetical else None
    }


def audit_trade_rows(trades,data,split_end):
    idx={s:{t:i for i,t in enumerate(a["time"])} for s,a in data.items()}
    out=[]
    for trade in trades:
        sym=trade["symbol"]
        entry=trade["entry_ms"]
        exit_=trade["exit_ms"]
        if entry not in idx[sym] or exit_ not in idx[sym]:
            raise ValueError("UNKNOWN_TRADE_TIMESTAMP")
        out.append(path_for_trade(trade,data[sym],split_end-1))
    return out


def groups(rows):
    lost=[r for r in rows if r["is_loss"]]
    won=[r for r in rows if not r["is_loss"]]
    stops=[r for r in rows if r["exit_reason"]=="STOP"]
    stopped_losers=[r for r in stops if r["is_loss"]]
    timed_losers=[r for r in lost if r["exit_reason"]=="TIME"]
    return {"all":summarize(rows),"losses":summarize(lost),
            "nonlosses":summarize(won),
            "stops":summarize(stops),
            "stopped_losses":summarize(stopped_losers),
            "timed_losses":summarize(timed_losers),
            "losses_by_direction":{s:summarize([r for r in lost if r["direction"]==s])
                                   for s in DIRECTION_MAP},
            "losses_by_trend":{s:summarize([r for r in lost if r["entry_features"]["trend_alignment"]==s])
                               for s in ("aligned","counter")}}


def evaluate(frames):
    data=base.features(frames)
    cut=int(base.BARS*.6)
    periods={"train":(base.WARMUP,cut),"holdout":(cut+20,base.BARS)}
    report={}
    for split,(start,end) in periods.items():
        report[split]={}
        for model in base.MODELS:
            res=base.backtest(data,model,start,end)
            paths=audit_trade_rows(res["trades"],data,end)
            if len(paths)!=res["metrics"]["trades"]:
                raise ValueError("LOST_TRADE_RECORDS")
            report[split][model]={"portfolio":res["metrics"],
                                  "path_diagnostics":groups(paths),
                                  "trade_paths":paths}
    return {"version":"momentum-expost-path-diagnostics-v1",
            "range":{"start":data[base.SYMBOLS[0]]["time"][0],
                     "train_last":data[base.SYMBOLS[0]]["time"][cut-1],
                     "end":data[base.SYMBOLS[0]]["time"][-1]},
            "method":{"entry":"frozen original momentum backtest, next 4H open",
                      "path":"MFE/MAE use entire stop candle; actual within-bar sequence unobservable",
                      "first_4h_close":"directional first-bar close against entry",
                      "post_exit_24h":"directional six 4H closes AFTER exit; only if entire window exists within same split",
                      "no_stop_counterfactual":"hypothetical fixed 6-bar exit ignoring hard stops; NOT investable or risk matched",
                      "selection":"post-event buckets conditioned on outcome; NEVER treated as causal, predictive or as a strategy filter"},
            "results":report}


def main():
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//base.TF*base.TF
    frames={s:get_bybit_data_before(s,"4h",boundary,total_limit=base.BARS)
            for s in base.SYMBOLS}
    print(json.dumps(evaluate(frames),sort_keys=True))


if __name__=="__main__":
    main()
