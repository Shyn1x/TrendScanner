"""Validate P3 with the same fixed 200-bar READY window used prospectively.

Research-only. This script does not write to Neon and does not change P3,
thresholds, or production code. It reconstructs CURRENT, HOLDOUT1, and HOLDOUT2
4h windows, evaluates READY from exactly the latest 200 rows at every replay
step, attaches the same past-only market regime/volatility context, and reports
H5 P3 outcomes.
"""
from __future__ import annotations

import json
from pathlib import Path

from analysis import analyze_timeframe
from ready_engine import evaluate_ready_candidate
from ready_market_regime_analysis import _attach_market, _frame_features, _metrics, _records
from ready_outcome_holdout import derive_current_first_timestamp
from ready_outcome_pilot import MAX_REPLAY_BARS, SYMBOLS, WARMUP_BARS
from research_data import get_research_data_before

TIMEFRAME = "4h"
TIMEFRAME_MS = 14_400_000
RESEARCH_ROWS = 1000
CONTEXT_ROWS = 213
TOTAL_ROWS = RESEARCH_ROWS + CONTEXT_ROWS
LIVE_ROWS = 200
DIRECTIONS = ("LONG", "SHORT")
MIN_SAMPLE = 15

CURRENT_PATH = "artifacts/trendscanner/ready_outcome_results.json"
HOLDOUT1_PATH = "artifacts/trendscanner/ready_outcome_holdout_results.json"
OUTPUT_PATH = "artifacts/trendscanner/fixed200_temporal_validation.json"
PERIODS = ("CURRENT", "HOLDOUT1", "HOLDOUT2")


def _event_key(row: dict) -> tuple[str, str, int]:
    return str(row["symbol"]), str(row["direction"]), int(row["ready_timestamp"])


def _find_holdout1_bounds(payload: dict, symbol: str) -> tuple[int, int]:
    for run in payload.get("runs", []):
        if not isinstance(run, dict) or run.get("status") != "OK" or run.get("symbol") != symbol:
            continue
        window = run.get("holdout_window", {})
        start = window.get("holdout_first_timestamp") if isinstance(window, dict) else None
        end = window.get("holdout_last_timestamp") if isinstance(window, dict) else None
        if isinstance(start, int) and isinstance(end, int):
            return start, end
    raise ValueError(f"missing HOLDOUT1 bounds for {symbol}")


def _bounds(period: str, symbol: str, current_payload: dict, holdout1_payload: dict) -> tuple[int, int]:
    if period == "CURRENT":
        start = derive_current_first_timestamp(current_payload, symbol, TIMEFRAME)
        return start, start + (RESEARCH_ROWS - 1) * TIMEFRAME_MS

    h1_start, h1_end = _find_holdout1_bounds(holdout1_payload, symbol)
    if period == "HOLDOUT1":
        return h1_start, h1_end
    if period == "HOLDOUT2":
        end = h1_start - TIMEFRAME_MS
        return end - (RESEARCH_ROWS - 1) * TIMEFRAME_MS, end
    raise ValueError(f"unknown period: {period}")


def _load_exact_window(symbol: str, start: int, end: int):
    frame = get_research_data_before(
        symbol,
        TIMEFRAME,
        before_timestamp=end + TIMEFRAME_MS,
        total_limit=TOTAL_ROWS,
    )
    frame = frame.copy(deep=True).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    if len(frame) != TOTAL_ROWS:
        raise ValueError(f"{symbol}: expected {TOTAL_ROWS} rows, got {len(frame)}")

    first_expected = start - CONTEXT_ROWS * TIMEFRAME_MS
    expected = [first_expected + index * TIMEFRAME_MS for index in range(TOTAL_ROWS)]
    actual = [int(value) for value in frame["time"]]
    if actual != expected:
        raise ValueError(f"{symbol}: non-exact 4h grid")

    research = frame.iloc[-RESEARCH_ROWS:].copy().reset_index(drop=True)
    if int(research.iloc[0]["time"]) != start or int(research.iloc[-1]["time"]) != end:
        raise ValueError(f"{symbol}: period reconstruction mismatch")
    return frame, research


