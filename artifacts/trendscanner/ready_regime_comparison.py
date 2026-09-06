"""Descriptive regime comparison for independent CURRENT and HOLDOUT outcomes."""

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


CURRENT_PATH = "artifacts/trendscanner/ready_outcome_results.json"
HOLDOUT_PATH = "artifacts/trendscanner/ready_outcome_holdout_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_regime_comparison.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_regime_comparison.csv"
PERIODS = ("CURRENT", "HOLDOUT")
DIRECTIONS = ("LONG", "SHORT")
MIN_FACTOR_SAMPLE = 10
MIN_SYMBOL_SAMPLE = 5


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = [record["mfe_pct"] for record in records]
    mae = [record["mae_pct"] for record in records]
    close = [record["close_return_pct"] for record in records]
    avg_mfe = statistics.mean(mfe) if mfe else None
    avg_mae = statistics.mean(mae) if mae else None
    return {
        "n": len(records),
        "avg_mfe_pct": avg_mfe,
        "avg_mae_pct": avg_mae,
        "mfe_minus_mae_pct": avg_mfe - avg_mae if avg_mfe is not None and avg_mae is not None else None,
        "avg_close_return_pct": statistics.mean(close) if close else None,
        "positive_close_rate": sum(value > 0.0 for value in close) / len(close) * 100.0 if close else None,
        "mfe_ge_2_pct_rate": sum(value >= 2.0 for value in mfe) / len(mfe) * 100.0 if mfe else None,
    }


def _bucket(value: float, edges: list[float], labels: list[str]) -> str:
    for index, edge in enumerate(edges):
        if value < edge:
            return labels[index]
    return labels[-1]


