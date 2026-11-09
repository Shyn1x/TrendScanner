"""Read-only diagnostics for prospective 4h Donchian signal outcomes.

Reports cohorts separately so 25 correlated altcoin signals at one close
aren't mistaken for 25 independent market observations.
No strategy modifications, orders, history backfills, or DB writes.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
import statistics

VERSION = "regime-donchian-4h-shadow-v1"


def summarize(records):
    cohorts = defaultdict(list)
    groups = defaultdict(list)
    pending = defaultdict(int)
    for r in records:
        key = (r["direction"], r["horizon"])
        if r["status"] != "SCORED":
            pending[(r["direction"], r["horizon"], r["status"])] += 1
            continue
        groups[key].append(r)
        cohorts[(r["direction"], r["horizon"], r["target_ms"])].append(r)
    aggregates = []
    for (direction, horizon), values in sorted(groups.items()):
        nets = [v["net_pct"] for v in values]
        grosses = [v["gross_pct"] for v in values]
        by_time = [statistics.mean([v["net_pct"] for v in c])
                   for (side, h, _), c in sorted(cohorts.items())
                   if side == direction and h == horizon]
        aggregates.append({
            "direction": direction, "horizon_bars": horizon,
            "signal_count": len(values), "independent_time_cohorts": len(by_time),
            "average_net_pct_ex_funding": round(statistics.mean(nets), 4),
            "average_gross_pct": round(statistics.mean(grosses), 4),
            "positive_rate_pct": round(100 * sum(v > 0 for v in nets) / len(nets), 2),
            "stopped_rate_pct": round(100 * sum(v["stop_hit"] for v in values) / len(values), 2),
            "median_net_pct": round(statistics.median(nets), 4),
            "average_of_cohort_means_pct": round(statistics.mean(by_time), 4),
            "cohorts_below_zero": sum(m < 0 for m in by_time),
        })
    cohort_rows = []
    for (direction, horizon, target), values in sorted(cohorts.items()):
        cohort_rows.append({
            "signal_timestamp_utc": datetime.fromtimestamp(target / 1000, timezone.utc).isoformat(),
            "direction": direction, "horizon_bars": horizon,
            "signals": len(values),
            "avg_net_pct_ex_funding": round(statistics.mean(r["net_pct"] for r in values), 4),
            "stopped": sum(r["stop_hit"] for r in values),
            "positive": sum(r["net_pct"] > 0 for r in values),
        })
    return {
        "experiment": VERSION,
        "interpretation": "signal_horizon_pnl_not_portfolio; correlated_symbols; channel_exit_not_modeled",
        "fees_slippage_roundtrip_pct": 0.16,
        "funding_modeled": False,
        "by_direction_and_horizon": aggregates,
        "cohorts_by_signal_time": cohort_rows,
        "non_scored": [{"direction": d, "horizon_bars": h, "status": status, "count": n}
                       for (d,h,status),n in sorted(pending.items())],
    }


def read_records(database_url):
    import psycopg
    records = []
    with psycopg.connect(database_url, options="-c default_transaction_read_only=on") as db:
        with db.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = 10000")
            cur.execute("SELECT to_regclass('regime_donchian_shadow_signals'), "
                        "to_regclass('regime_donchian_shadow_outcomes')")
            if not all(cur.fetchone() or []):
                raise RuntimeError("SHADOW_TABLES_MISSING")
            cur.execute("""
              SELECT s.direction, s.target_ms, o.horizon,
                     o.result->>'status',
                     o.result->>'gross_return_pct',
                     o.result->>'net_return_pct',
                     o.result->>'stop_hit'
              FROM regime_donchian_shadow_signals s
              JOIN regime_donchian_shadow_outcomes o
                ON o.version=s.version AND o.symbol=s.symbol AND o.target_ms=s.target_ms
              WHERE s.version=%s ORDER BY s.target_ms,s.symbol,o.horizon
            """, (VERSION,))
            for side,target,horizon,status,gross,net,stopped in cur.fetchall():
                records.append({"direction":side,"target_ms":int(target),
                                "horizon":int(horizon),"status":status,
                                "gross_pct":float(gross) if gross is not None else None,
                                "net_pct":float(net) if net is not None else None,
                                "stop_hit":stopped == "true"})
            cur.execute("""
              SELECT direction, count(*), count(DISTINCT target_ms)
              FROM regime_donchian_shadow_signals WHERE version=%s
              GROUP BY direction
            """,(VERSION,))
            totals = [{"direction":side,"signals":n,"signal_time_cohorts":cohorts}
                      for side,n,cohorts in cur.fetchall()]
    return records, totals


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--report",action="store_true",required=True)
    parser.parse_args()
    if not os.getenv("DATABASE_URL"):
        raise SystemExit("DATABASE_URL_REQUIRED")
    records, totals = read_records(os.environ["DATABASE_URL"])
    report=summarize(records)
    report["total_signals_by_direction"]=totals
    print(json.dumps(report, sort_keys=True))


if __name__=="__main__":
    main()
