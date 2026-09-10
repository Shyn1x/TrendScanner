"""Compare corrected historical READY events with a fixed 200-bar live window.

This audit does not change production or prospective logic. It replays the
CURRENT 4h research period using exactly 200 candles at every historical
observation timestamp, then compares False->True READY transitions and P3
membership against the stored corrected historical result.
"""
from __future__ import annotations

import json
from pathlib import Path

from analysis import analyze_timeframe
from ready_engine import evaluate_ready_candidate
from ready_market_regime_analysis import _attach_market, _frame_features
from ready_outcome_holdout import derive_current_first_timestamp
from ready_outcome_pilot import MAX_REPLAY_BARS, SYMBOLS, WARMUP_BARS
from research_data import get_research_data_before

TIMEFRAME = "4h"
TIMEFRAME_MS = 14_400_000
RESEARCH_ROWS = 1000
CONTEXT_ROWS = 213
AUDIT_ROWS = RESEARCH_ROWS + CONTEXT_ROWS
LIVE_ROWS = 200
CURRENT_PATH = "artifacts/trendscanner/ready_outcome_results.json"
OUTPUT_PATH = "artifacts/trendscanner/ready_window_parity_current.json"
DIRECTIONS = ("LONG", "SHORT")


def _key(symbol: str, direction: str, timestamp: int) -> tuple[str, str, int]:
    return symbol, direction, int(timestamp)


def _historical_events(payload: dict) -> dict[tuple[str, str, int], dict]:
    events = {}
    for run in payload.get("runs", []):
        if not isinstance(run, dict) or run.get("status") != "OK":
            continue
        for event in run.get("events", []):
            if not isinstance(event, dict):
                continue
            symbol = str(event.get("symbol", ""))
            direction = str(event.get("direction", "")).upper()
            timestamp = event.get("ready_timestamp")
            if symbol in SYMBOLS and direction in DIRECTIONS and isinstance(timestamp, int):
                events[_key(symbol, direction, timestamp)] = event
    return events


def _validate_frame(frame, *, start: int, end: int, symbol: str):
    if len(frame) != AUDIT_ROWS:
        raise ValueError(f"{symbol}: expected {AUDIT_ROWS} rows, got {len(frame)}")
    data = frame.copy(deep=True).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    if len(data) != AUDIT_ROWS:
        raise ValueError(f"{symbol}: duplicate/missing timestamps")
    times = [int(value) for value in data["time"]]
    expected_first = start - CONTEXT_ROWS * TIMEFRAME_MS
    expected = [expected_first + index * TIMEFRAME_MS for index in range(AUDIT_ROWS)]
    if times != expected:
        raise ValueError(f"{symbol}: non-exact 4h grid")
    research = data.iloc[-RESEARCH_ROWS:].reset_index(drop=True)
    if int(research.iloc[0]["time"]) != start or int(research.iloc[-1]["time"]) != end:
        raise ValueError(f"{symbol}: reconstructed CURRENT window mismatch")
    return data, research


def _simulate_symbol(full_frame, research_frame, symbol: str) -> list[dict]:
    events = []
    ready_state = {direction: False for direction in DIRECTIONS}
    offset = len(full_frame) - len(research_frame)
    actual_steps = min(MAX_REPLAY_BARS, len(research_frame) - WARMUP_BARS)

    for step in range(actual_steps):
        end_index = WARMUP_BARS + step
        full_end = offset + end_index
        live_start = full_end - LIVE_ROWS + 1
        if live_start < 0:
            raise ValueError(f"{symbol}: insufficient prehistory for 200-bar window")
        live_df = full_frame.iloc[live_start:full_end + 1].copy().reset_index(drop=True)
        if len(live_df) != LIVE_ROWS:
            raise ValueError(f"{symbol}: fixed live window is not 200 rows")

        result = analyze_timeframe(live_df)
        if not isinstance(result, dict) or result.get("trend") == "ERROR":
            raise ValueError(f"{symbol}: pipeline error at replay step {step}")

        expected_timestamp = int(research_frame.iloc[end_index - 1]["time"])
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
            is_ready = candidate.get("ready") is True
            was_ready = ready_state[direction]
            ready_state[direction] = is_ready
            if is_ready and not was_ready:
                events.append({
                    "symbol": symbol,
                    "direction": direction,
                    "ready_timestamp": expected_timestamp,
                    "confidence": candidate.get("confidence"),
                    "decision_score": candidate.get("decision_score"),
                    "replay_index": step,
                })
    return events


def _p3_keys(records: list[dict], contexts: dict) -> tuple[set[tuple[str, str, int]], dict]:
    enriched, diagnostics = _attach_market(records, contexts)
    keys = {
        _key(str(row["symbol"]), str(row["direction"]), int(row["ready_timestamp"]))
        for row in enriched
        if row.get("direction") == "LONG"
        and row.get("market_regime") == "MIXED"
        and row.get("volatility") == "NORMAL"
    }
    return keys, diagnostics


def _serialize_keys(keys: set[tuple[str, str, int]]) -> list[dict]:
    return [
        {"symbol": symbol, "direction": direction, "ready_timestamp": timestamp}
        for symbol, direction, timestamp in sorted(keys)
    ]


