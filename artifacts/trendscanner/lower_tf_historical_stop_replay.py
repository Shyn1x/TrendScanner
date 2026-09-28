"""Strict historical lower-TF READY and fixed-stop replay.

This research-only module reconstructs the current prospective 1h/15m
decision contract candle by candle.  It deliberately reuses the prospective
fixed-200 window builder, lower-TF compatibility evaluator, and outcome/stop
calculations instead of maintaining a second strategy implementation.

The replay never writes prospective state or event tables.  Each temporal
partition starts with an independent baseline observation, so only later
False->True READY transitions become historical events.  Outcomes are kept
inside their partition and use future candles only after the READY candle.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable

import pandas as pd

from analysis import analyze_timeframe
from lower_tf_outcome_analyzer import (
    HORIZONS,
    STOP_LOSS_BPS,
    _build_exact_window,
    _compute_metrics,
    _compute_stop_metrics,
)
from lower_tf_shadow import EXPERIMENT_VERSION, TIMEFRAME_MS
from prospective_lower_tf_collector import (
    CLOSED_BARS,
    OHLCV_COLUMNS,
    _build_window_for_target,
    _validate_frame,
    evaluate_lower_tf_candidate,
)
from ready_outcome_pilot import SYMBOLS
from research_data import get_research_data
from scanner import exchange

REPLAY_VERSION = "lower-tf-historical-stop-v2-fixed200-costs"
SPLIT_NAMES = ("DEVELOPMENT", "HOLDOUT_A", "HOLDOUT_B")
VALID_DIRECTIONS = ("LONG", "SHORT")
MAX_HORIZON = max(HORIZONS)
DEFAULT_BARS = 1600
DEFAULT_FEE_BPS_PER_SIDE = 6.0
DEFAULT_SLIPPAGE_BPS_PER_SIDE = 2.0
DEFAULT_STARTING_BALANCE = 300.0
DEFAULT_RISK_PCT = 1.0
DATA_QUALITY_FAILURES = (
    "EXPECTED_200_BARS",
    "TOO_MANY_NO_TICK_SLOTS",
    "MISSING_PREVIOUS_CLOSE",
)


def _safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_NUMBER") from exc
    if not math.isfinite(number):
        raise ValueError("INVALID_NUMBER")
    return number


def _normalize_source(source: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError("INVALID_TIMEFRAME")
    if source is None or source.empty:
        raise ValueError("EMPTY_SOURCE")
    missing = [column for column in OHLCV_COLUMNS if column not in source.columns]
    if missing:
        raise ValueError(f"MISSING_COLUMNS:{','.join(missing)}")

    clean = source[OHLCV_COLUMNS].copy()
    clean["time"] = clean["time"].map(int)
    clean = clean.drop_duplicates(subset="time", keep="last").sort_values("time")
    tf_ms = TIMEFRAME_MS[timeframe]
    clean = clean[clean["time"].map(lambda value: value >= 0 and value % tf_ms == 0)]
    if clean.empty:
        raise ValueError("NO_ALIGNED_CANDLES")
    return clean.reset_index(drop=True)


def eligible_target_timestamps(source: pd.DataFrame, timeframe: str) -> list[int]:
    """Return the regular decision grid with enough fixed-200 history."""
    clean = _normalize_source(source, timeframe)
    tf_ms = TIMEFRAME_MS[timeframe]
    first = int(clean.iloc[0]["time"]) + (CLOSED_BARS - 1) * tf_ms
    last = int(clean.iloc[-1]["time"])
    if first > last:
        return []
    return list(range(first, last + tf_ms, tf_ms))


def build_temporal_splits(targets: Iterable[int]) -> list[dict[str, Any]]:
    """Divide ordered targets into development plus two untouched holdouts.

    The three partitions are chronological and non-overlapping.  A separate
    READY baseline is established inside each partition.  The caller already
    excludes targets without all 12 future candles, and replay_split also
    rejects any outcome that would cross the partition boundary.
    """
    ordered = sorted({int(value) for value in targets})
    minimum = len(SPLIT_NAMES) * (MAX_HORIZON + 2)
    if len(ordered) < minimum:
        raise ValueError("INSUFFICIENT_TARGETS_FOR_THREE_SPLITS")

    base, remainder = divmod(len(ordered), len(SPLIT_NAMES))
    sizes = [base + (1 if index < remainder else 0) for index in range(len(SPLIT_NAMES))]
    result = []
    cursor = 0
    for name, size in zip(SPLIT_NAMES, sizes):
        values = ordered[cursor:cursor + size]
        result.append({
            "name": name,
            "start_timestamp": values[0],
            "end_timestamp": values[-1],
            "target_count": len(values),
            "targets": values,
        })
        cursor += size
    return result


def _candidate_is_valid(candidate: dict[str, Any], target: int) -> bool:
    return (
        isinstance(candidate, dict)
        and type(candidate.get("ready")) is bool
        and int(candidate.get("ready_timestamp", -1)) == int(target)
    )


def _bounded_source(source: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    """Keep the requested range plus the last earlier candle for flat fills."""
    earlier = source[source["time"] < start].tail(1)
    bounded = source[(source["time"] >= start) & (source["time"] <= end)]
    return pd.concat([earlier, bounded], ignore_index=True)


def _event_outcomes(
    source: pd.DataFrame,
    timeframe: str,
    direction: str,
    target: int,
    entry_price: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    tf_ms = TIMEFRAME_MS[timeframe]
    bounded = _bounded_source(source, target, target + MAX_HORIZON * tf_ms)
    exact = _build_exact_window(bounded, timeframe, target, MAX_HORIZON)
    signal_price = _safe_float(exact.iloc[0]["close"])
    if not math.isclose(signal_price, entry_price, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("ENTRY_PRICE_PARITY_FAILURE")

    raw_rows = []
    stop_rows = []
    for horizon in HORIZONS:
        future = exact.iloc[1:horizon + 1]
        raw_rows.append({"horizon_bars": horizon, **_compute_metrics(direction, entry_price, future)})
        for stop_bps in STOP_LOSS_BPS:
            stop_rows.append({
                "horizon_bars": horizon,
                **_compute_stop_metrics(direction, entry_price, future, stop_bps),
            })
    fills = [int(value) for value in exact.attrs.get("filled_no_tick_timestamps", [])]
    return raw_rows, stop_rows, fills


def replay_split(
    source: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    split: dict[str, Any],
    analyze_fn: Callable[[pd.DataFrame], dict[str, Any]] = analyze_timeframe,
    evaluate_fn: Callable[[str, str, str, dict[str, Any]], dict[str, Any]] = evaluate_lower_tf_candidate,
) -> dict[str, Any]:
    """Replay one independent partition with prospective baseline semantics."""
    clean = _normalize_source(source, timeframe)
    tf_ms = TIMEFRAME_MS[timeframe]
    split_end = int(split["end_timestamp"])
    state: dict[str, bool | None] = {direction: None for direction in VALID_DIRECTIONS}
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    evaluated = 0

    for target_value in split["targets"]:
        target = int(target_value)
        # Keeping the full 12-bar outcome inside the partition prevents
        # information from one holdout being scored in the next holdout.
        if target + MAX_HORIZON * tf_ms > split_end:
            continue
        try:
            decision_start = target - (CLOSED_BARS - 1) * tf_ms
            decision_source = _bounded_source(clean, decision_start, target)
            decision_window = _build_window_for_target(decision_source, timeframe, target)
            _validate_frame(decision_window, timeframe, symbol, target_timestamp=target)
            result = analyze_fn(decision_window)
            if not isinstance(result, dict) or result.get("trend") == "ERROR":
                raise ValueError("PIPELINE_ERROR")
            evaluated += 1

            for direction in VALID_DIRECTIONS:
                candidate = evaluate_fn(symbol, timeframe, direction, result)
                if not _candidate_is_valid(candidate, target):
                    raise ValueError(f"INVALID_{direction}_CANDIDATE")
                ready = candidate["ready"]
                previous = state[direction]
                state[direction] = ready

                # The first observation in every partition seeds a baseline,
                # exactly like lower_tf_storage.observe().
                if previous is None or not ready or previous:
                    continue

                entry_price = _safe_float(decision_window.iloc[-2]["close"])
                raw_rows, stop_rows, outcome_fills = _event_outcomes(
                    clean, timeframe, direction, target, entry_price,
                )
                events.append({
                    "replay_version": REPLAY_VERSION,
                    "experiment_version": EXPERIMENT_VERSION,
                    "split": split["name"],
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": direction,
                    "ready_timestamp": target,
                    "entry_reference_price": entry_price,
                    "decision_no_tick_fills": [
                        int(value)
                        for value in decision_window.attrs.get("filled_no_tick_timestamps", [])
                    ],
                    "outcome_no_tick_fills": outcome_fills,
                    "candidate": candidate,
                    "raw_outcomes": raw_rows,
                    "stop_outcomes": stop_rows,
                })
        except Exception as exc:
            detail = str(exc)[:160]
            row = {
                "target_timestamp": target,
                "error": type(exc).__name__,
                "detail": detail,
            }
            if any(code in detail for code in DATA_QUALITY_FAILURES):
                exclusions.append(row)
            else:
                errors.append(row)

    return {
        "split": {key: value for key, value in split.items() if key != "targets"},
        "evaluated_targets": evaluated,
        "events": events,
        "exclusions": exclusions,
        "errors": errors,
    }


def replay_symbol(
    source: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    analyze_fn: Callable[[pd.DataFrame], dict[str, Any]] = analyze_timeframe,
    evaluate_fn: Callable[[str, str, str, dict[str, Any]], dict[str, Any]] = evaluate_lower_tf_candidate,
) -> dict[str, Any]:
    clean = _normalize_source(source, timeframe)
    targets = eligible_target_timestamps(clean, timeframe)
    splits = build_temporal_splits(targets)
    split_results = [
        replay_split(
            clean,
            symbol=symbol,
            timeframe=timeframe,
            split=split,
            analyze_fn=analyze_fn,
            evaluate_fn=evaluate_fn,
        )
        for split in splits
    ]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source_first_timestamp": int(clean.iloc[0]["time"]),
        "source_last_timestamp": int(clean.iloc[-1]["time"]),
        "source_rows": len(clean),
        "splits": split_results,
    }


def _execution_metrics(
    gross_return_pct: float,
    stop_loss_bps: int,
    *,
    fee_bps_per_side: float,
    slippage_bps_per_side: float,
    starting_balance: float,
    risk_pct: float,
) -> dict[str, float]:
    """Convert a gross trade outcome to net percent, R, and isolated PnL.

    The PnL is an expectancy aid for one independently sized trade. It is not
    an equity curve: simultaneous signals and capital contention are left for
    a later chronological portfolio simulation.
    """
    values = (
        fee_bps_per_side,
        slippage_bps_per_side,
        starting_balance,
        risk_pct,
    )
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("INVALID_EXECUTION_ASSUMPTIONS")
    if fee_bps_per_side < 0 or slippage_bps_per_side < 0:
        raise ValueError("INVALID_EXECUTION_ASSUMPTIONS")
    if starting_balance <= 0 or risk_pct <= 0 or risk_pct > 100:
        raise ValueError("INVALID_EXECUTION_ASSUMPTIONS")

    stop_loss_pct = int(stop_loss_bps) / 100.0
    round_trip_cost_pct = 2.0 * (fee_bps_per_side + slippage_bps_per_side) / 100.0
    net_return_pct = float(gross_return_pct) - round_trip_cost_pct
    return_r = net_return_pct / stop_loss_pct
    starting_risk_amount = starting_balance * risk_pct / 100.0
    return {
        "round_trip_cost_pct": round_trip_cost_pct,
        "net_return_pct": net_return_pct,
        "return_r": return_r,
        "isolated_pnl_at_starting_risk": starting_risk_amount * return_r,
    }


def aggregate_events(
    events: Iterable[dict[str, Any]],
    *,
    fee_bps_per_side: float = DEFAULT_FEE_BPS_PER_SIDE,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_pct: float = DEFAULT_RISK_PCT,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        raw_by_horizon = {row["horizon_bars"]: row for row in event["raw_outcomes"]}
        for stop in event["stop_outcomes"]:
            key = (
                event["split"],
                event["timeframe"],
                event["direction"],
                int(stop["horizon_bars"]),
                int(stop["stop_loss_bps"]),
            )
            grouped[key].append({"stop": stop, "raw": raw_by_horizon[stop["horizon_bars"]]})

    rows = []
    for key in sorted(grouped):
        split, timeframe, direction, horizon, stop_bps = key
        values = grouped[key]
        adjusted = [float(value["stop"]["stop_adjusted_return_pct"]) for value in values]
        executed = [
            _execution_metrics(
                gross_return,
                stop_bps,
                fee_bps_per_side=fee_bps_per_side,
                slippage_bps_per_side=slippage_bps_per_side,
                starting_balance=starting_balance,
                risk_pct=risk_pct,
            )
            for gross_return in adjusted
        ]
        net_returns = [value["net_return_pct"] for value in executed]
        returns_r = [value["return_r"] for value in executed]
        isolated_pnl = [value["isolated_pnl_at_starting_risk"] for value in executed]
        raw_returns = [float(value["raw"]["close_return_pct"]) for value in values]
        stop_hits = sum(value["stop"]["stop_hit"] is True for value in values)
        rows.append({
            "split": split,
            "timeframe": timeframe,
            "direction": direction,
            "horizon_bars": horizon,
            "stop_loss_bps": stop_bps,
            "n": len(values),
            "stop_hits": stop_hits,
            "stop_hit_rate_pct": stop_hits / len(values) * 100.0,
            "avg_raw_close_return_pct": sum(raw_returns) / len(raw_returns),
            "raw_positive_rate_pct": sum(value > 0 for value in raw_returns) / len(raw_returns) * 100.0,
            "avg_stop_adjusted_return_pct": sum(adjusted) / len(adjusted),
            "median_stop_adjusted_return_pct": median(adjusted),
            "stop_adjusted_positive_rate_pct": sum(value > 0 for value in adjusted) / len(adjusted) * 100.0,
            "round_trip_cost_pct": executed[0]["round_trip_cost_pct"],
            "avg_net_return_pct": sum(net_returns) / len(net_returns),
            "median_net_return_pct": median(net_returns),
            "net_positive_rate_pct": sum(value > 0 for value in net_returns) / len(net_returns) * 100.0,
            "avg_return_r": sum(returns_r) / len(returns_r),
            "avg_isolated_pnl_at_starting_risk": sum(isolated_pnl) / len(isolated_pnl),
        })
    return rows


def build_report(
    results: list[dict[str, Any]],
    *,
    fee_bps_per_side: float = DEFAULT_FEE_BPS_PER_SIDE,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_pct: float = DEFAULT_RISK_PCT,
) -> dict[str, Any]:
    events = [
        event
        for result in results
        for split in result["splits"]
        for event in split["events"]
    ]
    errors = [
        {"symbol": result["symbol"], "timeframe": result["timeframe"], "split": split["split"]["name"], **error}
        for result in results
        for split in result["splits"]
        for error in split["errors"]
    ]
    exclusions = [
        {"symbol": result["symbol"], "timeframe": result["timeframe"], "split": split["split"]["name"], **row}
        for result in results
        for split in result["splits"]
        for row in split["exclusions"]
    ]
    return {
        "manifest": {
            "replay_version": REPLAY_VERSION,
            "experiment_version": EXPERIMENT_VERSION,
            "fixed_window_bars": CLOSED_BARS + 1,
            "signal_candle_index": -2,
            "transition_policy": "independent_split_baseline_then_false_to_true_only",
            "split_order": list(SPLIT_NAMES),
            "outcome_partition_policy": "all_12_future_bars_must_remain_inside_split",
            "horizons": list(HORIZONS),
            "stop_loss_bps": list(STOP_LOSS_BPS),
            "execution_assumptions": {
                "fee_bps_per_side": fee_bps_per_side,
                "slippage_bps_per_side": slippage_bps_per_side,
                "round_trip_cost_pct": 2.0 * (fee_bps_per_side + slippage_bps_per_side) / 100.0,
                "funding_included": False,
                "starting_balance": starting_balance,
                "risk_pct_per_isolated_trade": risk_pct,
                "portfolio_equity_curve_included": False,
            },
        },
        "sources": [
            {key: value for key, value in result.items() if key != "splits"}
            for result in results
        ],
        "split_diagnostics": [
            {
                "symbol": result["symbol"],
                "timeframe": result["timeframe"],
                **split["split"],
                "evaluated_targets": split["evaluated_targets"],
                "event_count": len(split["events"]),
                "exclusion_count": len(split["exclusions"]),
                "error_count": len(split["errors"]),
            }
            for result in results
            for split in result["splits"]
        ],
        "summary": aggregate_events(
            events,
            fee_bps_per_side=fee_bps_per_side,
            slippage_bps_per_side=slippage_bps_per_side,
            starting_balance=starting_balance,
            risk_pct=risk_pct,
        ),
        "events": events,
        "data_quality_exclusions": exclusions,
        "errors": errors,
    }


def _parse_symbols(value: str) -> list[str]:
    if value == "all":
        return list(SYMBOLS)
    requested = [item.strip().upper() for item in value.split(",") if item.strip()]
    unknown = [symbol for symbol in requested if symbol not in SYMBOLS]
    if unknown:
        raise ValueError(f"UNKNOWN_SYMBOLS:{','.join(unknown)}")
    if not requested:
        raise ValueError("EMPTY_SYMBOL_SET")
    return requested


def _print_summary(report: dict[str, Any]) -> None:
    print("Replay:", report["manifest"]["replay_version"])
    print("Prospective contract:", report["manifest"]["experiment_version"])
    print("Events:", len(report["events"]))
    print("Data-quality exclusions:", len(report["data_quality_exclusions"]))
    print("Errors:", len(report["errors"]))
    for row in report["summary"]:
        print(
            f"{row['split']} {row['timeframe']} {row['direction']} "
            f"h={row['horizon_bars']} stop={row['stop_loss_bps']}bp "
            f"N={row['n']} hit={row['stop_hit_rate_pct']:.1f}% "
            f"gross={row['avg_stop_adjusted_return_pct']:+.3f}% "
            f"net={row['avg_net_return_pct']:+.3f}% "
            f"R={row['avg_return_r']:+.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", required=True, choices=tuple(TIMEFRAME_MS))
    parser.add_argument("--symbols", default="all", help="Comma-separated research symbols or 'all'")
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--fee-bps-per-side", type=float, default=DEFAULT_FEE_BPS_PER_SIDE)
    parser.add_argument("--slippage-bps-per-side", type=float, default=DEFAULT_SLIPPAGE_BPS_PER_SIDE)
    parser.add_argument("--starting-balance", type=float, default=DEFAULT_STARTING_BALANCE)
    parser.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    minimum = CLOSED_BARS + len(SPLIT_NAMES) * (MAX_HORIZON + 2)
    if args.bars < minimum:
        raise SystemExit(f"--bars must be at least {minimum}")
    symbols = _parse_symbols(args.symbols)
    latest_closed = (int(exchange.milliseconds()) // TIMEFRAME_MS[args.timeframe]) * TIMEFRAME_MS[args.timeframe] - TIMEFRAME_MS[args.timeframe]

    results = []
    for symbol in symbols:
        print(f"Fetching {symbol} {args.timeframe} ({args.bars} bars)", flush=True)
        source = get_research_data(symbol, args.timeframe, total_limit=args.bars)
        source = source[source["time"].map(int) <= latest_closed]
        result = replay_symbol(source, symbol=symbol, timeframe=args.timeframe)
        results.append(result)
        event_count = sum(len(split["events"]) for split in result["splits"])
        exclusion_count = sum(len(split["exclusions"]) for split in result["splits"])
        error_count = sum(len(split["errors"]) for split in result["splits"])
        print(
            f"Completed {symbol}: events={event_count} "
            f"excluded={exclusion_count} errors={error_count}",
            flush=True,
        )

    report = build_report(
        results,
        fee_bps_per_side=args.fee_bps_per_side,
        slippage_bps_per_side=args.slippage_bps_per_side,
        starting_balance=args.starting_balance,
        risk_pct=args.risk_pct,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    _print_summary(report)
    if report["errors"]:
        raise SystemExit("FAIL: historical replay contained evaluation errors")
    print("STRICT HISTORICAL LOWER-TF REPLAY: PASS")


if __name__ == "__main__":
    main()
