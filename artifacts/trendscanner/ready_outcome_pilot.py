from __future__ import annotations

import copy
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable

from historical_replay import replay_timeframe
from ready_outcome_analyzer import analyze_ready_outcomes
from scanner import get_data


SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "LINK/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "AVAX/USDT",
    "DOT/USDT",
    "ATOM/USDT",
    "NEAR/USDT",
    "FIL/USDT",
    "ARB/USDT",
    "OP/USDT",
    "INJ/USDT",
    "SUI/USDT",
    "TIA/USDT",
    "APT/USDT",
    "PENDLE/USDT",
    "ETC/USDT",
    "AAVE/USDT",
    "UNI/USDT",
]

TIMEFRAMES = ["4h"]

HORIZONS = (3, 5, 10)

WARMUP_BARS = 120
MAX_REPLAY_BARS = 300

OUTPUT_JSON = "artifacts/trendscanner/ready_outcome_results.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_outcome_events.csv"


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


def _serialize_clean(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, list):
        return [_serialize_clean(item) for item in value]

    if isinstance(value, tuple):
        return [_serialize_clean(item) for item in value]

    if isinstance(value, dict):
        return {str(key): _serialize_clean(item) for key, item in value.items()}

    return str(value)


def _normalize_horizons(horizons: tuple[int, ...]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for item in horizons:
        if isinstance(item, bool):
            continue
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str, str, int, int]:
    return (
        str(event.get("symbol", "")),
        str(event.get("timeframe", "")),
        str(event.get("direction", "")),
        _safe_int(event.get("ready_timestamp")) or 0,
        _safe_int(event.get("ready_replay_index")) or 0,
    )


def _prepare_run_result(
    *,
    symbol: str,
    timeframe: str,
    status: str,
    error: str | None,
    replay_meta: dict[str, Any],
    summary: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    events_copy = [copy.deepcopy(event) for event in events]
    events_copy.sort(key=_event_sort_key)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": status,
        "error": error,
        "replay_meta": copy.deepcopy(replay_meta),
        "summary": copy.deepcopy(summary),
        "events": events_copy,
    }


