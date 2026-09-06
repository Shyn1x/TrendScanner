"""Temporal, cross-symbol, and factor stability diagnostic for READY H=5 events."""

from __future__ import annotations

import copy
import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SOURCE_PATH = "artifacts/trendscanner/ready_outcome_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_stability_report.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_stability_report.csv"
VALID_DIRECTIONS = ("LONG", "SHORT")
TIME_THIRDS = ("early", "middle", "late")
MIN_MONTH_DIRECTION_SAMPLE = 10
MIN_SYMBOL_COMPARISON_SAMPLE = 5
MIN_FACTOR_THIRD_SAMPLE = 8


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _date_label(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp / 1000, tz=UTC).isoformat()


def _month_label(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp / 1000, tz=UTC).strftime("%Y-%m")


def _bucket(value: float, edges: list[float], labels: list[str]) -> str:
    for index, edge in enumerate(edges):
        if value < edge:
            return labels[index]
    return labels[-1]


def _factor_values(record: dict[str, Any]) -> list[tuple[str, str]]:
    mappings = (
        ("confidence_bucket", "ready_confidence", [60.0, 70.0, 80.0], ["<60", "60-70", "70-80", "80+"]),
        ("decision_score_bucket", "ready_decision_score", [50.0, 60.0, 70.0, 80.0], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("breakout_score_bucket", "breakout_score", [50.0, 60.0, 70.0, 80.0], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("trend_quality_bucket", "trend_quality", [50.0, 60.0, 70.0, 80.0], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("volume_quality_bucket", "volume_quality", [40.0, 60.0, 80.0], ["<40", "40-60", "60-80", "80+"]),
        ("structure_quality_bucket", "structure_quality", [40.0, 60.0, 80.0], ["<40", "40-60", "60-80", "80+"]),
    )
    values: list[tuple[str, str]] = []
    for factor, field, edges, labels in mappings:
        value = _safe_float(record.get(field))
        if value is not None:
            values.append((factor, _bucket(value, edges, labels)))
    state = record.get("structure_state")
    if state is not None and str(state).strip():
        values.append(("structure_state", str(state).strip()))
    return values


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = [record["mfe_pct"] for record in records]
    mae = [record["mae_pct"] for record in records]
    close = [record["close_return_pct"] for record in records]
    avg_mfe = _mean(mfe)
    avg_mae = _mean(mae)
    return {
        "n": len(records),
        "avg_mfe_pct": avg_mfe,
        "avg_mae_pct": avg_mae,
        "mfe_minus_mae_pct": avg_mfe - avg_mae if avg_mfe is not None and avg_mae is not None else None,
        "avg_close_return_pct": _mean(close),
        "positive_close_rate": (sum(value > 0.0 for value in close) / len(close) * 100.0) if close else None,
        "mfe_ge_2_pct_rate": (sum(value >= 2.0 for value in mfe) / len(mfe) * 100.0) if mfe else None,
    }


def _records_from_payload(payload: dict[str, Any], horizon: int) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    skipped = 0
    for run in payload.get("runs", []) if isinstance(payload.get("runs"), list) else []:
        if not isinstance(run, dict) or run.get("status") != "OK":
            continue
        for event in run.get("events", []) if isinstance(run.get("events"), list) else []:
            if not isinstance(event, dict):
                skipped += 1
                continue
            direction = str(event.get("direction", "")).upper()
            timestamp = _safe_int(event.get("ready_timestamp"))
            symbol = str(event.get("symbol", "")).strip()
            horizons = event.get("horizons")
            outcome = horizons.get(str(horizon)) if isinstance(horizons, dict) else None
            if direction not in VALID_DIRECTIONS or timestamp is None or not symbol or not isinstance(outcome, dict):
                skipped += 1
                continue
            if not outcome.get("available"):
                continue
            mfe = _safe_float(outcome.get("mfe_pct"))
            mae = _safe_float(outcome.get("mae_pct"))
            close = _safe_float(outcome.get("close_return_pct"))
            if mfe is None or mae is None or close is None:
                skipped += 1
                continue
            record = dict(event)
            record.update({"direction": direction, "symbol": symbol, "ready_timestamp": timestamp, "mfe_pct": mfe, "mae_pct": mae, "close_return_pct": close})
            records.append(record)
    return sorted(records, key=lambda record: record["ready_timestamp"]), skipped


def _third_for_timestamp(timestamp: int, start: int, end: int) -> str:
    if start == end:
        return "late"
    span = (end - start) / 3.0
    if timestamp < start + span:
        return "early"
    if timestamp < start + 2.0 * span:
        return "middle"
    return "late"


def _time_thirds(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    if not records:
        return [], {name: [] for name in TIME_THIRDS}
    start, end = records[0]["ready_timestamp"], records[-1]["ready_timestamp"]
    grouped = {name: [] for name in TIME_THIRDS}
    for record in records:
        grouped[_third_for_timestamp(record["ready_timestamp"], start, end)].append(record)
    rows = []
    for name in TIME_THIRDS:
        subset = grouped[name]
        all_stats = _metrics(subset)
        long_records = [record for record in subset if record["direction"] == "LONG"]
        short_records = [record for record in subset if record["direction"] == "SHORT"]
        rows.append({
            "time_third": name,
            "window_start": _date_label(start if name == "early" else int(start + (TIME_THIRDS.index(name) * (end - start) / 3.0))),
            "window_end": _date_label(end if name == "late" else int(start + ((TIME_THIRDS.index(name) + 1) * (end - start) / 3.0))),
            "event_date_range": {"start": _date_label(subset[0]["ready_timestamp"]) if subset else None, "end": _date_label(subset[-1]["ready_timestamp"]) if subset else None},
            "n": all_stats["n"], "long_n": len(long_records), "short_n": len(short_records),
            "all": all_stats, "long": _metrics(long_records), "short": _metrics(short_records),
        })
    return rows, grouped


def _monthly(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[_month_label(record["ready_timestamp"])].append(record)
    rows = []
    for month, subset in sorted(groups.items()):
        long_records = [record for record in subset if record["direction"] == "LONG"]
        short_records = [record for record in subset if record["direction"] == "SHORT"]
        rows.append({"month": month, "n": len(subset), "long_n": len(long_records), "short_n": len(short_records),
                     "long": _metrics(long_records), "short": _metrics(short_records),
                     "long_low_sample": len(long_records) < MIN_MONTH_DIRECTION_SAMPLE,
                     "short_low_sample": len(short_records) < MIN_MONTH_DIRECTION_SAMPLE,
                     "eligible_for_ranking": len(long_records) >= MIN_MONTH_DIRECTION_SAMPLE and len(short_records) >= MIN_MONTH_DIRECTION_SAMPLE})
    return rows


def _symbols(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["symbol"]].append(record)
    rows = []
    comparable = []
    long_values, short_values = [], []
    for symbol, subset in sorted(groups.items()):
        long_records = [record for record in subset if record["direction"] == "LONG"]
        short_records = [record for record in subset if record["direction"] == "SHORT"]
        long_stats, short_stats = _metrics(long_records), _metrics(short_records)
        long_value, short_value = long_stats["mfe_minus_mae_pct"], short_stats["mfe_minus_mae_pct"]
        if long_value is not None:
            long_values.append(long_value)
        if short_value is not None:
            short_values.append(short_value)
        is_comparable = len(long_records) >= MIN_SYMBOL_COMPARISON_SAMPLE and len(short_records) >= MIN_SYMBOL_COMPARISON_SAMPLE
        if is_comparable:
            comparable.append((long_value, short_value))
        rows.append({"symbol": symbol, "total_n": len(subset), "long_n": len(long_records), "short_n": len(short_records), "long": long_stats, "short": short_stats, "comparable": is_comparable})
    return rows, {"symbols": len(rows), "long_mfe_minus_mae_positive_symbols": sum(value > 0.0 for value in long_values), "short_mfe_minus_mae_positive_symbols": sum(value > 0.0 for value in short_values), "long_greater_than_short_symbols": sum(long > short for long, short in comparable), "comparable_symbols": len(comparable), "median_long_mfe_minus_mae_pct": statistics.median(long_values) if long_values else None, "median_short_mfe_minus_mae_pct": statistics.median(short_values) if short_values else None}


def _factor_stability(thirds: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for third, records in thirds.items():
        for record in records:
            for factor, value in _factor_values(record):
                groups[(record["direction"], factor, value, third)].append(record)
    combined: dict[tuple[str, str, str], dict[str, Any]] = {}
    for direction, factor, value, third in groups:
        key = (direction, factor, value)
        combined.setdefault(key, {"direction": direction, "factor": factor, "factor_value": value, "thirds": {}})["thirds"][third] = _metrics(groups[(direction, factor, value, third)])
    all_groups = []
    for row in combined.values():
        for third in TIME_THIRDS:
            row["thirds"].setdefault(third, _metrics([]))
        row["minimum_sample_met"] = all(row["thirds"][third]["n"] >= MIN_FACTOR_THIRD_SAMPLE for third in TIME_THIRDS)
        all_groups.append(row)
    all_groups.sort(key=lambda row: (row["factor"], row["factor_value"], row["direction"]))
    eligible = [row for row in all_groups if row["minimum_sample_met"]]
    stable_positive = [row for row in eligible if all(row["thirds"][third]["mfe_minus_mae_pct"] > 0.0 for third in TIME_THIRDS)]
    stable_negative = [row for row in eligible if all(row["thirds"][third]["mfe_minus_mae_pct"] < 0.0 for third in TIME_THIRDS)]
    sign_changing = [row for row in eligible if any(row["thirds"][third]["mfe_minus_mae_pct"] > 0.0 for third in TIME_THIRDS) and any(row["thirds"][third]["mfe_minus_mae_pct"] < 0.0 for third in TIME_THIRDS)]
    return {"groups": all_groups, "stable_positive": stable_positive, "stable_negative": stable_negative, "sign_changing": sign_changing}


def _correlation(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_timestamp: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_timestamp[record["ready_timestamp"]].append(record)
        by_day[datetime.fromtimestamp(record["ready_timestamp"] / 1000, tz=UTC).strftime("%Y-%m-%d")].append(record)
    shared = sum(len(events) for events in by_timestamp.values() if len({event["symbol"] for event in events}) > 1)
    return {"unique_ready_timestamps": len(by_timestamp), "events_per_unique_timestamp": len(records) / len(by_timestamp) if by_timestamp else None, "max_symbols_at_one_timestamp": max((len({event["symbol"] for event in events}) for events in by_timestamp.values()), default=0), "shared_timestamp_event_share": shared / len(records) * 100.0 if records else None, "unique_active_days": len(by_day), "average_events_per_active_day": len(records) / len(by_day) if by_day else None, "max_events_in_one_day": max((len(events) for events in by_day.values()), default=0)}


def analyze_ready_stability(payload: dict[str, Any], horizon: int = 5) -> dict[str, Any]:
    source = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    records, skipped = _records_from_payload(source, horizon)
    thirds, third_records = _time_thirds(records)
    symbol_rows, symbol_summary = _symbols(records)
    return {"summary": {"horizon": horizon, "available_events": len(records), "skipped_malformed_events": skipped, "successful_runs": sum(isinstance(run, dict) and run.get("status") == "OK" for run in source.get("runs", []))}, "time_thirds": thirds, "monthly": _monthly(records), "per_symbol": {"rows": symbol_rows, "summary": symbol_summary}, "factor_stability": _factor_stability(third_records), "correlated_event_diagnostic": _correlation(records)}


def save_report_json(path: str | Path, report: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
    return target


def save_report_csv(path: str | Path, report: dict[str, Any]) -> Path:
    rows: list[dict[str, Any]] = []
    for third in report["time_thirds"]:
        for direction in ("ALL", "LONG", "SHORT"):
            stats = third[direction.lower()]
            rows.append({"section": "time_third", "key": third["time_third"], "direction": direction, **stats})
    for month in report["monthly"]:
        for direction in VALID_DIRECTIONS:
            stats = month[direction.lower()]
            rows.append({"section": "monthly", "key": month["month"], "direction": direction, "low_sample": month[f"{direction.lower()}_low_sample"], **stats})
    for symbol in report["per_symbol"]["rows"]:
        for direction in VALID_DIRECTIONS:
            rows.append({"section": "per_symbol", "key": symbol["symbol"], "direction": direction, "comparable": symbol["comparable"], **symbol[direction.lower()]})
    for group in report["factor_stability"]["groups"]:
        for third, stats in group["thirds"].items():
            rows.append({"section": "factor", "key": f"{group['factor']}={group['factor_value']}", "direction": group["direction"], "time_third": third, "minimum_sample_met": group["minimum_sample_met"], **stats})
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _factor_labels(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return "none"
    return "; ".join(f"{row['direction']} {row['factor']}={row['factor_value']}" for row in groups)


def build_console_summary(report: dict[str, Any]) -> str:
    lines = ["A. Time thirds LONG vs SHORT"]
    for row in report["time_thirds"]:
        lines.append(f"{row['time_third']}: N={row['n']} L={row['long_n']} { _fmt(row['long']['mfe_minus_mae_pct']) } / S={row['short_n']} { _fmt(row['short']['mfe_minus_mae_pct']) }")
    lines.append("B. Monthly LONG vs SHORT")
    for row in report["monthly"]:
        lines.append(f"{row['month']}: N={row['n']} L={row['long_n']} {_fmt(row['long']['mfe_minus_mae_pct'])} close={_fmt(row['long']['avg_close_return_pct'])} | S={row['short_n']} {_fmt(row['short']['mfe_minus_mae_pct'])} close={_fmt(row['short']['avg_close_return_pct'])}")
    summary = report["per_symbol"]["summary"]
    lines.extend(["C. Per-symbol summary", f"symbols={summary['symbols']} comparable={summary['comparable_symbols']} L>0={summary['long_mfe_minus_mae_positive_symbols']} S>0={summary['short_mfe_minus_mae_positive_symbols']} L>S={summary['long_greater_than_short_symbols']} median L/S={_fmt(summary['median_long_mfe_minus_mae_pct'])}/{_fmt(summary['median_short_mfe_minus_mae_pct'])}"])
    factors = report["factor_stability"]
    lines.extend([
        "D. Stable/unstable factors",
        f"stable positive ({len(factors['stable_positive'])}): {_factor_labels(factors['stable_positive'])}",
        f"stable negative ({len(factors['stable_negative'])}): {_factor_labels(factors['stable_negative'])}",
        f"sign changing ({len(factors['sign_changing'])}): {_factor_labels(factors['sign_changing'])}",
    ])
    diagnostic = report["correlated_event_diagnostic"]
    lines.extend(["E. Correlated-event diagnostic", f"timestamps={diagnostic['unique_ready_timestamps']} events/timestamp={_fmt(diagnostic['events_per_unique_timestamp'])} max symbols={diagnostic['max_symbols_at_one_timestamp']} shared={_fmt(diagnostic['shared_timestamp_event_share'])}% active days={diagnostic['unique_active_days']} avg/day={_fmt(diagnostic['average_events_per_active_day'])} max/day={diagnostic['max_events_in_one_day']}"])
    return "\n".join(lines)


def main() -> None:
    with Path(SOURCE_PATH).open("r", encoding="utf-8") as handle:
        report = analyze_ready_stability(json.load(handle))
    save_report_json(OUTPUT_JSON, report)
    save_report_csv(OUTPUT_CSV, report)
    print(build_console_summary(report))


if __name__ == "__main__":
    main()