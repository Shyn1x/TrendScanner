"""Rank batch experiments by balanced risk/reward, without claiming blind OOS."""
import argparse, json
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument("input")
parser.add_argument("--json-out",required=True)
parser.add_argument("--md-out",required=True)
parser.add_argument("--run-url",required=True)
a=parser.parse_args()
raw=json.loads(Path(a.input).read_text())
windows=raw["periods"]
compact={}
for name in raw["scenarios"]:
    out={}
    for period in ("train","later"):
        result=windows[period][name]
        out[period]={key:result[key] for key in
            ("net_return_pct","max_dd_pct","trades","total_fees_usdt",
             "funding_usdt","liquidated_proxy","by_strategy","selection")}
    compact[name]=out
baseline=compact["rsi2_only"]
late_base=baseline["later"]
def ranking(item):
    name,s=item
    earlier=s["train"]; later=s["later"]
    if earlier["liquidated_proxy"] or later["liquidated_proxy"]:return (-999999,-999999,0)
    min_profit=min(earlier["net_return_pct"],later["net_return_pct"])
    dd=max(earlier["max_dd_pct"],later["max_dd_pct"])
    return (round(min_profit-.50*dd,3),min_profit,-dd)
candidates=sorted(compact.items(),key=ranking,reverse=True)
def credible(s):
    e=s["train"];l=s["later"]
    return (not e["liquidated_proxy"] and not l["liquidated_proxy"]
        and e["net_return_pct"]>0 and l["net_return_pct"]>0
        and l["max_dd_pct"]<=late_base["max_dd_pct"]+3
        and l["trades"]>late_base["trades"])
passed=[(n,p) for n,p in candidates if credible(p)]
lines=["# GitHub-inspired strategy adaptations batch — 123 scenarios (not source-exact)","",
       f"Run: {a.run_url}","","123 scenarios: 15 different 4H signal families, 10 adapted from public code concepts",
       "with 1/2 companion slots, 0.5/1% companion stop risk and 2R/3R exits;",
       "plus RSI-only, breakout-only and unrestricted combined controls.",
       "Same 100 survivorship-selected symbols and 2026-11-17 historical cutoff.","",
       "## All variants, ranked by min(train,later) return minus 0.5 × worst drawdown",
       "| Scenario | Early return | Early DD | Early trades | Later return | Later DD | Later trades |",
       "|---|---:|---:|---:|---:|---:|---:|"]
for name,stats in candidates:
    t=stats["train"];l=stats["later"]
    lines.append(f"| {name} | {t['net_return_pct']:+.3f}% | {t['max_dd_pct']:.3f}% | {t['trades']} | {l['net_return_pct']:+.3f}% | {l['max_dd_pct']:.3f}% | {l['trades']} |")
lines.extend(["","## Research screen (not proof)","",
    "Pass = positive on both reused windows, late DD no more than baseline +3pp,",
    "and later trade count above RSI2-only. Passing variants: "+str(len(passed))+"."])
for name,stat in passed[:10]:
    e=stat["train"];l=stat["later"]
    lines.append(f"- {name}: earlier {e['net_return_pct']:+.2f}%, later {l['net_return_pct']:+.2f}%, later DD {l['max_dd_pct']:.2f}%, {l['trades']} trades")
lines+=["","## Limitations","",
        "**Important:** 4H conceptual adaptations of TrendRider public 1H entry categories and generic Freqtrade indicator strategies; NOT byte-for-byte original strategies. Original multi-timeframe, ADX, volume, OBV, ROI/exit, regime and execution are not reproduced.",
        "Source concepts: https://github.com/darkvolg/Trading and https://github.com/freqtrade/freqtrade-strategies",
        "", "*Exploratory in-sample selection over already reused historical windows.",
        "Not a portfolio/live recommendation. Same OHLC stop-first, simulated cross",
        "risk, survivorship-biased universe, and hypothetical funding assumptions.*"]
out={"study":raw["study"],"run_url":a.run_url,"baseline_later":late_base,
     "ranking_formula":"min(train,later) return - 0.5 * max(train,later) DD",
     "passed_screen":len(passed),"passed_names":[name for name,_ in passed],
     "scenarios":compact}
Path(a.json_out).parent.mkdir(parents=True,exist_ok=True)
Path(a.json_out).write_text(json.dumps(out,sort_keys=True,indent=2)+"\n")
Path(a.md_out).write_text("\n".join(lines)+"\n")
