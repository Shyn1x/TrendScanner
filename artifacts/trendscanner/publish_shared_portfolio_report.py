"""Publish compact 1D+4H shared-wallet comparison without raw trade ledgers."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("input")
p.add_argument("--json-out",required=True)
p.add_argument("--md-out",required=True)
p.add_argument("--run-url",required=True)
a=p.parse_args()
d=json.loads(Path(a.input).read_text())
compact={"study":d["study"],"run_url":a.run_url,"risk":d["risk"],
         "periods":{}, "limits":d["limits"]}
cols=("net_return_pct","max_dd_pct","end_balance_usdt","trades",
      "total_fees_usdt","funding_usdt","liquidated_proxy",
      "max_gross_equity_mult","by_strategy","selection")
for period,methods in d["periods"].items():
    compact["periods"][period]={m:{k:s[k] for k in cols} for m,s in methods.items()}
    for mode,record in compact["periods"][period].items():
        if record["liquidated_proxy"]:
            raise SystemExit(f"LIQUIDATION_IN_{period}_{mode}")
lines=["# RSI2 1D + 4H breakout — shared-account portfolio (research only)",
       "",f"Run: {a.run_url}","",
       "Same frozen 100 Bybit symbols, 2026-11-17 boundary, single $300 wallet.",
       "Limits: three owned positions, 1% fee-inclusive stop risk per entry,",
       "3% aggregate stop risk, 1.5x gross notional/equity cap. RSI2 first.","",
       "| Test | Early return | Early DD | Early trades | Later return | Later DD | Later trades |",
       "|---|---:|---:|---:|---:|---:|---:|"]
for m in ("rsi2_only","breakout_only","combined"):
    tr=compact["periods"]["train"][m]
    la=compact["periods"]["later"][m]
    lines.append(f"| {m} | {tr['net_return_pct']:+.3f}% | {tr['max_dd_pct']:.3f}% | {tr['trades']} | {la['net_return_pct']:+.3f}% | {la['max_dd_pct']:.3f}% | {la['trades']} |")
lines+=["","## Combined strategy attribution"]
for period in ("train","later"):
    stat=compact["periods"][period]["combined"]
    lines.append(f"- {period}: "+", ".join(f"{name}: {v['trades']} trades, trade P&L ${v['net_trade_pnl_usdt']:+.3f}" for name,v in stat["by_strategy"].items()))
    lines.append(f"  Entry selections: {stat['selection']}")
lines+=["","*Both windows are reused exploratory historical data. Survivorship bias, simplified fills, missing actual historical funding, cross-margin and mark-price liquidation. No live orders.*"]
Path(a["json-out"] if isinstance(a,dict) else a.json_out).parent.mkdir(parents=True,exist_ok=True)
Path(a.json_out).write_text(json.dumps(compact,indent=2,sort_keys=True)+"\n")
Path(a.md_out).write_text("\n".join(lines)+"\n")
