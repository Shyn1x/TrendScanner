"""Separate 1h/15m prospective READY observer.

This experiment does not touch the validated 4h shadow tables. It records only
post-baseline False->True READY transitions. Lower-timeframe READY is evaluated
from a fixed 200-slot time grid. KuCoin no-tick slots are represented by a flat
previous-close candle with zero volume under a bounded, recorded fill policy.
The latest fully closed 4h candle available when the lower-timeframe signal
closes is stored as a later market-context anchor.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import lower_tf_storage
from scanner import exchange

EXPERIMENT_VERSION = "lower-tf-ready-v4-fixed200-gridfill-restart"
TIMEFRAME_MS = {"1h": 3_600_000, "15m": 900_000}
FOUR_H_MS = 14_400_000
VALID_DIRECTIONS = ("LONG", "SHORT")
CONTEXT_POLICY = "latest_fully_closed_4h_at_signal_close"
RESEARCH_RULE = (
    "Collect lower-TF READY False->True transitions first; later test whether "
    "LONG + 4h market_regime=MIXED + 4h volatility=NORMAL remains useful."
)


def _milliseconds(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("INVALID_TIMESTAMP")
    return value


def context_4h_timestamp(ready_timestamp: int, timeframe: str) -> int:
    tf_ms = TIMEFRAME_MS[timeframe]
    signal_close = ready_timestamp + tf_ms
    return (signal_close // FOUR_H_MS) * FOUR_H_MS - FOUR_H_MS


def observe_lower_tf(candidate, *, database_url=None, db_path=None, observed_at_ms=None):
    try:
        if not isinstance(candidate, dict):
            raise ValueError("INVALID_CANDIDATE")

        symbol = candidate.get("symbol")
        timeframe = candidate.get("timeframe")
        direction = candidate.get("direction")
        timestamp = _milliseconds(candidate.get("ready_timestamp"))
        ready = candidate.get("ready")

        if not isinstance(symbol, str) or not symbol:
            raise ValueError("INVALID_SYMBOL")
        if timeframe not in TIMEFRAME_MS:
            raise ValueError("INVALID_TIMEFRAME")
        if direction not in VALID_DIRECTIONS:
            raise ValueError("INVALID_DIRECTION")
        if type(ready) is not bool:
            raise ValueError("INVALID_READY_STATE")

        tf_ms = TIMEFRAME_MS[timeframe]
        if timestamp % tf_ms:
            raise ValueError("UNALIGNED_READY_TIMESTAMP")

        now = _milliseconds(exchange.milliseconds() if observed_at_ms is None else observed_at_ms)
        if timestamp + tf_ms > now:
            raise ValueError("FUTURE_READY_TIMESTAMP")

        anchor = context_4h_timestamp(timestamp, timeframe)
        event = None
        if ready:
            event = {
                "schema_version": 2,
                "experiment_version": EXPERIMENT_VERSION,
                "observed_at": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "ready_timestamp": timestamp,
                "candidate_window_bars": 200,
                "candidate": deepcopy(candidate),
                "market_context": {
                    "timeframe": "4h",
                    "policy": CONTEXT_POLICY,
                    "target_4h_timestamp": anchor,
                    "classification_at_collection": False,
                },
                "research": {
                    "name": "Lower-timeframe READY prospective collection",
                    "rule": RESEARCH_RULE,
                    "market_regime_version": "market-regime-v1-preregistered",
                    "context_rows": 1213,
                },
            }

        result = lower_tf_storage.observe(
            event,
            identity=(EXPERIMENT_VERSION, symbol, timeframe, direction, timestamp),
            ready=ready,
            database_url=database_url,
            db_path=db_path,
        )
        return {
            "shadow_status": result["status"],
            "inserted": bool(result.get("inserted", False)),
            "target_4h_timestamp": anchor,
        }
    except Exception as exc:
        return {
            "shadow_status": "UNAVAILABLE",
            "inserted": False,
            "shadow_error": type(exc).__name__,
            "target_4h_timestamp": None,
        }