def _h5(direction: str, research, candle_index: int) -> dict:
    start = candle_index + 1
    stop = start + 5
    if stop > len(research):
        return {"available": False}

    entry = float(research.iloc[candle_index]["close"])
    future = research.iloc[start:stop]
    highs = [float(value) for value in future["high"]]
    lows = [float(value) for value in future["low"]]
    closes = [float(value) for value in future["close"]]
    if entry <= 0 or len(highs) != 5 or len(lows) != 5 or len(closes) != 5:
        return {"available": False}

    if direction == "LONG":
        mfe = max(0.0, (max(highs) - entry) / entry * 100.0)
        mae = max(0.0, (entry - min(lows)) / entry * 100.0)
        close_return = (closes[-1] - entry) / entry * 100.0
    else:
        mfe = max(0.0, (entry - min(lows)) / entry * 100.0)
        mae = max(0.0, (max(highs) - entry) / entry * 100.0)
        close_return = (entry - closes[-1]) / entry * 100.0

    return {
        "available": True,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "close_return_pct": close_return,
    }


def _simulate_symbol(period: str, symbol: str, full_frame, research) -> list[dict]:
    events: list[dict] = []
    ready_state = {direction: False for direction in DIRECTIONS}
    offset = len(full_frame) - len(research)
    actual_steps = min(MAX_REPLAY_BARS, len(research) - WARMUP_BARS)

    for step in range(actual_steps):
        end_index = WARMUP_BARS + step
        full_end = offset + end_index
        live_start = full_end - LIVE_ROWS + 1
        if live_start < 0:
            raise ValueError(f"{symbol}: insufficient fixed-200 prehistory")

        live_df = full_frame.iloc[live_start:full_end + 1].copy().reset_index(drop=True)
        if len(live_df) != LIVE_ROWS:
            raise ValueError(f"{symbol}: fixed live window is not 200 rows")

        result = analyze_timeframe(live_df)
        if not isinstance(result, dict) or result.get("trend") == "ERROR":
            raise ValueError(f"{symbol}: pipeline error at replay step {step}")

        signal_index = end_index - 1
        expected_timestamp = int(research.iloc[signal_index]["time"])
        if result.get("ready_timestamp") != expected_timestamp:
            raise ValueError(f"{symbol}: READY timestamp mismatch at step {step}")

        for direction in DIRECTIONS:
            available = (
                result.get("quality", {})
                .get(direction, {})
                .get("analysis_available")
            )
            if available is not True:
                raise ValueError(f"{symbol} {direction}: analysis unavailable at {expected_timestamp}")

            candidate = evaluate_ready_candidate(symbol, TIMEFRAME, direction, result)
            if type(candidate.get("ready")) is not bool:
                raise ValueError(f"{symbol} {direction}: invalid READY value")

            is_ready = candidate["ready"]
            was_ready = ready_state[direction]
            ready_state[direction] = is_ready
            if not is_ready or was_ready:
                continue

            outcome = _h5(direction, research, signal_index)
            row = {
                "period": period,
                "symbol": symbol,
                "timeframe": TIMEFRAME,
                "direction": direction,
                "ready_timestamp": expected_timestamp,
                "ready_confidence": candidate.get("confidence"),
                "ready_decision_score": candidate.get("decision_score"),
                "h5_available": outcome.get("available") is True,
            }
            if outcome.get("available") is True:
                row.update(
                    mfe_pct=outcome["mfe_pct"],
                    mae_pct=outcome["mae_pct"],
                    close_return_pct=outcome["close_return_pct"],
                )
            events.append(row)

    return events


def _p3(records: list[dict], contexts: dict) -> tuple[list[dict], dict]:
    enriched, diagnostics = _attach_market(records, contexts)
    rows = [
        row for row in enriched
        if row.get("direction") == "LONG"
        and row.get("market_regime") == "MIXED"
        and row.get("volatility") == "NORMAL"
    ]
    return rows, diagnostics


def _metric_summary(rows: list[dict]) -> dict:
    available = [row for row in rows if row.get("h5_available") is True]
    metrics = _metrics(available)
    edge = metrics.get("mfe_minus_mae_pct")
    if metrics["n"] < MIN_SAMPLE:
        verdict = "INSUFFICIENT_SAMPLE"
    elif edge is not None and edge > 0:
        verdict = "SUPPORTED"
    else:
        verdict = "NOT_SUPPORTED"
    return {
        "raw_p3_transitions": len(rows),
        "h5_available_p3": len(available),
        **metrics,
        "verdict": verdict,
    }


