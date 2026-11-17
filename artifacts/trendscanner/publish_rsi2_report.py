"""Create a compact, reproducible machine-readable RSI2 result and Actions summary.

No trading. Read one backtest JSON, discard bulky per-trade ledgers and retain
period-level statistics, R metrics, and worst losses. No secret values.
"""
import argparse,json
from pathlib import Path

def concise(raw,run_url=""):
    out={"study":raw.get("study"),"end_utc":raw.get("end_utc"),
         "account_usdt":raw.get("account_usdt"),"run_url":run_url,
         "periods":raw.get("periods"),"limitations":raw.get("limitations"),
         "settings":{k:raw[k] for k in ("leverage","atr_daily_stop_mult",
             "risk_per_entry_pct","aggregate_stop_risk_pct","fee_bps_side",
             "hypothetical_funding_bps_daily") if k in raw},
         "results":{}}
    for case,periods in raw["results"].items():
        out["results"][case]={}
        for period,full in periods.items():
            out["results"][case][period]={
                key:value for key,value in full.items()
                if key not in ("trade_ledger","worst_losers")
            }
            out["results"][case][period]["worst_losers"]=full.get("worst_losers",[])[:5]
    return out

def markdown(out):
    lines=["# RSI2 research — latest run","",
           f"Run: {out['run_url'] or 'local'}",
           f"Source: {out.get('study') or 'unknown'}",
           f"Market end (UTC): {out.get('end_utc') or 'unknown'}","",
           "| Mode | Later return | Later DD | Trades | Train return | Train DD |",
           "|---|---:|---:|---:|---:|---:|"]
    rows=[]
    for key,periods in out["results"].items():
        later=periods.get("later",{});train=periods.get("train",{})
        if not later:continue
        rows.append((later.get("net_return_pct",-1e9),key,later,train))
    for _,key,later,train in sorted(rows,reverse=True):
        lines.append(f"| {key} | {later.get('net_return_pct','?')}% | "
                     f"{later.get('max_dd_pct','?')}% | {later.get('trades','?')} | "
                     f"{train.get('net_return_pct','?')}% | {train.get('max_dd_pct','?')}% |")
    lines.extend(["","*Exploratory backtest, not independent out-of-sample validation. "
                  "Unrealistic fills, current-universe selection and funding assumptions apply.*"])
    return "\n".join(lines)+"\n"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--json-out",required=True)
    ap.add_argument("--md-out",required=True)
    ap.add_argument("--run-url",default="")
    a=ap.parse_args()
    raw=json.loads(Path(a.source).read_text(encoding="utf-8"))
    result=concise(raw,a.run_url)
    Path(a.json_out).parent.mkdir(parents=True,exist_ok=True)
    Path(a.md_out).parent.mkdir(parents=True,exist_ok=True)
    Path(a.json_out).write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    Path(a.md_out).write_text(markdown(result),encoding="utf-8")

if __name__=="__main__":main()
