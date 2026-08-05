"""
ready_take_pilot.py
~~~~~~~~~~~~~~~~~~~
Research-only comparison of READY -> TAKE breakout loss policies.

This module does not change production logic. It only orchestrates:
- scanner.get_data
- historical_replay.replay_timeframe
- ready_take_replay.validate_ready_to_take
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from historical_replay import replay_timeframe
from ready_take_replay import validate_ready_to_take
from scanner import get_data


SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "AVAX/USDT",
    "PENDLE/USDT",
]

TIMEFRAMES = [
    "4h",
]

POLICIES = [
    "cancel",
    "observe",
]

WARMUP_BARS = 120
MAX_REPLAY_BARS = 120
MAX_WAIT_STEPS = 3

OUTPUT_CSV = Path(__file__).with_name("ready_take_results.csv")
OUTPUT_JSON = Path(__file__).with_name("ready_take_results.json")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def _fmt_wait(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}"


def _fmt_metric(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)


def _status_row(
    *,
    symbol: str,
    timeframe: str,
    policy: str,
    status: str,
    error: str = "",
    replay_meta: dict | None = None,
    validation: dict | None = None,
) -> dict[str, Any]:
    summary = validation.get("summary", {}) if isinstance(validation, dict) else {}
    events = validation.get("events", []) if isinstance(validation, dict) else []

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "policy": policy,
        "status": status,
        "error": error,
        "ready_events": _safe_int(summary.get("ready_events"), 0) if status == "OK" else None,
        "entry_confirmed": _safe_int(summary.get("entry_confirmed"), 0) if status == "OK" else None,
        "cancelled": _safe_int(summary.get("cancelled"), 0) if status == "OK" else None,
        "expired": _safe_int(summary.get("expired"), 0) if status == "OK" else None,
        "conversion_rate": _safe_float(summary.get("conversion_rate"), None) if status == "OK" else None,
        "average_wait_steps": _safe_float(summary.get("average_wait_steps"), None) if status == "OK" else None,
        "replay_meta": replay_meta if isinstance(replay_meta, dict) else {},
        "summary": summary if isinstance(summary, dict) else {},
        "events": events if isinstance(events, list) else [],
    }


def _run_one(symbol: str, timeframe: str, policy: str) -> dict[str, Any]:
    try:
        df = get_data(symbol, timeframe)
        replay = replay_timeframe(
            df,
            symbol,
            timeframe,
            warmup_bars=WARMUP_BARS,
            max_replay_bars=MAX_REPLAY_BARS,
        )
        replay_results = replay.get("replay_results", []) if isinstance(replay, dict) else []
        validation = validate_ready_to_take(
            replay_results,
            max_wait_steps=MAX_WAIT_STEPS,
            breakout_loss_policy=policy,
        )
        replay_meta = replay.get("meta", {}) if isinstance(replay, dict) else {}
        return _status_row(
            symbol=symbol,
            timeframe=timeframe,
            policy=policy,
            status="OK",
            replay_meta=replay_meta,
            validation=validation,
        )
    except Exception as exc:
        return _status_row(
            symbol=symbol,
            timeframe=timeframe,
            policy=policy,
            status="FAILED",
            error=f"{type(exc).__name__}: {exc}",
        )


def _collect_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            for policy in POLICIES:
                rows.append(_run_one(symbol, timeframe, policy))
    return rows


def _aggregate_policy(rows: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    policy_rows = [row for row in rows if row.get("policy") == policy]
    ok_rows = [row for row in policy_rows if row.get("status") == "OK"]

    ready = sum(_safe_int(row.get("ready_events"), 0) for row in ok_rows)
    entry = sum(_safe_int(row.get("entry_confirmed"), 0) for row in ok_rows)
    cancel = sum(_safe_int(row.get("cancelled"), 0) for row in ok_rows)
    expired = sum(_safe_int(row.get("expired"), 0) for row in ok_rows)

    wait_values = [
        _safe_float(row.get("average_wait_steps"), None)
        for row in ok_rows
        if _safe_float(row.get("average_wait_steps"), None) is not None
    ]

    return {
        "policy": policy,
        "rows": len(policy_rows),
        "ok_rows": len(ok_rows),
        "failed_rows": len(policy_rows) - len(ok_rows),
        "ready_events": ready,
        "entry_confirmed": entry,
        "cancelled": cancel,
        "expired": expired,
        "conversion_rate": (entry / ready) if ready else 0.0,
        "average_wait_steps": (sum(wait_values) / len(wait_values)) if wait_values else None,
    }


def _policy_delta(aggregates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    observe = aggregates.get("observe", {})
    cancel = aggregates.get("cancel", {})

    observe_conv = _safe_float(observe.get("conversion_rate"), 0.0) or 0.0
    cancel_conv = _safe_float(cancel.get("conversion_rate"), 0.0) or 0.0
    observe_wait = _safe_float(observe.get("average_wait_steps"), None)
    cancel_wait = _safe_float(cancel.get("average_wait_steps"), None)

    wait_delta = None
    if observe_wait is not None and cancel_wait is not None:
        wait_delta = observe_wait - cancel_wait

    return {
        "entry_confirmed": _safe_int(observe.get("entry_confirmed"), 0) - _safe_int(cancel.get("entry_confirmed"), 0),
        "cancelled": _safe_int(observe.get("cancelled"), 0) - _safe_int(cancel.get("cancelled"), 0),
        "expired": _safe_int(observe.get("expired"), 0) - _safe_int(cancel.get("expired"), 0),
        "conversion_rate": observe_conv - cancel_conv,
        "average_wait_steps": wait_delta,
    }


def _table_lines(rows: list[dict[str, Any]]) -> list[str]:
    headers = [
        "Symbol",
        "TF",
        "Policy",
        "Status",
        "READY",
        "ENTRY",
        "CANCEL",
        "EXP",
        "CONV",
        "WAIT",
    ]

    body: list[list[str]] = []
    for row in rows:
        body.append([
            str(row.get("symbol", "")),
            str(row.get("timeframe", "")),
            str(row.get("policy", "")),
            str(row.get("status", "")),
            _fmt_metric(row.get("ready_events")),
            _fmt_metric(row.get("entry_confirmed")),
            _fmt_metric(row.get("cancelled")),
            _fmt_metric(row.get("expired")),
            _pct(_safe_float(row.get("conversion_rate"), None)),
            _fmt_wait(_safe_float(row.get("average_wait_steps"), None)),
        ])

    widths = [len(header) for header in headers]
    for line in body:
        for index, value in enumerate(line):
            widths[index] = max(widths[index], len(value))

    def render(values: list[str]) -> str:
        parts = []
        for index, value in enumerate(values):
            align = value.rjust(widths[index]) if index >= 4 else value.ljust(widths[index])
            parts.append(align)
        return "  ".join(parts)

    total_width = sum(widths) + (2 * (len(widths) - 1))
    lines = ["-" * total_width, render(headers)]
    for line in body:
        lines.append(render(line))
    lines.append("-" * total_width)
    return lines


def _aggregate_lines(aggregates: dict[str, dict[str, Any]], delta: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for policy in POLICIES:
        agg = aggregates[policy]
        lines.append("=" * 50)
        lines.append(f"TOTAL {policy}")
        lines.append(f"READY:         {agg['ready_events']}")
        lines.append(f"ENTRY:         {agg['entry_confirmed']}")
        lines.append(f"CANCEL:        {agg['cancelled']}")
        lines.append(f"EXPIRED:       {agg['expired']}")
        lines.append(f"Conversion:    {_pct(_safe_float(agg.get('conversion_rate'), 0.0))}")
        lines.append(f"Average Wait:  {_fmt_wait(_safe_float(agg.get('average_wait_steps'), None))}")
        lines.append(f"Failed Rows:   {agg['failed_rows']}")
    lines.append("=" * 50)
    lines.append("observe vs cancel")
    lines.append(f"ENTRY {delta['entry_confirmed']:+d}")
    lines.append(f"CANCEL {delta['cancelled']:+d}")
    lines.append(f"EXPIRED {delta['expired']:+d}")
    lines.append(f"Conversion {delta['conversion_rate'] * 100:+.1f}%")
    wait_delta = _safe_float(delta.get("average_wait_steps"), None)
    if wait_delta is not None:
        lines.append(f"Average Wait {wait_delta:+.1f}")
    else:
        lines.append("Average Wait -")
    lines.append("=" * 50)
    return lines


def _write_csv(rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "symbol",
        "timeframe",
        "policy",
        "status",
        "error",
        "ready_events",
        "entry_confirmed",
        "cancelled",
        "expired",
        "conversion_rate",
        "average_wait_steps",
    ]
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def _write_json(
    rows: list[dict[str, Any]],
    aggregates: dict[str, dict[str, Any]],
    delta: dict[str, Any],
) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "policies": POLICIES,
            "warmup_bars": WARMUP_BARS,
            "max_replay_bars": MAX_REPLAY_BARS,
            "max_wait_steps": MAX_WAIT_STEPS,
        },
        "aggregates": aggregates,
        "delta": delta,
        "results": rows,
    }
    with OUTPUT_JSON.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def main() -> int:
    rows = _collect_rows()
    aggregates = {policy: _aggregate_policy(rows, policy) for policy in POLICIES}
    delta = _policy_delta(aggregates)

    _write_csv(rows)
    _write_json(rows, aggregates, delta)

    print("READY TAKE PILOT")
    for line in _table_lines(rows):
        print(line)
    for line in _aggregate_lines(aggregates, delta):
        print(line)
    print(f"CSV saved: {OUTPUT_CSV}")
    print(f"JSON saved: {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())