def _factor_values(record: dict[str, Any]) -> list[tuple[str, str]]:
    numeric = (
        ("confidence_bucket", "ready_confidence", [60, 70, 80], ["<60", "60-70", "70-80", "80+"]),
        ("decision_score_bucket", "ready_decision_score", [50, 60, 70, 80], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("breakout_score_bucket", "breakout_score", [50, 60, 70, 80], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("trend_quality_bucket", "trend_quality", [50, 60, 70, 80], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("volume_quality_bucket", "volume_quality", [40, 60, 80], ["<40", "40-60", "60-80", "80+"]),
        ("structure_quality_bucket", "structure_quality", [40, 60, 80], ["<40", "40-60", "60-80", "80+"]),
    )
    factors = []
    for name, field, edges, labels in numeric:
        value = _safe_float(record.get(field))
        if value is not None:
            factors.append((name, _bucket(value, edges, labels)))
    state = str(record.get("structure_state") or "").strip().upper()
    if state:
        factors.append(("structure_state", state))
    warnings = record.get("ready_warnings")
    normalized_warnings = sorted({str(value).strip() for value in warnings if str(value).strip()}) if isinstance(warnings, list) else []
    for warning in normalized_warnings or ["NO_WARNINGS"]:
        factors.append(("warning", warning))
    return factors


def _load_records(payload: dict[str, Any], period: str) -> tuple[list[dict[str, Any]], int]:
    records, skipped = [], 0
    for run in payload.get("runs", []) if isinstance(payload.get("runs"), list) else []:
        if not isinstance(run, dict) or run.get("status") != "OK":
            continue
        for event in run.get("events", []) if isinstance(run.get("events"), list) else []:
            if not isinstance(event, dict):
                skipped += 1
                continue
            h5 = event.get("horizons", {}).get("5") if isinstance(event.get("horizons"), dict) else None
            direction = str(event.get("direction") or "").upper()
            timestamp = _safe_int(event.get("ready_timestamp"))
            symbol = str(event.get("symbol") or "").strip()
            if not isinstance(h5, dict) or not h5.get("available"):
                continue
            mfe, mae, close = (_safe_float(h5.get(name)) for name in ("mfe_pct", "mae_pct", "close_return_pct"))
            if direction not in DIRECTIONS or timestamp is None or not symbol or mfe is None or mae is None or close is None:
                skipped += 1
                continue
            record = dict(event)
            record.update({"period": period, "direction": direction, "symbol": symbol, "ready_timestamp": timestamp, "mfe_pct": mfe, "mae_pct": mae, "close_return_pct": close})
            records.append(record)
    return records, skipped


def _period_ranges(records: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for period in PERIODS:
        timestamps = [record["ready_timestamp"] for record in records if record["period"] == period]
        start, end = (min(timestamps), max(timestamps)) if timestamps else (None, None)
        result[period] = {"start_timestamp": start, "end_timestamp": end, "start_utc": _utc(start), "end_utc": _utc(end), "events": len(timestamps)}
    current, holdout = result["CURRENT"], result["HOLDOUT"]
    ranges_overlap = all(value is not None for value in (current["start_timestamp"], current["end_timestamp"], holdout["start_timestamp"], holdout["end_timestamp"])) and max(current["start_timestamp"], holdout["start_timestamp"]) <= min(current["end_timestamp"], holdout["end_timestamp"])
    result["ranges_overlap"] = ranges_overlap
    return result


def _utc(timestamp: int | None) -> str | None:
    return datetime.fromtimestamp(timestamp / 1000, tz=UTC).isoformat() if timestamp is not None else None


def _baseline(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {period: {direction: _metrics([record for record in records if record["period"] == period and record["direction"] == direction]) for direction in DIRECTIONS} for period in PERIODS}


def _factor_groups(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        for factor, value in _factor_values(record):
            row = grouped.setdefault((factor, value), {"factor": factor, "factor_value": value, "metrics": {}})
            row.setdefault("records", defaultdict(list))[(record["period"], record["direction"])].append(record)
    rows = []
    for row in grouped.values():
        for period in PERIODS:
            for direction in DIRECTIONS:
                stats = _metrics(row["records"].get((period, direction), []))
                stats["low_sample"] = stats["n"] < MIN_FACTOR_SAMPLE
                row["metrics"].setdefault(period, {})[direction] = stats
        del row["records"]
        rows.append(row)
    rows.sort(key=lambda row: (row["factor"], row["factor_value"]))

    def eligible(row: dict[str, Any], direction: str) -> bool:
        return all(row["metrics"][period][direction]["n"] >= MIN_FACTOR_SAMPLE for period in PERIODS)

    stable_positive = [{**row, "direction": direction} for row in rows for direction in DIRECTIONS if eligible(row, direction) and all(row["metrics"][period][direction]["mfe_minus_mae_pct"] > 0 for period in PERIODS)]
    stable_negative = [{**row, "direction": direction} for row in rows for direction in DIRECTIONS if eligible(row, direction) and all(row["metrics"][period][direction]["mfe_minus_mae_pct"] < 0 for period in PERIODS)]
    direction_flipping = [{**row, "direction": direction} for row in rows for direction in DIRECTIONS if eligible(row, direction) and row["metrics"]["CURRENT"][direction]["mfe_minus_mae_pct"] * row["metrics"]["HOLDOUT"][direction]["mfe_minus_mae_pct"] < 0]
    relative_stable = []
    for row in rows:
        if all(row["metrics"][period][direction]["n"] >= MIN_FACTOR_SAMPLE for period in PERIODS for direction in DIRECTIONS):
            current_delta = row["metrics"]["CURRENT"]["LONG"]["mfe_minus_mae_pct"] - row["metrics"]["CURRENT"]["SHORT"]["mfe_minus_mae_pct"]
            holdout_delta = row["metrics"]["HOLDOUT"]["LONG"]["mfe_minus_mae_pct"] - row["metrics"]["HOLDOUT"]["SHORT"]["mfe_minus_mae_pct"]
            if current_delta > 0 and holdout_delta > 0:
                relative_stable.append({**row, "relative_winner": "LONG"})
            elif current_delta < 0 and holdout_delta < 0:
                relative_stable.append({**row, "relative_winner": "SHORT"})
    return {"groups": rows, "direction_stable_positive": stable_positive, "direction_stable_negative": stable_negative, "direction_flipping": direction_flipping, "relative_direction_stable": relative_stable}


def _symbol_classification(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_symbol[record["symbol"]].append(record)
    rows, counts = [], defaultdict(int)
    for symbol, values in sorted(by_symbol.items()):
        metrics = {period: {direction: _metrics([record for record in values if record["period"] == period and record["direction"] == direction]) for direction in DIRECTIONS} for period in PERIODS}
        enough = all(metrics[period][direction]["n"] >= MIN_SYMBOL_SAMPLE for period in PERIODS for direction in DIRECTIONS)
        if not enough:
            status = "INSUFFICIENT_SAMPLE"
        else:
            winners = {period: "LONG" if metrics[period]["LONG"]["mfe_minus_mae_pct"] > metrics[period]["SHORT"]["mfe_minus_mae_pct"] else "SHORT" if metrics[period]["LONG"]["mfe_minus_mae_pct"] < metrics[period]["SHORT"]["mfe_minus_mae_pct"] else None for period in PERIODS}
            status = "LONG_STABLE" if winners["CURRENT"] == winners["HOLDOUT"] == "LONG" else "SHORT_STABLE" if winners["CURRENT"] == winners["HOLDOUT"] == "SHORT" else "FLIPPED_LONG_TO_SHORT" if winners["CURRENT"] == "LONG" and winners["HOLDOUT"] == "SHORT" else "FLIPPED_SHORT_TO_LONG" if winners["CURRENT"] == "SHORT" and winners["HOLDOUT"] == "LONG" else "NO_CLEAR_EDGE"
        counts[status] += 1
        rows.append({"symbol": symbol, "classification": status, "metrics": metrics})
    return {"rows": rows, "counts": dict(sorted(counts.items()))}


def _structure_alignment(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    states = ("BULLISH", "BEARISH", "RANGE", "TRANSITION_BULLISH", "TRANSITION_BEARISH")
    source = {row["factor_value"]: row for row in groups if row["factor"] == "structure_state"}
    rows = []
    for state in states:
        row = source.get(state)
        for period in PERIODS:
            for direction in DIRECTIONS:
                stats = row["metrics"][period][direction] if row else {**_metrics([]), "low_sample": True}
                alignment = "ALIGNED" if (direction, state) in (("LONG", "BULLISH"), ("SHORT", "BEARISH")) else "OPPOSED" if (direction, state) in (("LONG", "BEARISH"), ("SHORT", "BULLISH")) else "RANGE" if state == "RANGE" else "OTHER"
                rows.append({"period": period, "direction": direction, "structure_state": state, "alignment": alignment, **stats})
    return rows


def _monotonicity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = (
        ("confidence", "ready_confidence", [60, 70, 80], ["<60", "60-70", "70-80", "80+"]),
        ("decision_score", "ready_decision_score", [50, 60, 70, 80], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("breakout_score", "breakout_score", [50, 60, 70, 80], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("trend_quality", "trend_quality", [50, 60, 70, 80], ["<50", "50-60", "60-70", "70-80", "80+"]),
        ("volume_quality", "volume_quality", [40, 60, 80], ["<40", "40-60", "60-80", "80+"]),
        ("structure_quality", "structure_quality", [40, 60, 80], ["<40", "40-60", "60-80", "80+"]),
    )
    rows = []
    for period in PERIODS:
        for direction in DIRECTIONS:
            subset = [record for record in records if record["period"] == period and record["direction"] == direction]
            for name, field, edges, labels in definitions:
                buckets = []
                for label in labels:
                    bucket_records = [record for record in subset if (value := _safe_float(record.get(field))) is not None and _bucket(value, edges, labels) == label]
                    stats = _metrics(bucket_records)
                    buckets.append({"bucket": label, "n": stats["n"], "mfe_minus_mae_pct": stats["mfe_minus_mae_pct"], "low_sample": stats["n"] < MIN_FACTOR_SAMPLE})
                eligible = [row for row in buckets if not row["low_sample"]]
                values = [row["mfe_minus_mae_pct"] for row in eligible]
                monotonic = len(values) >= 2 and all(left <= right for left, right in zip(values, values[1:]))
                rows.append({"period": period, "direction": direction, "score": name, "monotonic_non_decreasing": monotonic, "eligible_bucket_count": len(eligible), "buckets": buckets})
    return rows


def analyze_regime_comparison(current_payload: dict[str, Any], holdout_payload: dict[str, Any]) -> dict[str, Any]:
    current, current_skipped = _load_records(copy.deepcopy(current_payload) if isinstance(current_payload, dict) else {}, "CURRENT")
    holdout, holdout_skipped = _load_records(copy.deepcopy(holdout_payload) if isinstance(holdout_payload, dict) else {}, "HOLDOUT")
    records = current + holdout
    ranges = _period_ranges(records)
    if ranges["ranges_overlap"]:
        raise ValueError("CURRENT and HOLDOUT READY timestamp ranges overlap")
    factors = _factor_groups(records)
    return {"summary": {"horizon": 5, "available_events": {"CURRENT": len(current), "HOLDOUT": len(holdout)}, "skipped_malformed_events": {"CURRENT": current_skipped, "HOLDOUT": holdout_skipped}, "period_ranges": ranges}, "baseline": _baseline(records), "factors": factors, "symbols": _symbol_classification(records), "structure_alignment": _structure_alignment(factors["groups"]), "monotonicity": _monotonicity(records)}


def save_report_json(path: str | Path, report: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return target


def save_report_csv(path: str | Path, report: dict[str, Any]) -> Path:
    rows = []
    for period in PERIODS:
        for direction in DIRECTIONS:
            rows.append({"section": "baseline", "period": period, "direction": direction, **report["baseline"][period][direction]})
    for group in report["factors"]["groups"]:
        for period in PERIODS:
            for direction in DIRECTIONS:
                rows.append({"section": "factor", "factor": group["factor"], "factor_value": group["factor_value"], "period": period, "direction": direction, **group["metrics"][period][direction]})
    for row in report["structure_alignment"]:
        rows.append({"section": "structure_alignment", **row})
    for row in report["monotonicity"]:
        for bucket in row["buckets"]:
            rows.append({"section": "monotonicity", "period": row["period"], "direction": row["direction"], "score": row["score"], "monotonic_non_decreasing": row["monotonic_non_decreasing"], **bucket})
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({field for row in rows for field in row}))
        writer.writeheader()
        writer.writerows(rows)
    return target


def _label(group: dict[str, Any]) -> str:
    direction = f"{group['direction']} " if group.get("direction") else ""
    return f"{direction}{group['factor']}={group['factor_value']}"


def build_console_summary(report: dict[str, Any]) -> str:
    ranges = report["summary"]["period_ranges"]
    lines = ["A. Baseline direction flip", f"CURRENT={ranges['CURRENT']['start_utc']}..{ranges['CURRENT']['end_utc']}; HOLDOUT={ranges['HOLDOUT']['start_utc']}..{ranges['HOLDOUT']['end_utc']}; ranges_overlap={ranges['ranges_overlap']}"]
    for period in PERIODS:
        long, short = report["baseline"][period]["LONG"], report["baseline"][period]["SHORT"]
        winner = "LONG" if long["mfe_minus_mae_pct"] > short["mfe_minus_mae_pct"] else "SHORT"
        lines.append(f"{period}: LONG N={long['n']} edge={long['mfe_minus_mae_pct']:.2f}; SHORT N={short['n']} edge={short['mfe_minus_mae_pct']:.2f}; winner={winner}")
    factors = report["factors"]
    lines.extend(["B. Stable positive groups", "; ".join(_label(row) for row in factors["direction_stable_positive"]) or "none", "C. Stable negative groups", "; ".join(_label(row) for row in factors["direction_stable_negative"]) or "none", "D. Direction-flipping groups", "; ".join(_label(row) for row in factors["direction_flipping"]) or "none", "E. Symbol classifications", str(report["symbols"]["counts"]), "F. Structure alignment"])
    for row in report["structure_alignment"]:
        if row["alignment"] in ("ALIGNED", "OPPOSED", "RANGE") and not row["low_sample"]:
            lines.append(f"{row['period']} {row['direction']} {row['structure_state']} {row['alignment']}: N={row['n']} edge={row['mfe_minus_mae_pct']:.2f}")
    lines.append("G. Monotonicity matrix")
    lines.extend(f"{row['period']} {row['direction']} {row['score']}: {'YES' if row['monotonic_non_decreasing'] else 'NO'} ({row['eligible_bucket_count']} buckets)" for row in report["monotonicity"])
    return "\n".join(lines)


def main() -> None:
    current = json.loads(Path(CURRENT_PATH).read_text(encoding="utf-8"))
    holdout = json.loads(Path(HOLDOUT_PATH).read_text(encoding="utf-8"))
    report = analyze_regime_comparison(current, holdout)
    save_report_json(OUTPUT_JSON, report)
    save_report_csv(OUTPUT_CSV, report)
    print(build_console_summary(report))


if __name__ == "__main__":
    main()