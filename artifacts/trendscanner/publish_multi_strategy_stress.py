"""Readable stress-test summary for seven pre-selected strategies.

Stress screens are descriptive only, NOT unseen holdout validation.
"""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("input")
p.add_argument("--json-out",required=True)
p.add_argument("--md-out",required=True)
p.add_argument("--run-url",required=True)
a=p.parse_args()
raw=json.loads(Path(a.input).read_text())
periods=raw["periods"]
names=raw["scenarios"]
compact={}
for name in names:
    compact[name]={}
    for period in ("train","later"):
        v=periods[period][name]
        keys=("net_return_pct","max_dd_pct","trades","total_fees_usdt",
              "funding_usdt","liquidated_proxy","by_strategy","selection","parameters")
        compact[name][period]={k:v[k] for k in keys}
def label(n):
    return n.split("__")[0]
labels=sorted({label(n) for n in names})
def lookup(name,group,cost,period):
    return compact[f"{name}__{group}__cost{cost}bps"][period]
def screen(name):
    wins=0
    for group in ("original12","other_even","other_odd"):
        for bps in (16,32,48):
            t=lookup(name,group,bps,"train")
            l=lookup(name,group,bps,"later")
            if t["net_return_pct"]>0 and l["net_return_pct"]>0:
                wins+=1
    return wins
rank=sorted(labels,key=lambda name:(
     min(lookup(name,"all100",c,"train")["net_return_pct"] for c in (16,32,48)),
     min(lookup(name,"all100",c,"later")["net_return_pct"] for c in (16,32,48))),reverse=True)
lines=[
"# Multi-strategy stress matrix — 84 scenarios, research only","",
f"Run: {a.run_url}","",
"**Seven prior candidates × four disjoint/full coin subsets × 16/32/48 bps all-in cost per side.**",
"All-in cost = estimated commission + assumed proportional slippage, not real fills.",
"Data end 2026-11-17. Both temporal windows already researched, NOT untouched OOS.","",
"## Full 100-symbol universe — profitability versus cost",
"| Candidate | Early 16bp | Later 16bp | Later 32bp | Later 48bp | Later DD 48bp | Later trades 48bp | Subset positive pairs / 9 |",
"|---|---:|---:|---:|---:|---:|---:|---:|"]
for name in rank:
    e=lookup(name,"all100",16,"train")
    x=lookup(name,"all100",16,"later")
    y=lookup(name,"all100",32,"later")
    z=lookup(name,"all100",48,"later")
    lines.append(f"| {name} | {e['net_return_pct']:+.2f}% | {x['net_return_pct']:+.2f}% | {y['net_return_pct']:+.2f}% | {z['net_return_pct']:+.2f}% | {z['max_dd_pct']:.2f}% | {z['trades']} | {screen(name)}/9 |")
lines.extend(["","## Subsets — later period (all-in 32bps per side)",
              "| Candidate | Original 12 | Other group A | Other group B |",
              "|---|---:|---:|---:|"])
for name in rank:
    cells=[lookup(name,group,32,"later")["net_return_pct"] for group in
           ("original12","other_even","other_odd")]
    lines.append(f"| {name} | "+ " | ".join(f"{v:+.2f}%" for v in cells)+" |")
lines+=["","## Research interpretation","",
        "Subset positivity counts are sensitivity diagnostics, not independent samples or a statistical significance test.",
        "The coin lists remain selected by later survival and historical windows are reused.",
        "Do not send live orders on the basis of this matrix.",
        "The underlying same-account replay uses OHLC stop-first and simplified funding/cross liquidation.",
        "A genuine untouched forward period must be collected after parameter selection."]
summary={"study":raw["study"],"run_url":a.run_url,"cost_model":raw["cost_model"],
         "symbol_groups":raw["symbol_groups"],"ranked_candidates":rank,
         "subset_pair_positive_counts":{name:screen(name) for name in labels},
         "scenarios":compact}
Path(a.json_out).parent.mkdir(parents=True,exist_ok=True)
Path(a.json_out).write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
Path(a.md_out).write_text("\n".join(lines)+"\n")
