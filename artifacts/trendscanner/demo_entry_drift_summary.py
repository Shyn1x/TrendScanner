"""Aggregate-only diagnostic of signed LONG drift from reference to Demo fill."""
from __future__ import annotations
import json
import statistics
import sys

def summarise(reports):
    rows=[t for report in reports for t in report.get("trades",[])]
    delays=[float(t["entry_delay_ms"])/1000 for t in rows
            if isinstance(t.get("entry_delay_ms"),(int,float))]
    gaps=[float(t["entry_gap_from_signal_close_bps"]) for t in rows
          if t.get("entry_gap_from_signal_close_bps") is not None]
    # All journal strategies here are 1H LONG; upward reference-to-fill drift is
    # harmful. NOT pure execution slippage: includes real price movement over delay.
    paired=[(float(t["entry_delay_ms"])/1000,
             float(t["entry_gap_from_signal_close_bps"]))
            for t in rows if t.get("entry_delay_ms") is not None
            and t.get("entry_gap_from_signal_close_bps") is not None]
    def distribution(v):
        if not v:return None
        v=sorted(v)
        return {"n":len(v),"median":round(statistics.median(v),3),
                "mean":round(statistics.mean(v),3),
                "p90_nearest_rank":round(v[(9*len(v)+9)//10-1],3),
                "min":round(v[0],3),"max":round(v[-1],3)}
    return {"read_only":True,"audit_batches":len(reports),"trades_checked":len(rows),
        "audit_errors":sum(len(r.get("errors",[])) for r in reports),
        "delay_seconds":distribution(delays),
        "reference_to_fill_bps_LONG":distribution(gaps),
        "paired_reference_and_delay":len(paired),
        "harmful_positive_gap_pct":round(100*sum(g>0 for g in gaps)/len(gaps),2) if gaps else None,
        "gap_over_20bps_count":sum(g>20 for g in gaps),
        "gap_over_50bps_count":sum(g>50 for g in gaps),
        "gap_over_100bps_count":sum(g>100 for g in gaps),
        "gap_under_minus_50bps_count":sum(g<-50 for g in gaps),
        "delay_over_4min_count":sum(s>240 for s in delays),
        "reference":"historical model.entry_reference_price (not independently verified exact signal close)",
        "warning":"Demo 1H LONG. Entry reference-to-fill drift is NOT pure exchange slippage; sample may be selected and lacks entry price benchmark from full 1m path."}

def main():
    reports=json.load(sys.stdin)
    print(json.dumps(summarise(reports),sort_keys=True))

if __name__=="__main__":main()
