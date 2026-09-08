"""Offline execution scenarios for saved READY events; no signal recalculation."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics

import pandas as pd

TIMEFRAMES = {"1h": 3_600_000, "4h": 14_400_000}


@dataclass(frozen=True)
class Scenario:
    stop_pct: float
    target_pct: float
    max_bars: int
    entry_fee_bps: float
    exit_fee_bps: float
    slippage_bps: float
    funding_cost_bps: float

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not isinstance(self.max_bars, int) or self.max_bars < 1:
            raise ValueError("max_bars must be a positive integer")
        if not 0 < self.stop_pct < 100 or not 0 < self.target_pct < 100:
            raise ValueError("stop_pct and target_pct must be between 0 and 100")
        for name in ("entry_fee_bps", "exit_fee_bps", "slippage_bps", "funding_cost_bps"):
            if not 0 <= getattr(self, name) < 10_000:
                raise ValueError(f"{name} must be between 0 and 10000 bps")


def _timestamp(value) -> int:
    if isinstance(value, bool):
        raise ValueError("Invalid timestamp")
    result = int(value)
    if result < 0 or result != float(value):
        raise ValueError("Invalid timestamp")
    return result


def _index(frame: pd.DataFrame) -> dict[int, list[dict]]:
    result = {}
    if "time" not in frame.columns:
        raise ValueError("Candles require a time column")
    for row in frame.to_dict("records"):
        timestamp = _timestamp(row["time"])
        result.setdefault(timestamp, []).append(row)
    return result


def _bar(index: dict, timestamp: int) -> tuple[dict | None, str | None]:
    rows = index.get(timestamp, [])
    if not rows:
        return None, "MISSING_CANDLE"
    if len(rows) != 1:
        return None, "DUPLICATE_TIMESTAMP"
    try:
        raw = [rows[0][key] for key in ("open", "high", "low", "close")]
        if any(isinstance(value, bool) for value in raw):
            raise ValueError("Boolean price")
        op, high, low, close = map(float, raw)
        if not all(math.isfinite(value) and value > 0 for value in (op, high, low, close)):
            raise ValueError("Invalid price")
        if low > min(op, close) or high < max(op, close) or low > high:
            raise ValueError("Invalid candle range")
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, "INVALID_OHLC"
    return {"open": op, "high": high, "low": low, "close": close}, None


def _pnl(entry: float, reference_exit: float, direction: int, scenario: Scenario) -> dict:
    exit_price = reference_exit * (1 - direction * scenario.slippage_bps / 10_000)
    gross = direction * (exit_price / entry - 1) * 100
    fees = scenario.entry_fee_bps / 100 + scenario.exit_fee_bps / 100 * exit_price / entry
    funding = scenario.funding_cost_bps / 100
    return {"exit_price": exit_price, "gross_return_pct": gross, "fees_pct": fees,
            "funding_cost_pct": funding, "net_return_pct": gross - fees - funding}


def evaluate_event(event: dict, frame: pd.DataFrame, scenario: Scenario, as_of_ms: int) -> dict:
    """Evaluate an independent candidate, using only closed post-signal candles.

    Signal timestamps identify candle opens, as in ready_outcome_analyzer.
    A signal becomes actionable at that candle's close. Entry is the next open.
    """
    identity = {key: event.get(key) for key in ("symbol", "timeframe", "direction", "ready_timestamp")}
    result = {**identity, "status": "INVALID", "net_return_pct": None}
    try:
        timestamp, cutoff = _timestamp(event["ready_timestamp"]), _timestamp(as_of_ms)
        timeframe = TIMEFRAMES[event["timeframe"]]
        if event["direction"] not in ("LONG", "SHORT") or not event.get("symbol"):
            raise ValueError("Invalid event identity")
        index = _index(frame)
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**result, "reason": "INVALID_EVENT_OR_TIMESTAMPS"}
    signal, error = _bar(index, timestamp)
    if error:
        return {**result, "reason": error}
    direction = 1 if event["direction"] == "LONG" else -1
    entry, stop, target = None, None, None
    for bars_held in range(1, scenario.max_bars + 1):
        candle_time = timestamp + bars_held * timeframe
        if candle_time + timeframe > cutoff:
            return {**result, "status": "CENSORED", "reason": "INCOMPLETE_HORIZON"}
        bar, error = _bar(index, candle_time)
        if error:
            return {**result, "reason": error}
        if entry is None:
            entry = bar["open"] * (1 + direction * scenario.slippage_bps / 10_000)
            stop = entry * (1 - direction * scenario.stop_pct / 100)
            target = entry * (1 + direction * scenario.target_pct / 100)
            result.update(entry_timestamp=candle_time, entry_price=entry, stop_price=stop, target_price=target)
        stop_at_open = direction * (bar["open"] - stop) <= 0
        target_at_open = direction * (bar["open"] - target) >= 0
        stop_touched = bar["low"] <= stop if direction == 1 else bar["high"] >= stop
        target_touched = bar["high"] >= target if direction == 1 else bar["low"] <= target
        ambiguous = False
        if stop_at_open or target_at_open:
            reason, price = ("STOP_GAP" if stop_at_open else "TARGET_GAP"), bar["open"]
        elif stop_touched and target_touched:
            reason, price, ambiguous = "BOTH_TOUCHED_STOP_FIRST", stop, True
        elif stop_touched:
            reason, price = "STOP", stop
        elif target_touched:
            reason, price = "TARGET", target
        elif bars_held == scenario.max_bars:
            reason, price = "TIME_EXIT", bar["close"]
        else:
            continue
        conservative = _pnl(entry, price, direction, scenario)
        optimistic = _pnl(entry, target, direction, scenario) if ambiguous else conservative
        return {**result, **conservative, "status": "RESOLVED", "exit_reason": reason,
                "exit_candle_timestamp": candle_time, "bars_held": bars_held,
                "intrabar_ambiguous": ambiguous, "optimistic_net_return_pct": optimistic["net_return_pct"]}
    raise AssertionError("unreachable")


def _events(payload: dict) -> list[dict]:
    events = []
    for run in payload.get("runs", []):
        if run.get("status") == "OK":
            events.extend(run.get("events", []))
    return events


def summarize(results: list[dict]) -> dict:
    resolved = [row for row in results if row["status"] == "RESOLVED"]
    values = [row["net_return_pct"] for row in resolved]
    return {"events": len(results), "status_counts": dict(Counter(row["status"] for row in results)),
            "resolved_events": len(resolved), "ambiguous_events": sum(row["intrabar_ambiguous"] for row in resolved),
            "mean_net_return_pct": statistics.mean(values) if values else None,
            "median_net_return_pct": statistics.median(values) if values else None,
            "mean_optimistic_net_return_pct": statistics.mean(row["optimistic_net_return_pct"] for row in resolved) if resolved else None,
            "net_positive_rate_pct": sum(value > 0 for value in values) / len(values) * 100 if values else None}


def compare_periods(current: dict, holdout: dict, frames: dict, scenario: Scenario, as_of_ms: int) -> dict:
    """Frames are keyed by (CURRENT/HOLDOUT, symbol, timeframe)."""
    as_of_ms = _timestamp(as_of_ms)
    periods = {"CURRENT": _events(current), "HOLDOUT": _events(holdout)}
    spans = {}
    for period, events in periods.items():
        times = [_timestamp(event["ready_timestamp"]) for event in events]
        # Include the complete potential outcome horizon, not only signal times.
        ends = [_timestamp(event["ready_timestamp"]) + (scenario.max_bars + 1) * TIMEFRAMES[event["timeframe"]] for event in events]
        spans[period] = {"start": min(times) if times else None, "end_exclusive": max(ends) if ends else None}
    if periods["CURRENT"] and periods["HOLDOUT"] and spans["HOLDOUT"]["end_exclusive"] > spans["CURRENT"]["start"]:
        raise ValueError("HOLDOUT including outcome horizons must end before CURRENT")
    results, seen = [], set()
    for period, events in periods.items():
        for event in events:
            key = (period, event["symbol"], event["timeframe"], event["direction"], _timestamp(event["ready_timestamp"]))
            if key in seen:
                raise ValueError(f"Duplicate READY event: {key}")
            seen.add(key)
            frame = frames.get(key[:3])
            if frame is None:
                result = {**event, "status": "INVALID", "reason": "MISSING_CANDLE_FILE", "net_return_pct": None}
            else:
                result = evaluate_event(event, frame, scenario, as_of_ms)
            results.append({**result, "period": period})
    groups = [{"period": period, "direction": direction,
               **summarize([row for row in results if row["period"] == period and row["direction"] == direction])}
              for period in periods for direction in ("LONG", "SHORT")]
    counts = summarize(results)
    coverage = {key: counts[key] for key in ("events", "status_counts", "resolved_events", "ambiguous_events")}
    return {"schema_version": 1, "as_of_ms": as_of_ms, "scenario": asdict(scenario), "period_spans": spans,
            "summary": coverage, "by_period_direction": groups, "events": results,
            "interpretation": "Independent candidate outcomes, not a portfolio return. Ambiguous bars use stop-first with a separate target-first estimate. Funding is an explicit fixed scenario cost, not historical funding."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("current", "holdout", "manifest", "scenario", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--as-of-ms", type=int, required=True)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    frames = {}
    for item in read(args.manifest):
        key = (item["period"], item["symbol"], item["timeframe"])
        if key in frames:
            raise ValueError(f"Duplicate manifest key: {key}")
        frames[key] = pd.read_csv(args.manifest.parent / item["path"])
    report = compare_periods(read(args.current), read(args.holdout), frames, Scenario(**read(args.scenario)), args.as_of_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], allow_nan=False))
    return 0 if report["summary"]["events"] and report["summary"]["resolved_events"] == report["summary"]["events"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
