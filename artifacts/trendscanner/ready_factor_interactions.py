"""Descriptive interaction analysis for READY outcome factors.

This is a descriptive cross-tab study: it checks whether single-factor
signal strength (e.g. "high volume_quality looks good") is actually a
side effect of mixing with direction or other factors, rather than an
independent effect. It does not change any trading/decision logic and
does not draw conclusions about future performance.
"""

from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
from typing import Any, Callable

from ready_factor_analysis import (
    _confidence_bucket,
    _decision_bucket,
    _score_bucket,
    _structure_bucket,
    _volume_bucket,
    load_results,
)


SOURCE_PATH = "artifacts/trendscanner/ready_outcome_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_factor_interactions.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_factor_interactions.csv"
HORIZON = 5
MIN_SAMPLE_FOR_RANKING = 8

# Ascending bucket order per numeric score, used for monotonicity checks.
_BUCKET_ORDER = {
    "confidence": ["<60", "60-70", "70-80", "80+"],
    "decision_score": ["<50", "50-60", "60-70", "70-80", "80+"],
    "breakout_score": ["<50", "50-60", "60-70", "70-80", "80+"],
    "trend_quality": ["<50", "50-60", "60-70", "70-80", "80+"],
    "volume_quality": ["<40", "40-60", "60-80", "80+"],
    "structure_quality": ["<40", "40-60", "60-80", "80+"],
}

_BUCKET_FN = {
    "confidence": _confidence_bucket,
    "decision_score": _decision_bucket,
    "breakout_score": _score_bucket,
    "trend_quality": _score_bucket,
    "volume_quality": _volume_bucket,
    "structure_quality": _structure_bucket,
}

_SCORE_RECORD_FIELD = {
    "confidence": "ready_confidence",
    "decision_score": "ready_decision_score",
    "breakout_score": "breakout_score",
    "trend_quality": "trend_quality",
    "volume_quality": "volume_quality",
    "structure_quality": "structure_quality",
}

# Cross-tab definitions: (factor_type, function returning list of bucket labels or None if unavailable).
_CROSS_TABS: list[tuple[str, Callable[[dict[str, Any]], list[str] | None]]] = [
    ("structure_state", lambda rec: [rec["structure_state"]] if rec["structure_state"] is not None else None),
    ("warning", lambda rec: sorted(set(rec["ready_warnings"])) if rec["ready_warnings"] else ["NO_WARNINGS"]),
    ("confidence_bucket", lambda rec: [_confidence_bucket(rec["ready_confidence"])]),
    ("breakout_score_bucket", lambda rec: [_score_bucket(rec["breakout_score"])] if rec["breakout_score"] is not None else None),
    ("trend_quality_bucket", lambda rec: [_score_bucket(rec["trend_quality"])] if rec["trend_quality"] is not None else None),
    ("volume_quality_bucket", lambda rec: [_volume_bucket(rec["volume_quality"])] if rec["volume_quality"] is not None else None),
    ("structure_quality_bucket", lambda rec: [_structure_bucket(rec["structure_quality"])] if rec["structure_quality"] is not None else None),
    ("decision_score_bucket", lambda rec: [_decision_bucket(rec["ready_decision_score"])]),
]


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rate(values: list[float], predicate: Callable[[float], bool]) -> float | None:
    if not values:
        return None
    hits = sum(1 for value in values if predicate(value))
    return hits / len(values) * 100.0


def _clean_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_clean_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean_json_value(item) for key, item in value.items()}
    return str(value)


def _format_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