def _collect_horizon_values(runs: list[dict[str, Any]], horizon_key: str) -> dict[str, list[float]]:
    mfe_values: list[float] = []
    mae_values: list[float] = []
    close_values: list[float] = []

    for run in runs:
        if run.get("status") != "OK":
            continue
        events = run.get("events", [])
        if not isinstance(events, list):
            continue

        for event in events:
            if not isinstance(event, dict):
                continue
            horizon_data = event.get("horizons", {}).get(horizon_key, {})
            if not isinstance(horizon_data, dict) or not bool(horizon_data.get("available")):
                continue

            mfe = _safe_float(horizon_data.get("mfe_pct"))
            mae = _safe_float(horizon_data.get("mae_pct"))
            close = _safe_float(horizon_data.get("close_return_pct"))

            if mfe is not None:
                mfe_values.append(mfe)
            if mae is not None:
                mae_values.append(mae)
            if close is not None:
                close_values.append(close)

    return {
        "mfe": mfe_values,
        "mae": mae_values,
        "close": close_values,
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.mean(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


def _rate(values: list[float], predicate: Callable[[float], bool]) -> float | None:
    if not values:
        return None
    hits = sum(1 for value in values if predicate(value))
    return hits / len(values) * 100.0


def build_aggregate(runs: list[dict[str, Any]], horizons: tuple[int, ...]) -> dict[str, Any]:
    horizon_values = _normalize_horizons(horizons)

    total_ready_events = 0
    total_skipped_events = 0
    for run in runs:
        if run.get("status") != "OK":
            continue
        summary = run.get("summary", {})
        if not isinstance(summary, dict):
            continue
        total_ready_events += _safe_int(summary.get("ready_events")) or 0
        total_skipped_events += _safe_int(summary.get("skipped_events")) or 0

    by_horizon: dict[str, Any] = {}
    for horizon in horizon_values:
        key = str(horizon)
        values = _collect_horizon_values(runs, key)
        mfe_values = values["mfe"]
        mae_values = values["mae"]
        close_values = values["close"]

        by_horizon[key] = {
            "available_events": len(mfe_values),
            "average_mfe_pct": _mean(mfe_values),
            "median_mfe_pct": _median(mfe_values),
            "average_mae_pct": _mean(mae_values),
            "median_mae_pct": _median(mae_values),
            "average_close_return_pct": _mean(close_values),
            "positive_close_rate": _rate(close_values, lambda value: value > 0.0),
            "mfe_ge_1_pct_rate": _rate(mfe_values, lambda value: value >= 1.0),
            "mfe_ge_2_pct_rate": _rate(mfe_values, lambda value: value >= 2.0),
            "mfe_ge_3_pct_rate": _rate(mfe_values, lambda value: value >= 3.0),
        }

    return {
        "total_ready_events": total_ready_events,
        "total_skipped_events": total_skipped_events,
        "by_horizon": by_horizon,
    }


def _flatten_event_for_csv(event: dict[str, Any], horizons: tuple[int, ...]) -> dict[str, Any]:
    blocker_codes_raw = event.get("blocker_codes")
    blocker_codes = blocker_codes_raw if isinstance(blocker_codes_raw, list) else []

    row: dict[str, Any] = {
        "symbol": event.get("symbol"),
        "timeframe": event.get("timeframe"),
        "direction": event.get("direction"),
        "ready_replay_index": event.get("ready_replay_index"),
        "ready_timestamp": event.get("ready_timestamp"),
        "ready_candle_index": event.get("ready_candle_index"),
        "entry_reference_price": event.get("entry_reference_price"),
        "ready_confidence": event.get("ready_confidence"),
        "ready_decision_score": event.get("ready_decision_score"),
        "ready_warnings": " | ".join(str(item) for item in event.get("ready_warnings", []) if item),
        "breakout_score": event.get("breakout_score"),
        "breakout_confirmed": event.get("breakout_confirmed"),
        "trend_quality": event.get("trend_quality"),
        "volume_quality": event.get("volume_quality"),
        "structure_quality": event.get("structure_quality"),
        "structure_state": event.get("structure_state"),
        "blocker_codes": " | ".join(str(item) for item in blocker_codes if item),
    }

    event_horizons = event.get("horizons", {}) if isinstance(event.get("horizons"), dict) else {}

    for horizon in _normalize_horizons(horizons):
        key = str(horizon)
        prefix = f"h{horizon}_"
        horizon_data = event_horizons.get(key, {})
        if not isinstance(horizon_data, dict):
            horizon_data = {}

        row[f"{prefix}available"] = bool(horizon_data.get("available"))
        row[f"{prefix}mfe_pct"] = horizon_data.get("mfe_pct")
        row[f"{prefix}mae_pct"] = horizon_data.get("mae_pct")
        row[f"{prefix}close_return_pct"] = horizon_data.get("close_return_pct")
        row[f"{prefix}bars_to_mfe"] = horizon_data.get("bars_to_mfe")
        row[f"{prefix}bars_to_mae"] = horizon_data.get("bars_to_mae")

    return row


def _csv_headers(horizons: tuple[int, ...]) -> list[str]:
    headers = [
        "symbol",
        "timeframe",
        "direction",
        "ready_replay_index",
        "ready_timestamp",
        "ready_candle_index",
        "entry_reference_price",
        "ready_confidence",
        "ready_decision_score",
        "ready_warnings",
        "breakout_score",
        "breakout_confirmed",
        "trend_quality",
        "volume_quality",
        "structure_quality",
        "structure_state",
        "blocker_codes",
    ]

    for horizon in _normalize_horizons(horizons):
        prefix = f"h{horizon}_"
        headers.extend(
            [
                f"{prefix}available",
                f"{prefix}mfe_pct",
                f"{prefix}mae_pct",
                f"{prefix}close_return_pct",
                f"{prefix}bars_to_mfe",
                f"{prefix}bars_to_mae",
            ]
        )

    return headers


def _all_success_events(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for run in runs:
        if run.get("status") != "OK":
            continue
        events = run.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if isinstance(event, dict):
                collected.append(event)
    collected.sort(key=_event_sort_key)
    return collected


def _example_sort_key(event: dict[str, Any]) -> tuple[str, str, str, int, int]:
    return (
        str(event.get("symbol", "")),
        str(event.get("timeframe", "")),
        str(event.get("direction", "")),
        _safe_int(event.get("ready_timestamp")) or 0,
        _safe_int(event.get("ready_replay_index")) or 0,
    )


def collect_examples(runs: list[dict[str, Any]], horizon: int = 5, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    key = str(horizon)
    candidates: list[dict[str, Any]] = []

    for event in _all_success_events(runs):
        horizon_data = event.get("horizons", {}).get(key, {})
        if not isinstance(horizon_data, dict) or not bool(horizon_data.get("available")):
            continue

        mfe = _safe_float(horizon_data.get("mfe_pct"))
        mae = _safe_float(horizon_data.get("mae_pct"))
        close_ret = _safe_float(horizon_data.get("close_return_pct"))
        if mfe is None or mae is None or close_ret is None:
            continue

        candidates.append(
            {
                "symbol": event.get("symbol"),
                "timeframe": event.get("timeframe"),
                "direction": event.get("direction"),
                "ready_timestamp": event.get("ready_timestamp"),
                "entry_reference_price": event.get("entry_reference_price"),
                "mfe_pct": mfe,
                "mae_pct": mae,
                "close_return_pct": close_ret,
                "ready_confidence": event.get("ready_confidence"),
                "ready_decision_score": event.get("ready_decision_score"),
                "warnings": list(event.get("ready_warnings", [])),
                "ready_replay_index": event.get("ready_replay_index"),
            }
        )

    top_mfe = sorted(
        candidates,
        key=lambda item: (
            -(_safe_float(item.get("mfe_pct")) or 0.0),
            _example_sort_key(item),
        ),
    )[:limit]

    worst_close = sorted(
        candidates,
        key=lambda item: (
            (_safe_float(item.get("close_return_pct")) or 0.0),
            _example_sort_key(item),
        ),
    )[:limit]

    return {
        "top_mfe_h5": top_mfe,
        "worst_close_h5": worst_close,
    }


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def save_results_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output_path = Path(path)
    _ensure_parent_dir(output_path)

    cleaned = _serialize_clean(payload)

    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, ensure_ascii=False, indent=2, allow_nan=False)

    return output_path


def save_events_csv(path: str | Path, runs: list[dict[str, Any]], horizons: tuple[int, ...]) -> Path:
    output_path = Path(path)
    _ensure_parent_dir(output_path)

    headers = _csv_headers(horizons)
    rows: list[dict[str, Any]] = []

    for event in _all_success_events(runs):
        rows.append(_flatten_event_for_csv(event, horizons))

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(_serialize_clean(row))

    return output_path


def build_console_report(payload: dict[str, Any], horizons: tuple[int, ...]) -> str:
    lines: list[str] = []
    runs = payload.get("runs", []) if isinstance(payload.get("runs"), list) else []

    lines.append("READY Outcome Pilot")
    lines.append("=" * 72)

    for run in runs:
        symbol = str(run.get("symbol", ""))
        timeframe = str(run.get("timeframe", ""))
        status = str(run.get("status", ""))
        summary = run.get("summary", {}) if isinstance(run.get("summary"), dict) else {}
        diagnostics = summary.get("diagnostics", {}) if isinstance(summary.get("diagnostics"), dict) else {}

        lines.append(
            f"{symbol} {timeframe} | status={status} | ready={summary.get('ready_events', 0)} "
            f"| skipped={summary.get('skipped_events', 0)} "
            f"| diag(ts={diagnostics.get('timestamp_not_found', 0)}, "
            f"price={diagnostics.get('invalid_price', 0)}, "
            f"malformed={diagnostics.get('malformed_replay_entry', 0)})"
        )

        by_horizon = summary.get("by_horizon", {}) if isinstance(summary.get("by_horizon"), dict) else {}
        for horizon in _normalize_horizons(horizons):
            key = str(horizon)
            h = by_horizon.get(key, {}) if isinstance(by_horizon.get(key), dict) else {}
            lines.append(
                f"{symbol} {timeframe} | H={horizon} | events={h.get('available_events', 0)} "
                f"| MFE avg={_fmt_pct(_safe_float(h.get('average_mfe_pct')))} "
                f"| MAE avg={_fmt_pct(_safe_float(h.get('average_mae_pct')))} "
                f"| close avg={_fmt_pct(_safe_float(h.get('average_close_return_pct')))} "
                f"| positive close={_fmt_pct(_safe_float(h.get('positive_close_rate')), 1)} "
                f"| MFE>=2%={_fmt_pct(_safe_float(h.get('mfe_ge_2_pct_rate')), 1)}"
            )

    aggregate = payload.get("aggregate", {}) if isinstance(payload.get("aggregate"), dict) else {}
    by_horizon_agg = aggregate.get("by_horizon", {}) if isinstance(aggregate.get("by_horizon"), dict) else {}

    lines.append("-" * 72)
    lines.append("Aggregate (all successful runs, weighted by event count)")
    lines.append(
        f"total_ready_events={aggregate.get('total_ready_events', 0)} | "
        f"total_skipped_events={aggregate.get('total_skipped_events', 0)}"
    )

    for horizon in _normalize_horizons(horizons):
        key = str(horizon)
        h = by_horizon_agg.get(key, {}) if isinstance(by_horizon_agg.get(key), dict) else {}
        lines.append(
            f"H={horizon} | available={h.get('available_events', 0)} "
            f"| MFE avg={_fmt_pct(_safe_float(h.get('average_mfe_pct')))} "
            f"| MFE med={_fmt_pct(_safe_float(h.get('median_mfe_pct')))} "
            f"| MAE avg={_fmt_pct(_safe_float(h.get('average_mae_pct')))} "
            f"| MAE med={_fmt_pct(_safe_float(h.get('median_mae_pct')))} "
            f"| close avg={_fmt_pct(_safe_float(h.get('average_close_return_pct')))} "
            f"| positive={_fmt_pct(_safe_float(h.get('positive_close_rate')), 1)} "
            f"| MFE>=1%={_fmt_pct(_safe_float(h.get('mfe_ge_1_pct_rate')), 1)} "
            f"| MFE>=2%={_fmt_pct(_safe_float(h.get('mfe_ge_2_pct_rate')), 1)} "
            f"| MFE>=3%={_fmt_pct(_safe_float(h.get('mfe_ge_3_pct_rate')), 1)}"
        )

    examples = payload.get("examples", {}) if isinstance(payload.get("examples"), dict) else {}

    lines.append("-" * 72)
    lines.append("Top MFE events (H=5)")
    top_mfe = examples.get("top_mfe_h5", []) if isinstance(examples.get("top_mfe_h5"), list) else []
    if not top_mfe:
        lines.append("No available events for H=5")
    for item in top_mfe:
        warnings = " | ".join(str(entry) for entry in item.get("warnings", []) if entry)
        lines.append(
            f"{item.get('symbol')} {item.get('timeframe')} {item.get('direction')} "
            f"| ts={item.get('ready_timestamp')} | entry={_fmt_num(_safe_float(item.get('entry_reference_price')), 4)} "
            f"| MFE={_fmt_pct(_safe_float(item.get('mfe_pct')))} "
            f"| MAE={_fmt_pct(_safe_float(item.get('mae_pct')))} "
            f"| close={_fmt_pct(_safe_float(item.get('close_return_pct')))} "
            f"| conf={_fmt_num(_safe_float(item.get('ready_confidence')))} "
            f"| score={_fmt_num(_safe_float(item.get('ready_decision_score')))} "
            f"| warnings={warnings or '-'}"
        )

    lines.append("-" * 72)
    lines.append("Worst close return events (H=5)")
    worst_close = examples.get("worst_close_h5", []) if isinstance(examples.get("worst_close_h5"), list) else []
    if not worst_close:
        lines.append("No available events for H=5")
    for item in worst_close:
        warnings = " | ".join(str(entry) for entry in item.get("warnings", []) if entry)
        lines.append(
            f"{item.get('symbol')} {item.get('timeframe')} {item.get('direction')} "
            f"| ts={item.get('ready_timestamp')} | entry={_fmt_num(_safe_float(item.get('entry_reference_price')), 4)} "
            f"| MFE={_fmt_pct(_safe_float(item.get('mfe_pct')))} "
            f"| MAE={_fmt_pct(_safe_float(item.get('mae_pct')))} "
            f"| close={_fmt_pct(_safe_float(item.get('close_return_pct')))} "
            f"| conf={_fmt_num(_safe_float(item.get('ready_confidence')))} "
            f"| score={_fmt_num(_safe_float(item.get('ready_decision_score')))} "
            f"| warnings={warnings or '-'}"
        )

    return "\n".join(lines)


def run_ready_outcome_pilot(
    *,
    symbols: list[str] = SYMBOLS,
    timeframes: list[str] = TIMEFRAMES,
    horizons: tuple[int, ...] = HORIZONS,
    warmup_bars: int = WARMUP_BARS,
    max_replay_bars: int = MAX_REPLAY_BARS,
    output_json: str = OUTPUT_JSON,
    output_csv: str = OUTPUT_CSV,
    get_data_fn: Callable[[str, str], Any] = get_data,
    replay_timeframe_fn: Callable[..., dict[str, Any]] = replay_timeframe,
    analyze_ready_outcomes_fn: Callable[..., dict[str, Any]] = analyze_ready_outcomes,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []

    for symbol in symbols:
        for timeframe in timeframes:
            try:
                df = get_data_fn(symbol, timeframe)
                replay = replay_timeframe_fn(
                    df,
                    symbol=symbol,
                    timeframe=timeframe,
                    warmup_bars=warmup_bars,
                    max_replay_bars=max_replay_bars,
                )

                replay_meta = replay.get("meta", {}) if isinstance(replay, dict) else {}
                replay_results = replay.get("replay_results", []) if isinstance(replay, dict) else []

                analyzed = analyze_ready_outcomes_fn(
                    df,
                    replay_results,
                    horizons=horizons,
                )

                summary = analyzed.get("summary", {}) if isinstance(analyzed, dict) else {}
                events = analyzed.get("events", []) if isinstance(analyzed, dict) else []

                runs.append(
                    _prepare_run_result(
                        symbol=symbol,
                        timeframe=timeframe,
                        status="OK",
                        error=None,
                        replay_meta=replay_meta if isinstance(replay_meta, dict) else {},
                        summary=summary if isinstance(summary, dict) else {},
                        events=events if isinstance(events, list) else [],
                    )
                )
            except Exception as exc:
                runs.append(
                    _prepare_run_result(
                        symbol=symbol,
                        timeframe=timeframe,
                        status="FAILED",
                        error=f"{type(exc).__name__}: {exc}",
                        replay_meta={},
                        summary={},
                        events=[],
                    )
                )

    aggregate = build_aggregate(runs, horizons)
    examples = collect_examples(runs, horizon=5, limit=5)

    payload = {
        "config": {
            "symbols": list(symbols),
            "timeframes": list(timeframes),
            "horizons": _normalize_horizons(horizons),
            "warmup_bars": int(warmup_bars),
            "max_replay_bars": int(max_replay_bars),
        },
        "runs": runs,
        "aggregate": aggregate,
        "examples": examples,
    }

    json_path = save_results_json(output_json, payload)
    csv_path = save_events_csv(output_csv, runs, horizons)

    payload["output_json"] = str(json_path)
    payload["output_csv"] = str(csv_path)

    return payload


def main() -> None:
    payload = run_ready_outcome_pilot()
    report = build_console_report(payload, HORIZONS)
    print(report)
    print("-" * 72)
    print(f"JSON: {payload.get('output_json')}")
    print(f"CSV:  {payload.get('output_csv')}")


if __name__ == "__main__":
    main()
