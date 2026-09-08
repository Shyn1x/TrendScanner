"""Third, non-overlapping temporal validation window for READY outcomes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from historical_replay import replay_timeframe
from ready_outcome_analyzer import analyze_ready_outcomes
from ready_outcome_holdout import HOLDOUT_LIMIT, TIMEFRAME_MS
from ready_outcome_pilot import HORIZONS, MAX_REPLAY_BARS, SYMBOLS, TIMEFRAMES, WARMUP_BARS, _prepare_run_result, build_aggregate, collect_examples, save_events_csv, save_results_json
from research_data import get_research_data_before

HOLDOUT1_PATH = "artifacts/trendscanner/ready_outcome_holdout_results.json"
CURRENT_PATH = "artifacts/trendscanner/ready_outcome_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_outcome_holdout2_results.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_outcome_holdout2_events.csv"


def _window(payload: dict, symbol: str) -> tuple[int, int]:
    for run in payload.get("runs", []):
        if isinstance(run, dict) and run.get("symbol") == symbol and run.get("status") == "OK":
            data = run.get("holdout_window", {})
            if isinstance(data, dict) and isinstance(data.get("holdout_first_timestamp"), int) and isinstance(data.get("holdout_last_timestamp"), int):
                return data["holdout_first_timestamp"], data["holdout_last_timestamp"]
    raise ValueError(f"missing HOLDOUT1 window for {symbol}")


def _current_window(payload: dict, symbol: str) -> tuple[int, int]:
    from ready_outcome_holdout import derive_current_first_timestamp
    start = derive_current_first_timestamp(payload, symbol, "4h")
    return start, start + 999 * TIMEFRAME_MS["4h"]


def prepare_holdout2_window(frame: pd.DataFrame, holdout1_first: int, holdout1_range: tuple[int, int], current_range: tuple[int, int]) -> tuple[pd.DataFrame, dict]:
    data = frame.copy(deep=True).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    timestamps = [int(value) for value in data["time"]]
    overlaps_holdout1 = sum(holdout1_range[0] <= value <= holdout1_range[1] for value in timestamps)
    overlaps_current = sum(current_range[0] <= value <= current_range[1] for value in timestamps)
    diagnostic = {"holdout1_first_timestamp": holdout1_first, "holdout2_first_timestamp": timestamps[0] if timestamps else None, "holdout2_last_timestamp": timestamps[-1] if timestamps else None, "holdout2_rows": len(data), "overlap_holdout1_count": overlaps_holdout1, "overlap_current_count": overlaps_current}
    diagnostic["eligible"] = diagnostic["holdout2_rows"] == HOLDOUT_LIMIT and diagnostic["holdout2_last_timestamp"] is not None and diagnostic["holdout2_last_timestamp"] < holdout1_first and overlaps_holdout1 == 0 and overlaps_current == 0
    return data, diagnostic


def preflight_holdout2(holdout1: dict, current: dict, symbols: list[str] = SYMBOLS, loader: Callable[..., pd.DataFrame] = get_research_data_before) -> list[dict]:
    rows = []
    for symbol in symbols:
        try:
            holdout1_range = _window(holdout1, symbol)
            frame = loader(symbol, "4h", holdout1_range[0], total_limit=HOLDOUT_LIMIT)
            _, diagnostic = prepare_holdout2_window(frame, holdout1_range[0], holdout1_range, _current_window(current, symbol))
            rows.append({"symbol": symbol, "status": "READY" if diagnostic["eligible"] else "FAILED", **diagnostic})
        except Exception as exc:
            rows.append({"symbol": symbol, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"})
    return rows


def run_ready_outcome_holdout2(*, holdout1_payload: dict, current_payload: dict, symbols: list[str] = SYMBOLS, output_json: str = OUTPUT_JSON, output_csv: str = OUTPUT_CSV, loader: Callable[..., pd.DataFrame] = get_research_data_before, replay_fn: Callable[..., dict] = replay_timeframe, analyzer: Callable[..., dict] = analyze_ready_outcomes, progress: bool = True) -> dict:
    source1, source_current = copy.deepcopy(holdout1_payload), copy.deepcopy(current_payload)
    runs = []
    for row in preflight_holdout2(source1, source_current, symbols, loader):
        symbol = row["symbol"]
        if progress: print(f"{symbol} | holdout2_first={row.get('holdout2_first_timestamp')} | holdout2_last={row.get('holdout2_last_timestamp')} | rows={row.get('holdout2_rows')} | overlap H1/current={row.get('overlap_holdout1_count')}/{row.get('overlap_current_count')} | {row['status']}", flush=True)
        try:
            if row["status"] != "READY": raise ValueError(row.get("error", "HOLDOUT2 preflight failed"))
            frame = loader(symbol, "4h", row["holdout1_first_timestamp"], total_limit=HOLDOUT_LIMIT)
            frame, diagnostic = prepare_holdout2_window(frame, row["holdout1_first_timestamp"], _window(source1, symbol), _current_window(source_current, symbol))
            replay = replay_fn(frame, symbol=symbol, timeframe="4h", warmup_bars=WARMUP_BARS, max_replay_bars=MAX_REPLAY_BARS)
            analyzed = analyzer(frame, replay.get("replay_results", []), horizons=HORIZONS)
            run = _prepare_run_result(symbol=symbol, timeframe="4h", status="OK", error=None, replay_meta=replay.get("meta", {}), summary=analyzed.get("summary", {}), events=analyzed.get("events", []))
            run["holdout2_window"] = diagnostic
        except Exception as exc:
            run = _prepare_run_result(symbol=symbol, timeframe="4h", status="FAILED", error=f"{type(exc).__name__}: {exc}", replay_meta={}, summary={}, events=[])
            run["holdout2_window"] = row
        runs.append(run)
    payload = {"config": {"symbols": list(symbols), "timeframes": TIMEFRAMES, "horizons": HORIZONS, "warmup_bars": WARMUP_BARS, "max_replay_bars": MAX_REPLAY_BARS, "holdout_limit": HOLDOUT_LIMIT}, "runs": runs, "aggregate": build_aggregate(runs, HORIZONS), "examples": collect_examples(runs, 5, 5)}
    payload["output_json"] = str(save_results_json(output_json, payload)); payload["output_csv"] = str(save_events_csv(output_csv, runs, HORIZONS))
    return payload


def main() -> None:
    holdout1 = json.loads(Path(HOLDOUT1_PATH).read_text(encoding="utf-8")); current = json.loads(Path(CURRENT_PATH).read_text(encoding="utf-8"))
    payload = run_ready_outcome_holdout2(holdout1_payload=holdout1, current_payload=current)
    print(json.dumps(payload["aggregate"], indent=2))


if __name__ == "__main__": main()