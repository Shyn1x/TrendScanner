"""Summarize EXISTING read-only audit output; no exchange or database access."""
import json
import statistics
import sys
from collections import Counter
from decimal import Decimal


def summarize(report):
    samples=[]
    issues=Counter()
    for trade in report.get("trades",[]):
        issues.update(trade.get("issues",[]))
        item={}
        for side in ("entry","exit"):
            fills=trade.get(side+"_fills")
            fee=trade.get(side+"_fees_usdt")
            if not fills or fee is None:
                continue
            notional=Decimal(str(fills["quantity"]))*Decimal(str(fills["average_price"]))
            if notional>0:
                item[side+"_fee_bps"]=float(Decimal(str(fee))/notional*10000)
        if item:samples.append(item)
    return {
        "audit_status":report.get("comparison_status"),
        "read_only":report.get("read_only"),
        "total_journal_trades":report.get("journal_trades"),
        "journal_status_counts":report.get("journal_status_counts"),
        "journal_failure_codes":report.get("journal_failure_codes"),
        "eligible_recent_trades":report.get("eligible_detail_trades"),
        "detail_checked":report.get("attempted_detail_trades"),
        "matched_closed_trades_no_issues":report.get("matched_closed_trades"),
        "issues_counts":dict(issues),
        "errors_count":len(report.get("errors",[])),
        "mean_entry_fee_bps":round(statistics.mean(x["entry_fee_bps"] for x in samples if "entry_fee_bps" in x),3) if any("entry_fee_bps" in x for x in samples) else None,
        "mean_exit_fee_bps":round(statistics.mean(x["exit_fee_bps"] for x in samples if "exit_fee_bps" in x),3) if any("exit_fee_bps" in x for x in samples) else None,
        "entry_fee_samples":sum("entry_fee_bps" in x for x in samples),
        "exit_fee_samples":sum("exit_fee_bps" in x for x in samples),
        "funding_observed":sum(t.get("funding_cashflow_usdt") is not None for t in report.get("trades",[])),
        "funding_unknown":sum(t.get("funding_cashflow_usdt") is None for t in report.get("trades",[])),
        "entry_delay_samples":sum("entry_delay_ms" in t for t in report.get("trades",[])),
        "entry_delay_median_seconds":round(statistics.median(float(t["entry_delay_ms"])/1000 for t in report["trades"] if "entry_delay_ms" in t),2) if any("entry_delay_ms" in t for t in report.get("trades",[])) else None,
        "warning":"Bybit Demo historical 1H executor fills only; NOT live fees for 4H hypothetical strategy, and timing drift is not pure slippage."
    }


def main():
    report=json.load(sys.stdin)
    print(json.dumps(summarize(report),sort_keys=True))


if __name__=="__main__":main()