def _historical_current_discrepancy(current_payload: dict, contexts: dict) -> dict:
    raw_records = []
    for run in current_payload.get("runs", []):
        if not isinstance(run, dict) or run.get("status") != "OK":
            continue
        for event in run.get("events", []):
            if not isinstance(event, dict):
                continue
            symbol = str(event.get("symbol", "")).strip()
            direction = str(event.get("direction", "")).upper()
            timestamp = event.get("ready_timestamp")
            if symbol in SYMBOLS and direction in DIRECTIONS and isinstance(timestamp, int):
                raw_records.append({
                    "period": "CURRENT",
                    "symbol": symbol,
                    "direction": direction,
                    "ready_timestamp": timestamp,
                })

    raw_p3, raw_diag = _p3(raw_records, contexts)
    h5_records, skipped = _records(current_payload, "CURRENT")
    h5_p3, h5_diag = _p3(h5_records, contexts)
    raw_keys = {_event_key(row) for row in raw_p3}
    h5_keys = {_event_key(row) for row in h5_p3}
    unavailable = sorted(raw_keys - h5_keys)

    return {
        "raw_historical_ready_events": len(raw_records),
        "raw_historical_p3": len(raw_p3),
        "h5_available_historical_p3": len(h5_p3),
        "p3_without_h5_outcome": len(unavailable),
        "p3_without_h5_keys": [
            {"symbol": symbol, "direction": direction, "ready_timestamp": timestamp}
            for symbol, direction, timestamp in unavailable
        ],
        "skipped_malformed_h5_records": skipped,
        "raw_context_diagnostics": raw_diag,
        "h5_context_diagnostics": h5_diag,
        "explanation": (
            "Historical regime outcome tables count only READY events with an available H5 outcome; "
            "the raw parity audit counts every False->True READY transition, including tail events "
            "whose five future 4h bars fall outside the research window."
        ),
    }


def run_validation() -> dict:
    current_payload = json.loads(Path(CURRENT_PATH).read_text(encoding="utf-8"))
    holdout1_payload = json.loads(Path(HOLDOUT1_PATH).read_text(encoding="utf-8"))

    contexts: dict = {}
    events_by_period = {period: [] for period in PERIODS}
    windows: dict[str, dict] = {period: {} for period in PERIODS}

    for period in PERIODS:
        print(f"=== {period} ===", flush=True)
        for index, symbol in enumerate(SYMBOLS, start=1):
            start, end = _bounds(period, symbol, current_payload, holdout1_payload)
            full_frame, research = _load_exact_window(symbol, start, end)
            contexts[(period, symbol)] = _frame_features(full_frame)
            symbol_events = _simulate_symbol(period, symbol, full_frame, research)
            events_by_period[period].extend(symbol_events)
            windows[period][symbol] = {
                "start": start,
                "end": end,
                "fixed200_ready_events": len(symbol_events),
            }
            print(
                f"[{index:02d}/{len(SYMBOLS)}] {symbol}: fixed200 transitions={len(symbol_events)}",
                flush=True,
            )

    results = {}
    for period in PERIODS:
        raw = events_by_period[period]
        p3_rows, diag = _p3(raw, contexts)
        summary = _metric_summary(p3_rows)
        summary.update(
            total_ready_transitions=len(raw),
            h5_available_ready_transitions=sum(row.get("h5_available") is True for row in raw),
            context_diagnostics=diag,
        )
        results[period] = summary

    all_supported = all(results[period]["verdict"] == "SUPPORTED" for period in PERIODS)
    report = {
        "model": "fixed last 200 rows at every historical observation timestamp",
        "timeframe": TIMEFRAME,
        "symbols": list(SYMBOLS),
        "p3_definition": {
            "direction": "LONG",
            "market_regime": "MIXED",
            "volatility": "NORMAL",
            "outcome_horizon_bars": 5,
            "minimum_sample": MIN_SAMPLE,
        },
        "current_50_vs_54_diagnostic": _historical_current_discrepancy(
            current_payload,
            {key: value for key, value in contexts.items() if key[0] == "CURRENT"},
        ),
        "fixed200_results": results,
        "overall_verdict": "SUPPORTED_ALL_PERIODS" if all_supported else "NOT_SUPPORTED_ALL_PERIODS",
        "windows": windows,
    }
    Path(OUTPUT_PATH).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> None:
    report = run_validation()
    compact = {
        "current_50_vs_54_diagnostic": {
            "raw_historical_p3": report["current_50_vs_54_diagnostic"]["raw_historical_p3"],
            "h5_available_historical_p3": report["current_50_vs_54_diagnostic"]["h5_available_historical_p3"],
            "p3_without_h5_outcome": report["current_50_vs_54_diagnostic"]["p3_without_h5_outcome"],
        },
        "fixed200_results": report["fixed200_results"],
        "overall_verdict": report["overall_verdict"],
    }
    print("FIXED-200 TEMPORAL P3 VALIDATION")
    print(json.dumps(compact, indent=2, allow_nan=False))
    print(f"Full report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