def run_audit() -> dict:
    payload = json.loads(Path(CURRENT_PATH).read_text(encoding="utf-8"))
    historical = _historical_events(payload)
    fixed_events = []
    contexts = {}
    windows = {}

    for index, symbol in enumerate(SYMBOLS, start=1):
        start = derive_current_first_timestamp(payload, symbol, TIMEFRAME)
        end = start + (RESEARCH_ROWS - 1) * TIMEFRAME_MS
        frame = get_research_data_before(
            symbol,
            TIMEFRAME,
            before_timestamp=end + TIMEFRAME_MS,
            total_limit=AUDIT_ROWS,
        )
        full_frame, research_frame = _validate_frame(frame, start=start, end=end, symbol=symbol)
        contexts[("CURRENT", symbol)] = _frame_features(full_frame)
        symbol_events = _simulate_symbol(full_frame, research_frame, symbol)
        fixed_events.extend(symbol_events)
        windows[symbol] = {"start": start, "end": end, "fixed200_events": len(symbol_events)}
        print(f"[{index:02d}/{len(SYMBOLS)}] {symbol}: fixed200 transitions={len(symbol_events)}", flush=True)

    fixed = {
        _key(event["symbol"], event["direction"], event["ready_timestamp"]): event
        for event in fixed_events
    }
    historical_keys = set(historical)
    fixed_keys = set(fixed)

    historical_records = [
        {"period": "CURRENT", "symbol": symbol, "direction": direction, "ready_timestamp": timestamp}
        for symbol, direction, timestamp in historical_keys
    ]
    fixed_records = [
        {"period": "CURRENT", "symbol": symbol, "direction": direction, "ready_timestamp": timestamp}
        for symbol, direction, timestamp in fixed_keys
    ]
    historical_p3, historical_context_diag = _p3_keys(historical_records, contexts)
    fixed_p3, fixed_context_diag = _p3_keys(fixed_records, contexts)

    retained = historical_keys & fixed_keys
    lost = historical_keys - fixed_keys
    new = fixed_keys - historical_keys
    p3_retained = historical_p3 & fixed_p3
    p3_lost = historical_p3 - fixed_p3
    p3_new = fixed_p3 - historical_p3

    confidence_deltas = []
    score_deltas = []
    for key in retained:
        old = historical[key]
        new_event = fixed[key]
        old_conf = old.get("ready_confidence")
        new_conf = new_event.get("confidence")
        old_score = old.get("ready_decision_score")
        new_score = new_event.get("decision_score")
        if isinstance(old_conf, (int, float)) and isinstance(new_conf, (int, float)):
            confidence_deltas.append(abs(float(new_conf) - float(old_conf)))
        if isinstance(old_score, (int, float)) and isinstance(new_score, (int, float)):
            score_deltas.append(abs(float(new_score) - float(old_score)))

    report = {
        "period": "CURRENT",
        "historical_model": "growing prefix from 1000-row research window",
        "live_model": "fixed last 200 rows at the same timestamps",
        "symbols": len(SYMBOLS),
        "historical_ready_events": len(historical_keys),
        "fixed200_ready_events": len(fixed_keys),
        "ready_event_parity": {
            "exact": historical_keys == fixed_keys,
            "retained": len(retained),
            "lost": len(lost),
            "new": len(new),
            "lost_events": _serialize_keys(lost),
            "new_events": _serialize_keys(new),
        },
        "p3_parity": {
            "exact": historical_p3 == fixed_p3,
            "historical": len(historical_p3),
            "fixed200": len(fixed_p3),
            "retained": len(p3_retained),
            "lost": len(p3_lost),
            "new": len(p3_new),
            "lost_events": _serialize_keys(p3_lost),
            "new_events": _serialize_keys(p3_new),
        },
        "retained_event_metric_drift": {
            "mean_abs_confidence_delta": (
                sum(confidence_deltas) / len(confidence_deltas) if confidence_deltas else None
            ),
            "max_abs_confidence_delta": max(confidence_deltas) if confidence_deltas else None,
            "mean_abs_decision_score_delta": (
                sum(score_deltas) / len(score_deltas) if score_deltas else None
            ),
            "max_abs_decision_score_delta": max(score_deltas) if score_deltas else None,
        },
        "context_diagnostics": {
            "historical": historical_context_diag,
            "fixed200": fixed_context_diag,
        },
        "windows": windows,
    }
    Path(OUTPUT_PATH).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> None:
    report = run_audit()
    summary = {
        "historical_ready_events": report["historical_ready_events"],
        "fixed200_ready_events": report["fixed200_ready_events"],
        "ready_event_parity": report["ready_event_parity"],
        "p3_parity": report["p3_parity"],
        "retained_event_metric_drift": report["retained_event_metric_drift"],
    }
    # Keep console output compact; full event lists are in the JSON artifact.
    summary["ready_event_parity"] = {
        key: value for key, value in summary["ready_event_parity"].items()
        if key not in ("lost_events", "new_events")
    }
    summary["p3_parity"] = {
        key: value for key, value in summary["p3_parity"].items()
        if key not in ("lost_events", "new_events")
    }
    print("READY WINDOW PARITY RESULT")
    print(json.dumps(summary, indent=2, allow_nan=False))
    print(f"Full report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
