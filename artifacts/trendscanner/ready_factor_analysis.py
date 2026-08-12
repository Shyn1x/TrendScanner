from __future__ import annotations

import copy
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any


SOURCE_PATH = "artifacts/trendscanner/ready_outcome_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_factor_report.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_factor_report.csv"
MIN_SAMPLE_FOR_RANKING = 5
_MISSING = object()


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
    return statistics.mean(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


def _rate(values: list[float], predicate) -> float | None:
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


def _bucket_by_edges(value: float, edges: list[float], labels: list[str]) -> str:
    for idx, edge in enumerate(edges):
        if value < edge:
            return labels[idx]
    return labels[-1]


def _confidence_bucket(value: float) -> str:
    return _bucket_by_edges(value, [60.0, 70.0, 80.0], ["<60", "60-70", "70-80", "80+"])


def _decision_bucket(value: float) -> str:
    return _bucket_by_edges(value, [50.0, 60.0, 70.0, 80.0], ["<50", "50-60", "60-70", "70-80", "80+"])


def _score_bucket(value: float) -> str:
    return _bucket_by_edges(value, [50.0, 60.0, 70.0, 80.0], ["<50", "50-60", "60-70", "70-80", "80+"])


def _volume_bucket(value: float) -> str:
    return _bucket_by_edges(value, [40.0, 60.0, 80.0], ["<40", "40-60", "60-80", "80+"])


def _structure_bucket(value: float) -> str:
    return _bucket_by_edges(value, [40.0, 60.0, 80.0], ["<40", "40-60", "60-80", "80+"])


def _factor_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("factor_type", "")), str(row.get("factor_value", ""))


def _to_group_row(
    *,
    factor_type: str,
    factor_value: str,
    records: list[dict[str, Any]],
    min_sample_for_ranking: int,
) -> dict[str, Any]:
    mfe_values = [rec["mfe_pct"] for rec in records]
    mae_values = [rec["mae_pct"] for rec in records]
    close_values = [rec["close_return_pct"] for rec in records]
    diff_values = [rec["mfe_pct"] - rec["mae_pct"] for rec in records]

    event_count = len(records)

    return {
        "factor_type": factor_type,
        "factor_value": factor_value,
        "event_count": event_count,
        "low_sample": event_count < min_sample_for_ranking,
        "average_mfe_pct": _mean(mfe_values),
        "median_mfe_pct": _median(mfe_values),
        "average_mae_pct": _mean(mae_values),
        "median_mae_pct": _median(mae_values),
        "average_close_return_pct": _mean(close_values),
        "median_close_return_pct": _median(close_values),
        "positive_close_rate": _rate(close_values, lambda value: value > 0.0),
        "mfe_ge_1_pct_rate": _rate(mfe_values, lambda value: value >= 1.0),
        "mfe_ge_2_pct_rate": _rate(mfe_values, lambda value: value >= 2.0),
        "mfe_ge_3_pct_rate": _rate(mfe_values, lambda value: value >= 3.0),
        "average_mfe_minus_mae_pct": _mean(diff_values),
    }


def _format_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


def _format_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def load_results(path: str | Path) -> dict:
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload if isinstance(payload, dict) else {}


