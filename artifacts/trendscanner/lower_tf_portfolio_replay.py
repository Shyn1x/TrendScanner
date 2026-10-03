"""Chronological portfolio replay for the validated 1h LONG candidates.

The input is one or more strict historical lower-TF replay reports.  This
module does not recompute READY signals.  It consumes their auditable event
rows, sizes every position from then-current realised balance, settles exits
before new entries at the same timestamp, and enforces explicit portfolio
risk, concurrency, duplicate-symbol, and leverage limits.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from lower_tf_historical_stop_replay import (
    DEFAULT_FEE_BPS_PER_SIDE,
    DEFAULT_RISK_PCT,
    DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    DEFAULT_STARTING_BALANCE,
    REPLAY_VERSION,
    SPLIT_NAMES,
    _execution_metrics,
)
from lower_tf_shadow import TIMEFRAME_MS

PORTFOLIO_REPLAY_VERSION = "lower-tf-portfolio-v1-1h-long-12bar"
PORTFOLIO_TIMEFRAME = "1h"
PORTFOLIO_DIRECTION = "LONG"
PORTFOLIO_HORIZON_BARS = 12
PORTFOLIO_STOP_LOSS_BPS = (100, 150)
DEFAULT_MAX_TOTAL_RISK_PCT = 3.0
DEFAULT_MAX_OPEN_POSITIONS = 3
DEFAULT_MAX_LEVERAGE = 3.0


def _finite_positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"INVALID_{label}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"INVALID_{label}")
    return number


def _stop_outcome(event: dict[str, Any], stop_loss_bps: int) -> dict[str, Any]:
    matches = [
        row
        for row in event.get("stop_outcomes", [])
        if int(row.get("horizon_bars", -1)) == PORTFOLIO_HORIZON_BARS
        and int(row.get("stop_loss_bps", -1)) == int(stop_loss_bps)
    ]
    if len(matches) != 1:
        raise ValueError("MISSING_OR_DUPLICATE_STOP_OUTCOME")
    row = matches[0]
    stop_hit = row.get("stop_hit")
    bars_to_stop = row.get("bars_to_stop")
    if type(stop_hit) is not bool:
        raise ValueError("INVALID_STOP_HIT")
    if stop_hit:
        if isinstance(bars_to_stop, bool) or int(bars_to_stop) not in range(1, PORTFOLIO_HORIZON_BARS + 1):
            raise ValueError("INVALID_BARS_TO_STOP")
        exit_bars = int(bars_to_stop)
    else:
        if bars_to_stop is not None:
            raise ValueError("INVALID_BARS_TO_STOP")
        exit_bars = PORTFOLIO_HORIZON_BARS
    return {**row, "exit_bars": exit_bars}


def _prepare_events(
    events: Iterable[dict[str, Any]],
    *,
    stop_loss_bps: int,
    fee_bps_per_side: float,
    slippage_bps_per_side: float,
) -> list[dict[str, Any]]:
    if int(stop_loss_bps) not in PORTFOLIO_STOP_LOSS_BPS:
        raise ValueError("UNSUPPORTED_STOP_LOSS")
    prepared = []
    seen = set()
    for event in events:
        if event.get("timeframe") != PORTFOLIO_TIMEFRAME or event.get("direction") != PORTFOLIO_DIRECTION:
            continue
        split = event.get("split")
        symbol = event.get("symbol")
        timestamp = event.get("ready_timestamp")
        if split not in SPLIT_NAMES or not isinstance(symbol, str) or not symbol:
            raise ValueError("INVALID_EVENT_IDENTITY")
        if isinstance(timestamp, bool):
            raise ValueError("INVALID_EVENT_TIMESTAMP")
        timestamp = int(timestamp)
        if timestamp < 0 or timestamp % TIMEFRAME_MS[PORTFOLIO_TIMEFRAME]:
            raise ValueError("INVALID_EVENT_TIMESTAMP")
        identity = (split, symbol, PORTFOLIO_TIMEFRAME, PORTFOLIO_DIRECTION, timestamp)
        if identity in seen:
            raise ValueError("DUPLICATE_EVENT")
        seen.add(identity)

        outcome = _stop_outcome(event, stop_loss_bps)
        gross_return_pct = float(outcome["stop_adjusted_return_pct"])
        execution = _execution_metrics(
            gross_return_pct,
            stop_loss_bps,
            fee_bps_per_side=fee_bps_per_side,
            slippage_bps_per_side=slippage_bps_per_side,
            starting_balance=1.0,
            risk_pct=1.0,
        )
        prepared.append({
            "split": split,
            "symbol": symbol,
            "ready_timestamp": timestamp,
            "exit_timestamp": timestamp + outcome["exit_bars"] * TIMEFRAME_MS[PORTFOLIO_TIMEFRAME],
            "stop_hit": outcome["stop_hit"],
            "bars_to_stop": outcome["bars_to_stop"],
            "gross_return_pct": gross_return_pct,
            "net_return_pct": execution["net_return_pct"],
            "return_r": execution["return_r"],
        })
    return sorted(prepared, key=lambda row: (row["ready_timestamp"], row["symbol"]))


def simulate_portfolio(
    events: Iterable[dict[str, Any]],
    *,
    stop_loss_bps: int,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_pct: float = DEFAULT_RISK_PCT,
    max_total_risk_pct: float = DEFAULT_MAX_TOTAL_RISK_PCT,
    max_open_positions: int = DEFAULT_MAX_OPEN_POSITIONS,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
    fee_bps_per_side: float = DEFAULT_FEE_BPS_PER_SIDE,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
) -> dict[str, Any]:
    """Run one realised-balance portfolio simulation.

    PnL is fixed at entry from that entry's risk amount.  Positions exiting at
    the same timestamp settle as a batch, avoiding arbitrary symbol-order
    effects on the equity curve.  This is a futures-style simulation: notional
    exposure may exceed balance but never the explicit leverage cap.
    """
    balance = _finite_positive(starting_balance, "STARTING_BALANCE")
    risk_pct = _finite_positive(risk_pct, "RISK_PCT")
    max_total_risk_pct = _finite_positive(max_total_risk_pct, "MAX_TOTAL_RISK_PCT")
    max_leverage = _finite_positive(max_leverage, "MAX_LEVERAGE")
    if isinstance(max_open_positions, bool) or int(max_open_positions) <= 0:
        raise ValueError("INVALID_MAX_OPEN_POSITIONS")
    max_open_positions = int(max_open_positions)
    if risk_pct > max_total_risk_pct or max_total_risk_pct > 100:
        raise ValueError("INVALID_RISK_LIMITS")
    if fee_bps_per_side < 0 or slippage_bps_per_side < 0:
        raise ValueError("INVALID_EXECUTION_ASSUMPTIONS")

    ordered = _prepare_events(
        events,
        stop_loss_bps=stop_loss_bps,
        fee_bps_per_side=fee_bps_per_side,
        slippage_bps_per_side=slippage_bps_per_side,
    )
    open_positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    skipped = defaultdict(int)
    equity_curve = [{"timestamp": None, "balance": balance, "realised_pnl": 0.0}]
    peak = balance
    max_drawdown_pct = 0.0
    max_concurrent_positions = 0
    max_notional_to_balance = 0.0

    def settle_through(timestamp: int | None) -> None:
        nonlocal balance, peak, max_drawdown_pct, open_positions
        eligible = [
            position for position in open_positions
            if timestamp is None or position["exit_timestamp"] <= timestamp
        ]
        for exit_timestamp in sorted({position["exit_timestamp"] for position in eligible}):
            closing = [position for position in open_positions if position["exit_timestamp"] == exit_timestamp]
            if not closing:
                continue
            batch_pnl = sum(position["pnl"] for position in closing)
            balance += batch_pnl
            for position in closing:
                position["exit_balance_after_batch"] = balance
                trades.append(position)
            open_positions = [position for position in open_positions if position["exit_timestamp"] != exit_timestamp]
            peak = max(peak, balance)
            drawdown_pct = (peak - balance) / peak * 100.0
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
            equity_curve.append({
                "timestamp": exit_timestamp,
                "balance": balance,
                "realised_pnl": batch_pnl,
            })

    stop_fraction = int(stop_loss_bps) / 10_000.0
    for event in ordered:
        settle_through(event["ready_timestamp"])
        if balance <= 0:
            skipped["insolvent"] += 1
            continue
        if any(position["symbol"] == event["symbol"] for position in open_positions):
            skipped["duplicate_symbol"] += 1
            continue
        if len(open_positions) >= max_open_positions:
            skipped["max_open_positions"] += 1
            continue

        risk_amount = balance * risk_pct / 100.0
        reserved_risk = sum(position["risk_amount"] for position in open_positions)
        if reserved_risk + risk_amount > balance * max_total_risk_pct / 100.0 + 1e-12:
            skipped["max_total_risk"] += 1
            continue
        notional = risk_amount / stop_fraction
        open_notional = sum(position["notional"] for position in open_positions)
        if open_notional + notional > balance * max_leverage + 1e-12:
            skipped["max_leverage"] += 1
            continue

        pnl = risk_amount * event["return_r"]
        open_positions.append({
            **event,
            "entry_balance": balance,
            "risk_amount": risk_amount,
            "notional": notional,
            "pnl": pnl,
        })
        max_concurrent_positions = max(max_concurrent_positions, len(open_positions))
        max_notional_to_balance = max(
            max_notional_to_balance,
            sum(position["notional"] for position in open_positions) / balance,
        )

    settle_through(None)
    pnls = [trade["pnl"] for trade in trades]
    returns_r = [trade["return_r"] for trade in trades]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    total_pnl = balance - starting_balance
    return {
        "stop_loss_bps": int(stop_loss_bps),
        "starting_balance": float(starting_balance),
        "final_balance": balance,
        "total_pnl": total_pnl,
        "total_return_pct": total_pnl / starting_balance * 100.0,
        "realised_balance_max_drawdown_pct": max_drawdown_pct,
        "candidate_events": len(ordered),
        "entered_trades": len(trades),
        "skipped_events": dict(sorted(skipped.items())),
        "win_rate_pct": (sum(value > 0 for value in pnls) / len(pnls) * 100.0) if pnls else 0.0,
        "average_return_r": (sum(returns_r) / len(returns_r)) if returns_r else 0.0,
        "median_return_r": median(returns_r) if returns_r else 0.0,
        "profit_factor": (gains / losses) if losses else None,
        "max_concurrent_positions": max_concurrent_positions,
        "max_notional_to_balance": max_notional_to_balance,
        "ending_open_positions": len(open_positions),
        "trades": trades,
        "equity_curve": equity_curve,
    }


def load_reports(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reports = []
    events = []
    for path in sorted({Path(value) for value in paths}):
        report = json.loads(path.read_text())
        manifest = report.get("manifest", {})
        if manifest.get("replay_version") != REPLAY_VERSION:
            raise ValueError(f"WRONG_REPLAY_VERSION:{path}")
        if report.get("errors"):
            raise ValueError(f"SOURCE_REPORT_HAS_ERRORS:{path}")
        reports.append(report)
        events.extend(report.get("events", []))
    if not reports:
        raise ValueError("NO_INPUT_REPORTS")
    return reports, events


def build_portfolio_report(
    reports: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_pct: float = DEFAULT_RISK_PCT,
    max_total_risk_pct: float = DEFAULT_MAX_TOTAL_RISK_PCT,
    max_open_positions: int = DEFAULT_MAX_OPEN_POSITIONS,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
    fee_bps_per_side: float = DEFAULT_FEE_BPS_PER_SIDE,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
) -> dict[str, Any]:
    assumptions = {
        "starting_balance": starting_balance,
        "risk_pct_per_trade": risk_pct,
        "max_total_open_risk_pct": max_total_risk_pct,
        "max_open_positions": max_open_positions,
        "max_leverage": max_leverage,
        "fee_bps_per_side": fee_bps_per_side,
        "slippage_bps_per_side": slippage_bps_per_side,
        "funding_included": False,
        "mark_to_market_drawdown_included": False,
        "same_timestamp_entry_order": "symbol_ascending",
        "same_timestamp_exit_policy": "settle_as_one_batch_before_entries",
    }
    simulations = []
    for stop_loss_bps in PORTFOLIO_STOP_LOSS_BPS:
        for split in (*SPLIT_NAMES, "COMBINED"):
            selected = events if split == "COMBINED" else [event for event in events if event.get("split") == split]
            simulations.append({
                "scope": split,
                **simulate_portfolio(
                    selected,
                    stop_loss_bps=stop_loss_bps,
                    starting_balance=starting_balance,
                    risk_pct=risk_pct,
                    max_total_risk_pct=max_total_risk_pct,
                    max_open_positions=max_open_positions,
                    max_leverage=max_leverage,
                    fee_bps_per_side=fee_bps_per_side,
                    slippage_bps_per_side=slippage_bps_per_side,
                ),
            })
    return {
        "manifest": {
            "portfolio_replay_version": PORTFOLIO_REPLAY_VERSION,
            "source_replay_version": REPLAY_VERSION,
            "timeframe": PORTFOLIO_TIMEFRAME,
            "direction": PORTFOLIO_DIRECTION,
            "horizon_bars": PORTFOLIO_HORIZON_BARS,
            "stop_loss_bps": list(PORTFOLIO_STOP_LOSS_BPS),
            "assumptions": assumptions,
            "source_report_count": len(reports),
            "source_event_count": len(events),
        },
        "simulations": simulations,
    }


def _print_summary(report: dict[str, Any]) -> None:
    print("Portfolio replay:", report["manifest"]["portfolio_replay_version"])
    print("Source reports:", report["manifest"]["source_report_count"])
    print("Source events:", report["manifest"]["source_event_count"])
    for row in report["simulations"]:
        print(
            f"{row['scope']} stop={row['stop_loss_bps']}bp "
            f"candidates={row['candidate_events']} entered={row['entered_trades']} "
            f"final=${row['final_balance']:.2f} return={row['total_return_pct']:+.2f}% "
            f"dd={row['realised_balance_max_drawdown_pct']:.2f}% "
            f"win={row['win_rate_pct']:.1f}% avgR={row['average_return_r']:+.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-glob", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--starting-balance", type=float, default=DEFAULT_STARTING_BALANCE)
    parser.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT)
    parser.add_argument("--max-total-risk-pct", type=float, default=DEFAULT_MAX_TOTAL_RISK_PCT)
    parser.add_argument("--max-open-positions", type=int, default=DEFAULT_MAX_OPEN_POSITIONS)
    parser.add_argument("--max-leverage", type=float, default=DEFAULT_MAX_LEVERAGE)
    parser.add_argument("--fee-bps-per-side", type=float, default=DEFAULT_FEE_BPS_PER_SIDE)
    parser.add_argument("--slippage-bps-per-side", type=float, default=DEFAULT_SLIPPAGE_BPS_PER_SIDE)
    args = parser.parse_args()

    paths = [Path(value) for value in glob.glob(args.input_glob)]
    reports, events = load_reports(paths)
    report = build_portfolio_report(
        reports,
        events,
        starting_balance=args.starting_balance,
        risk_pct=args.risk_pct,
        max_total_risk_pct=args.max_total_risk_pct,
        max_open_positions=args.max_open_positions,
        max_leverage=args.max_leverage,
        fee_bps_per_side=args.fee_bps_per_side,
        slippage_bps_per_side=args.slippage_bps_per_side,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    _print_summary(report)
    print("LOWER-TF PORTFOLIO REPLAY: PASS")


if __name__ == "__main__":
    main()
