"""Non-overlapping temporal holdout runner for READY outcome research."""

from __future__ import annotations

import copy
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from historical_replay import replay_timeframe
from ready_outcome_analyzer import analyze_ready_outcomes
from ready_outcome_pilot import (
    HORIZONS,
    MAX_REPLAY_BARS,
    RESEARCH_DATA_LIMIT,
    SYMBOLS,
    TIMEFRAMES,
    WARMUP_BARS,
    _prepare_run_result,
    build_aggregate,
    collect_examples,
    save_events_csv,
    save_results_json,
)
from research_data import get_research_data_before


CURRENT_RESULTS = "artifacts/trendscanner/ready_outcome_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_outcome_holdout_results.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_outcome_holdout_events.csv"
HOLDOUT_LIMIT = 1000
TIMEFRAME_MS = {"4h": 4 * 60 * 60 * 1000}


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = [record["mfe_pct"] for record in records]
    mae = [record["mae_pct"] for record in records]
    close = [record["close_return_pct"] for record in records]
    avg_mfe, avg_mae = _mean(mfe), _mean(mae)
    return {
        "n": len(records),
        "avg_mfe_pct": avg_mfe,
        "avg_mae_pct": avg_mae,
        "mfe_minus_mae_pct": avg_mfe - avg_mae if avg_mfe is not None and avg_mae is not None else None,
        "avg_close_return_pct": _mean(close),
        "positive_close_rate": sum(value > 0.0 for value in close) / len(close) * 100.0 if close else None,
        "mfe_ge_2_pct_rate": sum(value >= 2.0 for value in mfe) / len(mfe) * 100.0 if mfe else None,
    }


def derive_current_first_timestamp(
    current_payload: dict[str, Any],
    symbol: str,
    timeframe: str,
) -> int:
    timeframe_ms = TIMEFRAME_MS.get(timeframe)
    if timeframe_ms is None:
        raise ValueError(f"unsupported holdout timeframe: {timeframe}")

    candidates: set[int] = set()
    for run in current_payload.get("runs", []) if isinstance(current_payload.get("runs"), list) else []:
        if not isinstance(run, dict) or run.get("status") != "OK":
            continue
        for event in run.get("events", []) if isinstance(run.get("events"), list) else []:
            if not isinstance(event, dict) or event.get("symbol") != symbol or event.get("timeframe") != timeframe:
                continue
            timestamp = _safe_int(event.get("ready_timestamp"))
            candle_index = _safe_int(event.get("ready_candle_index"))
            if timestamp is None or candle_index is None or candle_index < 0:
                raise ValueError(f"ambiguous boundary for {symbol} {timeframe}: invalid READY event index/timestamp")
            candidates.add(timestamp - candle_index * timeframe_ms)

    if not candidates:
        raise ValueError(f"ambiguous boundary for {symbol} {timeframe}: no READY events")
    if len(candidates) != 1:
        raise ValueError(f"ambiguous boundary for {symbol} {timeframe}: {len(candidates)} candidate first timestamps")
    return candidates.pop()