def analyze_ready_factors(payload: dict, horizon: int = 5) -> dict:
    payload_copy = copy.deepcopy(payload) if isinstance(payload, dict) else {}

    min_sample_for_ranking = MIN_SAMPLE_FOR_RANKING
    target_horizon_key = str(horizon)

    runs = payload_copy.get("runs", []) if isinstance(payload_copy.get("runs"), list) else []
    total_runs = len(runs)
    ok_runs = [run for run in runs if isinstance(run, dict) and run.get("status") == "OK"]

    missing_field_counts = {
        "breakout_score": 0,
        "trend_quality": 0,
        "volume_quality": 0,
        "structure_quality": 0,
        "structure_state": 0,
        "blocker_codes": 0,
    }

    malformed_events_skipped = 0

    available_records: list[dict[str, Any]] = []
    for run in ok_runs:
        events = run.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                malformed_events_skipped += 1
                continue

            horizons = event.get("horizons", {})
            if not isinstance(horizons, dict):
                malformed_events_skipped += 1
                continue

            h5 = horizons.get(target_horizon_key, {})
            if not isinstance(h5, dict) or not bool(h5.get("available")):
                continue

            mfe = _safe_float(h5.get("mfe_pct"))
            mae = _safe_float(h5.get("mae_pct"))
            close_ret = _safe_float(h5.get("close_return_pct"))
            if mfe is None or mae is None or close_ret is None:
                malformed_events_skipped += 1
                continue

            confidence = _safe_float(event.get("ready_confidence"))
            decision_score = _safe_float(event.get("ready_decision_score"))
            if confidence is None or decision_score is None:
                malformed_events_skipped += 1
                continue

            breakout_score = _safe_float(event.get("breakout_score"))
            if breakout_score is None:
                missing_field_counts["breakout_score"] += 1

            trend_quality = _safe_float(event.get("trend_quality"))
            if trend_quality is None:
                missing_field_counts["trend_quality"] += 1

            volume_quality = _safe_float(event.get("volume_quality"))
            if volume_quality is None:
                missing_field_counts["volume_quality"] += 1

            structure_quality = _safe_float(event.get("structure_quality"))
            if structure_quality is None:
                missing_field_counts["structure_quality"] += 1

            structure_state_raw = event.get("structure_state", _MISSING)
            structure_state: str | None
            if structure_state_raw is _MISSING or structure_state_raw is None:
                structure_state = None
                missing_field_counts["structure_state"] += 1
            else:
                normalized_state = str(structure_state_raw).strip().upper()
                if normalized_state in {"", "UNKNOWN", "UNDEFINED", "N/A", "NONE", "NULL", "?"}:
                    structure_state = "UNKNOWN"
                else:
                    structure_state = normalized_state

            blocker_codes_raw = event.get("blocker_codes")
            blocker_codes: list[str] | None
            if isinstance(blocker_codes_raw, list):
                blocker_codes = [str(item).strip() for item in blocker_codes_raw if str(item).strip()]
            else:
                blocker_codes = None
                missing_field_counts["blocker_codes"] += 1

            warnings_raw = event.get("ready_warnings")
            warnings = [str(item).strip() for item in warnings_raw] if isinstance(warnings_raw, list) else []
            warnings = [item for item in warnings if item]

            available_records.append(
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
                    "breakout_score": breakout_score,
                    "trend_quality": trend_quality,
                    "volume_quality": volume_quality,
                    "structure_quality": structure_quality,
                    "structure_state": structure_state,
                    "blocker_codes": blocker_codes,
                }
            )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def add_group(ftype: str, fvalue: str, record: dict[str, Any]) -> None:
        key = (ftype, fvalue)
        grouped.setdefault(key, []).append(record)

    for rec in available_records:
        add_group("direction", rec["direction"], rec)

        warnings = rec["ready_warnings"]
        if warnings:
            for warning in sorted(set(warnings)):
                add_group("warning", warning, rec)
        else:
            add_group("warning", "NO_WARNINGS", rec)

        add_group("confidence_bucket", _confidence_bucket(rec["ready_confidence"]), rec)
        add_group("decision_score_bucket", _decision_bucket(rec["ready_decision_score"]), rec)

        breakout_score = rec["breakout_score"]
        if breakout_score is not None:
            add_group("breakout_score_bucket", _score_bucket(breakout_score), rec)

        trend_quality = rec["trend_quality"]
        if trend_quality is not None:
            add_group("trend_quality_bucket", _score_bucket(trend_quality), rec)

        volume_quality = rec["volume_quality"]
        if volume_quality is not None:
            add_group("volume_quality_bucket", _volume_bucket(volume_quality), rec)

        structure_quality = rec["structure_quality"]
        if structure_quality is not None:
            add_group("structure_quality_bucket", _structure_bucket(structure_quality), rec)

        structure_state = rec["structure_state"]
        if structure_state is not None:
            add_group("structure_state", structure_state, rec)

        blockers = rec["blocker_codes"]
        if blockers is None:
            continue
        if blockers:
            for code in sorted(set(blockers)):
                add_group("blocker_code", code, rec)
        else:
            add_group("blocker_code", "NO_BLOCKERS", rec)

    groups: list[dict[str, Any]] = []
    for (factor_type, factor_value), records in grouped.items():
        if not records:
            continue
        groups.append(
            _to_group_row(
                factor_type=factor_type,
                factor_value=factor_value,
                records=records,
                min_sample_for_ranking=min_sample_for_ranking,
            )
        )

    groups.sort(key=_factor_sort_key)

    unavailable_factor_types: set[str] = set()
    if missing_field_counts["breakout_score"] == len(available_records):
        unavailable_factor_types.add("breakout_score_bucket")
    if missing_field_counts["trend_quality"] == len(available_records):
        unavailable_factor_types.add("trend_quality_bucket")
    if missing_field_counts["volume_quality"] == len(available_records):
        unavailable_factor_types.add("volume_quality_bucket")
    if missing_field_counts["structure_quality"] == len(available_records):
        unavailable_factor_types.add("structure_quality_bucket")
    if missing_field_counts["structure_state"] == len(available_records):
        unavailable_factor_types.add("structure_state")
    if missing_field_counts["blocker_codes"] == len(available_records):
        unavailable_factor_types.add("blocker_code")

    rankable = [
        row
        for row in groups
        if not bool(row.get("low_sample")) and row.get("factor_type") not in unavailable_factor_types
    ]

    def rank_key_desc(row: dict[str, Any]) -> tuple[float, int, str, str]:
        return (
            _safe_float(row.get("average_mfe_minus_mae_pct")) or -10**9,
            int(row.get("event_count", 0)),
            str(row.get("factor_type", "")),
            str(row.get("factor_value", "")),
        )

    def rank_key_asc(row: dict[str, Any]) -> tuple[float, int, str, str]:
        return (
            _safe_float(row.get("average_mfe_minus_mae_pct")) if _safe_float(row.get("average_mfe_minus_mae_pct")) is not None else 10**9,
            -int(row.get("event_count", 0)),
            str(row.get("factor_type", "")),
            str(row.get("factor_value", "")),
        )

    strongest = sorted(rankable, key=lambda row: (-rank_key_desc(row)[0], -rank_key_desc(row)[1], rank_key_desc(row)[2], rank_key_desc(row)[3]))[:10]
    weakest = sorted(rankable, key=rank_key_asc)[:10]

    limitations: list[str] = []
    limitations.append("descriptive factor comparison on current sample")
    if malformed_events_skipped > 0:
        limitations.append(f"Skipped malformed events: {malformed_events_skipped}")
    if len(available_records) == 0:
        limitations.append("No available events for requested horizon")

    optional_map = {
        "breakout_score_bucket": "breakout_score",
        "trend_quality_bucket": "trend_quality",
        "volume_quality_bucket": "volume_quality",
        "structure_quality_bucket": "structure_quality",
        "structure_state": "structure_state",
        "blocker_code": "blocker_codes",
    }
    for factor_type, source_field in optional_map.items():
        if factor_type in unavailable_factor_types:
            limitations.append(f"Factor unavailable in current payload: {source_field}")

    report = {
        "config": {
            "source_path": str(payload_copy.get("_source_path", "<in-memory>")),
            "horizon": int(horizon),
            "min_sample_for_ranking": min_sample_for_ranking,
        },
        "summary": {
            "total_runs": total_runs,
            "successful_runs": len(ok_runs),
            "available_events": len(available_records),
            "missing_field_counts": missing_field_counts,
        },
        "groups": groups,
        "strongest_factors": strongest,
        "weakest_factors": weakest,
        "limitations": limitations,
    }

    return _clean_json_value(report)