def _format_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _extract_records(payload: dict[str, Any], horizon: int) -> list[dict[str, Any]]:
    horizon_key = str(horizon)
    runs = payload.get("runs", []) if isinstance(payload.get("runs"), list) else []
    ok_runs = [run for run in runs if isinstance(run, dict) and run.get("status") == "OK"]

    records: list[dict[str, Any]] = []

    for run in ok_runs:
        events = run.get("events", [])
        if not isinstance(events, list):
            continue

        for event in events:
            if not isinstance(event, dict):
                continue

            horizons = event.get("horizons", {})
            if not isinstance(horizons, dict):
                continue

            h_data = horizons.get(horizon_key, {})
            if not isinstance(h_data, dict) or not bool(h_data.get("available")):
                continue

            mfe = _safe_float(h_data.get("mfe_pct"))
            mae = _safe_float(h_data.get("mae_pct"))
            close_ret = _safe_float(h_data.get("close_return_pct"))
            if mfe is None or mae is None or close_ret is None:
                continue

            confidence = _safe_float(event.get("ready_confidence"))
            decision_score = _safe_float(event.get("ready_decision_score"))
            if confidence is None or decision_score is None:
                continue

            structure_state_raw = event.get("structure_state")
            if structure_state_raw is None:
                structure_state = None
            else:
                normalized = str(structure_state_raw).strip().upper()
                structure_state = normalized if normalized else None

            warnings_raw = event.get("ready_warnings")
            warnings = [str(item).strip() for item in warnings_raw] if isinstance(warnings_raw, list) else []
            warnings = [item for item in warnings if item]

            records.append(
                {
                    "symbol": str(event.get("symbol", "")),
                    "timeframe": str(event.get("timeframe", "")),
                    "direction": str(event.get("direction", "")),
                    "ready_confidence": confidence,
                    "ready_decision_score": decision_score,
                    "ready_warnings": warnings,
                    "mfe_pct": mfe,
                    "mae_pct": mae,
                    "close_return_pct": close_ret,
                    "breakout_score": _safe_float(event.get("breakout_score")),
                    "trend_quality": _safe_float(event.get("trend_quality")),
                    "volume_quality": _safe_float(event.get("volume_quality")),
                    "structure_quality": _safe_float(event.get("structure_quality")),
                    "structure_state": structure_state,
                }
            )

    return records


def _to_row_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    mfe_values = [rec["mfe_pct"] for rec in records]
    mae_values = [rec["mae_pct"] for rec in records]
    close_values = [rec["close_return_pct"] for rec in records]
    diff_values = [rec["mfe_pct"] - rec["mae_pct"] for rec in records]

    event_count = len(records)
    return {
        "event_count": event_count,
        "low_sample": event_count < MIN_SAMPLE_FOR_RANKING,
        "average_mfe_pct": _mean(mfe_values),
        "average_mae_pct": _mean(mae_values),
        "average_mfe_minus_mae_pct": _mean(diff_values),
        "average_close_return_pct": _mean(close_values),
        "positive_close_rate": _rate(close_values, lambda value: value > 0.0),
        "mfe_ge_2_pct_rate": _rate(mfe_values, lambda value: value >= 2.0),
    }


def _build_cross_tabs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    for rec in records:
        direction = rec["direction"]
        for factor_type, bucket_fn in _CROSS_TABS:
            labels = bucket_fn(rec)
            if labels is None:
                continue
            for label in labels:
                key = (direction, factor_type, label)
                grouped.setdefault(key, []).append(rec)

    rows: list[dict[str, Any]] = []
    for (direction, factor_type, factor_value), group_records in grouped.items():
        row = {
            "direction": direction,
            "factor_type": factor_type,
            "factor_value": factor_value,
        }
        row.update(_to_row_metrics(group_records))
        rows.append(row)

    rows.sort(key=lambda row: (str(row["factor_type"]), str(row["factor_value"]), str(row["direction"])))
    return rows