def prepare_holdout_window(
    frame: pd.DataFrame,
    current_first_timestamp: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if "time" not in frame.columns:
        raise ValueError("holdout data has no time column")
    prepared = frame.copy(deep=True)
    prepared["time"] = pd.to_numeric(prepared["time"], errors="coerce")
    prepared = prepared.dropna(subset=["time"])
    prepared["time"] = prepared["time"].astype("int64")
    prepared = prepared.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    timestamps = prepared["time"].tolist()
    overlap_count = sum(timestamp >= current_first_timestamp for timestamp in timestamps)
    diagnostic = {
        "derived_current_first_timestamp": current_first_timestamp,
        "holdout_first_timestamp": timestamps[0] if timestamps else None,
        "holdout_last_timestamp": timestamps[-1] if timestamps else None,
        "holdout_rows": len(prepared),
        "overlap_count": overlap_count,
    }
    return prepared, diagnostic


def holdout_window_is_eligible(diagnostic: dict[str, Any], total_limit: int = HOLDOUT_LIMIT) -> bool:
    return (
        diagnostic.get("holdout_rows") == total_limit
        and diagnostic.get("holdout_last_timestamp") is not None
        and diagnostic["holdout_last_timestamp"] < diagnostic.get("derived_current_first_timestamp")
        and diagnostic.get("overlap_count") == 0
    )


def _h5_records(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run in runs:
        if run.get("status") != "OK":
            continue
        for event in run.get("events", []) if isinstance(run.get("events"), list) else []:
            if not isinstance(event, dict):
                continue
            outcome = event.get("horizons", {}).get("5", {}) if isinstance(event.get("horizons"), dict) else {}
            if not isinstance(outcome, dict) or not outcome.get("available"):
                continue
            mfe, mae, close = (_safe_float(outcome.get(key)) for key in ("mfe_pct", "mae_pct", "close_return_pct"))
            direction = str(event.get("direction", "")).upper()
            if mfe is None or mae is None or close is None or direction not in ("LONG", "SHORT"):
                continue
            records.append({"symbol": str(event.get("symbol", "")), "direction": direction, "mfe_pct": mfe, "mae_pct": mae, "close_return_pct": close})
    return records


def classify_hypothesis(comparison: dict[str, Any]) -> str:
    aggregate = comparison["aggregate"]
    symbols = comparison["per_symbol"]
    long_score = aggregate["LONG"]["mfe_minus_mae_pct"]
    short_score = aggregate["SHORT"]["mfe_minus_mae_pct"]
    median_long = symbols["median_long_mfe_minus_mae_pct"]
    if long_score is None or short_score is None or median_long is None:
        return "MIXED"
    if long_score <= short_score or median_long <= 0.0:
        return "NOT_SUPPORTED"
    comparable = symbols["comparable_symbols"]
    long_better_share = symbols["long_greater_than_short_symbols"] / comparable if comparable else 0.0
    if long_score > 0.0 and median_long > 0.0 and long_better_share >= 0.60:
        return "SUPPORTED"
    return "MIXED"


def build_holdout_comparison(runs: list[dict[str, Any]]) -> dict[str, Any]:
    records = _h5_records(runs)
    aggregate = {"ALL": _metrics(records)}
    aggregate.update({direction: _metrics([record for record in records if record["direction"] == direction]) for direction in ("LONG", "SHORT")})
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_symbol.setdefault(record["symbol"], []).append(record)
    long_values, short_values, long_better, comparable = [], [], 0, 0
    for symbol_records in by_symbol.values():
        long = _metrics([record for record in symbol_records if record["direction"] == "LONG"])
        short = _metrics([record for record in symbol_records if record["direction"] == "SHORT"])
        if long["mfe_minus_mae_pct"] is not None:
            long_values.append(long["mfe_minus_mae_pct"])
        if short["mfe_minus_mae_pct"] is not None:
            short_values.append(short["mfe_minus_mae_pct"])
        if long["n"] >= 5 and short["n"] >= 5:
            comparable += 1
            long_better += long["mfe_minus_mae_pct"] > short["mfe_minus_mae_pct"]
    comparison = {"aggregate": aggregate, "per_symbol": {"symbols": len(by_symbol), "long_mfe_minus_mae_positive_symbols": sum(value > 0.0 for value in long_values), "short_mfe_minus_mae_positive_symbols": sum(value > 0.0 for value in short_values), "long_greater_than_short_symbols": long_better, "comparable_symbols": comparable, "median_long_mfe_minus_mae_pct": statistics.median(long_values) if long_values else None, "median_short_mfe_minus_mae_pct": statistics.median(short_values) if short_values else None}}
    comparison["hypothesis_result"] = classify_hypothesis(comparison)
    return comparison


def run_ready_outcome_holdout(
    *,
    current_payload: dict[str, Any],
    symbols: list[str] = SYMBOLS,
    timeframes: list[str] = TIMEFRAMES,
    horizons: tuple[int, ...] = HORIZONS,
    warmup_bars: int = WARMUP_BARS,
    max_replay_bars: int = MAX_REPLAY_BARS,
    output_json: str = OUTPUT_JSON,
    output_csv: str = OUTPUT_CSV,
    get_data_before_fn: Callable[..., pd.DataFrame] = get_research_data_before,
    replay_timeframe_fn: Callable[..., dict[str, Any]] = replay_timeframe,
    analyze_ready_outcomes_fn: Callable[..., dict[str, Any]] = analyze_ready_outcomes,
    progress: bool = True,
) -> dict[str, Any]:
    source = copy.deepcopy(current_payload)
    runs: list[dict[str, Any]] = []
    for symbol in symbols:
        for timeframe in timeframes:
            window: dict[str, Any] = {}
            try:
                boundary = derive_current_first_timestamp(source, symbol, timeframe)
                frame = get_data_before_fn(symbol, timeframe, boundary, total_limit=HOLDOUT_LIMIT)
                frame, window = prepare_holdout_window(frame, boundary)
                window["eligible"] = holdout_window_is_eligible(window)
                if progress:
                    print(f"{symbol} | derived_current_first_timestamp={window['derived_current_first_timestamp']} | holdout_first_timestamp={window['holdout_first_timestamp']} | holdout_last_timestamp={window['holdout_last_timestamp']} | holdout_rows={window['holdout_rows']} | overlap_count={window['overlap_count']}", flush=True)
                if not window["eligible"]:
                    raise ValueError("holdout window is incomplete or overlaps current research window")
                replay = replay_timeframe_fn(frame, symbol=symbol, timeframe=timeframe, warmup_bars=warmup_bars, max_replay_bars=max_replay_bars)
                analyzed = analyze_ready_outcomes_fn(frame, replay.get("replay_results", []), horizons=horizons)
                run = _prepare_run_result(symbol=symbol, timeframe=timeframe, status="OK", error=None, replay_meta=replay.get("meta", {}), summary=analyzed.get("summary", {}), events=analyzed.get("events", []))
            except Exception as exc:
                if progress and not window:
                    print(f"{symbol} | boundary FAILED: {type(exc).__name__}: {exc}", flush=True)
                run = _prepare_run_result(symbol=symbol, timeframe=timeframe, status="FAILED", error=f"{type(exc).__name__}: {exc}", replay_meta={}, summary={}, events=[])
            run["holdout_window"] = window
            runs.append(run)
    payload = {"config": {"symbols": list(symbols), "timeframes": list(timeframes), "horizons": list(horizons), "warmup_bars": warmup_bars, "max_replay_bars": max_replay_bars, "current_results": CURRENT_RESULTS, "holdout_limit": HOLDOUT_LIMIT}, "runs": runs, "aggregate": build_aggregate(runs, horizons), "examples": collect_examples(runs, horizon=5, limit=5), "holdout_comparison": build_holdout_comparison(runs)}
    payload["output_json"] = str(save_results_json(output_json, payload))
    payload["output_csv"] = str(save_events_csv(output_csv, runs, horizons))
    return payload


def main() -> None:
    with Path(CURRENT_RESULTS).open("r", encoding="utf-8") as handle:
        current_payload = json.load(handle)
    payload = run_ready_outcome_holdout(current_payload=current_payload)
    print(json.dumps(payload["holdout_comparison"], indent=2))


if __name__ == "__main__":
    main()