def save_report_json(path: str | Path, report: dict) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _clean_json_value(report)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, ensure_ascii=False, indent=2, allow_nan=False)
    return output_path


def save_report_csv(path: str | Path, report: dict) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "factor_type",
        "factor_value",
        "event_count",
        "low_sample",
        "average_mfe_pct",
        "median_mfe_pct",
        "average_mae_pct",
        "median_mae_pct",
        "average_close_return_pct",
        "median_close_return_pct",
        "positive_close_rate",
        "mfe_ge_1_pct_rate",
        "mfe_ge_2_pct_rate",
        "mfe_ge_3_pct_rate",
        "average_mfe_minus_mae_pct",
    ]

    groups = report.get("groups", []) if isinstance(report.get("groups"), list) else []

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for group in groups:
            if not isinstance(group, dict):
                continue
            row = {key: group.get(key) for key in headers}
            writer.writerow(_clean_json_value(row))

    return output_path


def _table_rows(groups: list[dict[str, Any]], factor_type: str) -> list[dict[str, Any]]:
    selected = [row for row in groups if row.get("factor_type") == factor_type]
    selected.sort(key=lambda row: str(row.get("factor_value", "")))
    return selected


def _print_table(title: str, rows: list[dict[str, Any]]) -> None:
    print(title)
    if not rows:
        print("No rows")
        return

    for row in rows:
        print(
            f"{row.get('factor_type')} | {row.get('factor_value')} | N={row.get('event_count')} "
            f"| MFE={_format_pct(_safe_float(row.get('average_mfe_pct')))} "
            f"| MAE={_format_pct(_safe_float(row.get('average_mae_pct')))} "
            f"| MFE-MAE={_format_pct(_safe_float(row.get('average_mfe_minus_mae_pct')))} "
            f"| CLOSE={_format_pct(_safe_float(row.get('average_close_return_pct')))} "
            f"| POSITIVE={_format_pct(_safe_float(row.get('positive_close_rate')), 1)} "
            f"| MFE>=2%={_format_pct(_safe_float(row.get('mfe_ge_2_pct_rate')), 1)} "
            f"| LOW_SAMPLE={bool(row.get('low_sample'))}"
        )