def _build_monotonicity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for score_name, order in _BUCKET_ORDER.items():
        record_field = _SCORE_RECORD_FIELD[score_name]
        bucket_fn = _BUCKET_FN[score_name]

        grouped: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            value = rec.get(record_field)
            if value is None:
                continue
            grouped.setdefault(bucket_fn(value), []).append(rec)

        buckets: list[dict[str, Any]] = []
        for label in order:
            group_records = grouped.get(label, [])
            if not group_records:
                continue
            bucket_row = {"bucket": label}
            bucket_row.update(_to_row_metrics(group_records))
            buckets.append(bucket_row)

        eligible_values = [
            bucket["average_mfe_minus_mae_pct"]
            for bucket in buckets
            if not bucket["low_sample"] and bucket["average_mfe_minus_mae_pct"] is not None
        ]

        status = _monotonic_status(eligible_values)

        results.append(
            {
                "score": score_name,
                "buckets": buckets,
                "eligible_bucket_count": len(eligible_values),
                "monotonicity": status,
            }
        )

    return results


def _monotonic_status(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient_data"

    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    epsilon = 1e-9

    if all(diff >= -epsilon for diff in diffs) and any(diff > epsilon for diff in diffs):
        return "increasing"
    if all(diff <= epsilon for diff in diffs) and any(diff < -epsilon for diff in diffs):
        return "decreasing"
    if all(abs(diff) <= epsilon for diff in diffs):
        return "flat"
    return "not_monotonic"


def _cross_tab_sort_key_desc(row: dict[str, Any]) -> tuple[float, int]:
    return (_safe_float(row.get("average_mfe_minus_mae_pct")) or -10**9, int(row.get("event_count", 0)))


def analyze_ready_factor_interactions(payload: dict[str, Any], horizon: int = HORIZON) -> dict[str, Any]:
    payload_copy = copy.deepcopy(payload) if isinstance(payload, dict) else {}

    records = _extract_records(payload_copy, horizon)
    cross_tabs = _build_cross_tabs(records)
    monotonicity = _build_monotonicity(records)

    rankable = [row for row in cross_tabs if not bool(row.get("low_sample"))]
    strongest = sorted(rankable, key=_cross_tab_sort_key_desc, reverse=True)[:10]
    weakest = sorted(rankable, key=_cross_tab_sort_key_desc)[:10]

    report = {
        "config": {
            "source_path": str(payload_copy.get("_source_path", "<in-memory>")),
            "horizon": int(horizon),
            "min_sample_for_ranking": MIN_SAMPLE_FOR_RANKING,
        },
        "summary": {
            "available_events": len(records),
        },
        "cross_tabs": cross_tabs,
        "monotonicity": monotonicity,
        "strongest_interactions": strongest,
        "weakest_interactions": weakest,
        "limitations": [
            "descriptive interaction analysis on current sample only",
            "cross-tab groups with N below min_sample_for_ranking are excluded from strongest/weakest rankings",
            "not a claim that any factor determines outcomes; direction and other fields may still be confounded",
        ],
    }

    return _clean_json_value(report)


def save_report_json(path: str | Path, report: dict[str, Any]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _clean_json_value(report)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, ensure_ascii=False, indent=2, allow_nan=False)
    return output_path


def save_report_csv(path: str | Path, report: dict[str, Any]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "direction",
        "factor_type",
        "factor_value",
        "event_count",
        "low_sample",
        "average_mfe_pct",
        "average_mae_pct",
        "average_mfe_minus_mae_pct",
        "average_close_return_pct",
        "positive_close_rate",
        "mfe_ge_2_pct_rate",
    ]

    cross_tabs = report.get("cross_tabs", []) if isinstance(report.get("cross_tabs"), list) else []

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in cross_tabs:
            if not isinstance(row, dict):
                continue
            writer.writerow(_clean_json_value({key: row.get(key) for key in headers}))

    return output_path


def _cross_tab_rows(cross_tabs: list[dict[str, Any]], factor_type: str) -> list[dict[str, Any]]:
    selected = [row for row in cross_tabs if row.get("factor_type") == factor_type]
    selected.sort(key=lambda row: (str(row.get("factor_value", "")), str(row.get("direction", ""))))
    return selected


def _print_cross_tab(title: str, rows: list[dict[str, Any]]) -> None:
    print(title)
    if not rows:
        print("No rows")
        return
    for row in rows:
        print(
            f"direction={row.get('direction')} | {row.get('factor_value')} | N={row.get('event_count')} "
            f"| MFE={_format_pct(_safe_float(row.get('average_mfe_pct')))} "
            f"| MAE={_format_pct(_safe_float(row.get('average_mae_pct')))} "
            f"| MFE-MAE={_format_pct(_safe_float(row.get('average_mfe_minus_mae_pct')))} "
            f"| CLOSE={_format_pct(_safe_float(row.get('average_close_return_pct')))} "
            f"| POSITIVE={_format_pct(_safe_float(row.get('positive_close_rate')), 1)} "
            f"| MFE>=2%={_format_pct(_safe_float(row.get('mfe_ge_2_pct_rate')), 1)} "
            f"| LOW_SAMPLE={bool(row.get('low_sample'))}"
        )


def _print_monotonicity(monotonicity: list[dict[str, Any]]) -> None:
    print("Monotonicity Summary")
    for entry in monotonicity:
        print(f"score={entry.get('score')} | status={entry.get('monotonicity')}")
        for bucket in entry.get("buckets", []):
            print(
                f"    {bucket.get('bucket')} | N={bucket.get('event_count')} "
                f"| MFE-MAE={_format_pct(_safe_float(bucket.get('average_mfe_minus_mae_pct')))} "
                f"| CLOSE={_format_pct(_safe_float(bucket.get('average_close_return_pct')))} "
                f"| LOW_SAMPLE={bool(bucket.get('low_sample'))}"
            )


def _print_interactions(title: str, rows: list[dict[str, Any]]) -> None:
    print(title)
    if not rows:
        print("No rows with N >= min_sample_for_ranking")
        return
    for row in rows:
        print(
            f"{row.get('factor_type')}={row.get('factor_value')} | direction={row.get('direction')} "
            f"| N={row.get('event_count')} "
            f"| MFE-MAE={_format_pct(_safe_float(row.get('average_mfe_minus_mae_pct')))}"
        )


def main() -> None:
    payload = load_results(SOURCE_PATH)
    payload["_source_path"] = SOURCE_PATH

    report = analyze_ready_factor_interactions(payload, horizon=HORIZON)

    json_path = save_report_json(OUTPUT_JSON, report)
    csv_path = save_report_csv(OUTPUT_CSV, report)

    print("READY Factor Interactions (descriptive)")
    print("=" * 72)
    print(f"available_events={report.get('summary', {}).get('available_events', 0)}")

    cross_tabs = report.get("cross_tabs", []) if isinstance(report.get("cross_tabs"), list) else []

    _print_cross_tab("Direction x Structure State", _cross_tab_rows(cross_tabs, "structure_state"))
    _print_cross_tab("Direction x Volume Quality Bucket", _cross_tab_rows(cross_tabs, "volume_quality_bucket"))
    _print_cross_tab("Direction x Breakout Score Bucket", _cross_tab_rows(cross_tabs, "breakout_score_bucket"))
    _print_cross_tab("Direction x Decision Score Bucket", _cross_tab_rows(cross_tabs, "decision_score_bucket"))

    _print_monotonicity(report.get("monotonicity", []) if isinstance(report.get("monotonicity"), list) else [])

    _print_interactions(
        "Strongest Interactions (N >= min_sample_for_ranking)",
        report.get("strongest_interactions", []) if isinstance(report.get("strongest_interactions"), list) else [],
    )
    _print_interactions(
        "Weakest Interactions (N >= min_sample_for_ranking)",
        report.get("weakest_interactions", []) if isinstance(report.get("weakest_interactions"), list) else [],
    )

    print("JSON:", json_path)
    print("CSV:", csv_path)


if __name__ == "__main__":
    main()