def main() -> None:
    payload = load_results(SOURCE_PATH)
    payload["_source_path"] = SOURCE_PATH

    report = analyze_ready_factors(payload, horizon=5)

    json_path = save_report_json(OUTPUT_JSON, report)
    csv_path = save_report_csv(OUTPUT_CSV, report)

    summary = report.get("summary", {})
    missing = summary.get("missing_field_counts", {}) if isinstance(summary.get("missing_field_counts"), dict) else {}

    print("READY Factor Analysis")
    print("=" * 72)
    print(f"successful_runs={summary.get('successful_runs', 0)}")
    print(f"available_events={summary.get('available_events', 0)}")
    print(f"missing_field_counts={missing}")

    groups = report.get("groups", []) if isinstance(report.get("groups"), list) else []

    _print_table("Direction", _table_rows(groups, "direction"))
    _print_table("Warnings", _table_rows(groups, "warning"))
    _print_table("Confidence Buckets", _table_rows(groups, "confidence_bucket"))
    _print_table("Decision Score Buckets", _table_rows(groups, "decision_score_bucket"))

    if _table_rows(groups, "breakout_score_bucket"):
        _print_table("Breakout Score Buckets", _table_rows(groups, "breakout_score_bucket"))
    if _table_rows(groups, "trend_quality_bucket"):
        _print_table("Trend Quality Buckets", _table_rows(groups, "trend_quality_bucket"))
    if _table_rows(groups, "volume_quality_bucket"):
        _print_table("Volume Quality Buckets", _table_rows(groups, "volume_quality_bucket"))
    if _table_rows(groups, "structure_quality_bucket"):
        _print_table("Structure Quality Buckets", _table_rows(groups, "structure_quality_bucket"))
    if _table_rows(groups, "structure_state"):
        _print_table("Structure State", _table_rows(groups, "structure_state"))

    print("Strongest Factors")
    strongest = report.get("strongest_factors", []) if isinstance(report.get("strongest_factors"), list) else []
    if strongest:
        for row in strongest:
            print(
                f"{row.get('factor_type')} | {row.get('factor_value')} | N={row.get('event_count')} "
                f"| score={_format_num(_safe_float(row.get('average_mfe_minus_mae_pct')))}"
            )
    else:
        print("No rankable factors")

    print("Weakest Factors")
    weakest = report.get("weakest_factors", []) if isinstance(report.get("weakest_factors"), list) else []
    if weakest:
        for row in weakest:
            print(
                f"{row.get('factor_type')} | {row.get('factor_value')} | N={row.get('event_count')} "
                f"| score={_format_num(_safe_float(row.get('average_mfe_minus_mae_pct')))}"
            )
    else:
        print("No rankable factors")

    print("JSON:", json_path)
    print("CSV:", csv_path)


if __name__ == "__main__":
    main()
