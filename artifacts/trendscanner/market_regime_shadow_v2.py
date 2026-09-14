"""Isolated v2 storage adapter for sequential prospective 4h collection.

The validated READY rules and market-regime calculations are unchanged. This
module only gives the repaired prospective series a new immutable namespace so
it cannot be mixed with the gapped v1 history.
"""
from copy import deepcopy
from datetime import datetime, timezone

from market_regime_shadow import (
    TIMEFRAME_MS,
    MarketContextCache,
    _PROCESS_CONTEXT,
    _milliseconds,
    _persist,
    _unavailable,
    shadow_tag,
)
from scanner import exchange
from strategy_analytics import DEFAULT_DB_PATH

SHADOW_VERSION = "market-regime-v2-sequential"


def observe_ready(candidate, *, context=None, db_path=DEFAULT_DB_PATH,
                  observed_at_ms=None, database_url=None):
    """Persist one 4h observation in the isolated v2 namespace."""
    try:
        timestamp = _milliseconds(candidate["ready_timestamp"])
        symbol = candidate["symbol"]
        timeframe = candidate["timeframe"]
        direction = candidate["direction"]
        if (
            not isinstance(symbol, str)
            or not symbol
            or timeframe != "4h"
            or direction not in ("LONG", "SHORT")
        ):
            raise ValueError("INVALID_READY_IDENTITY")
        if timestamp % TIMEFRAME_MS:
            raise ValueError("UNALIGNED_READY_TIMESTAMP")
        if candidate.get("ready") is not True and candidate.get("ready") is not False:
            raise ValueError("INVALID_READY_STATE")

        now = _milliseconds(exchange.milliseconds() if observed_at_ms is None else observed_at_ms)
        if timestamp + TIMEFRAME_MS > now:
            raise ValueError("FUTURE_READY_TIMESTAMP")

        if candidate["ready"] is False:
            _persist(
                None,
                db_path,
                identity=(SHADOW_VERSION, symbol, timeframe, direction, timestamp),
                database_url=database_url,
            )
            return {
                "shadow_status": "NOT_READY",
                "shadow_tag": "NONE",
                "candidate_tier": "NONE",
                "inserted": False,
            }

        snapshot = (context if context is not None else _PROCESS_CONTEXT).snapshot()
        if snapshot["market"].get("target_4h_timestamp") != timestamp:
            snapshot = _unavailable("CONTEXT_TIMESTAMP_MISMATCH", timestamp)

        if snapshot["shadow_status"] == "OK":
            tag, tier = shadow_tag(
                direction,
                snapshot["market"]["market_regime"],
                snapshot["market"]["volatility"],
            )
        else:
            tag, tier = "NONE", "NONE"

        event = {
            "schema_version": 1,
            "shadow_version": SHADOW_VERSION,
            "observed_at": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "ready_timestamp": timestamp,
            "production": {
                "decision": candidate["production_decision"],
                "decision_score": candidate["decision_score"],
                "confidence": candidate["confidence"],
            },
            "market": deepcopy(snapshot["market"]),
            "shadow": {
                "shadow_status": snapshot["shadow_status"],
                "shadow_error": snapshot["shadow_error"],
                "shadow_tag": tag,
                "candidate_tier": tier,
            },
        }
        inserted, stored_event = _persist(event, db_path, database_url=database_url)
        if stored_event is None:
            return {
                "shadow_status": "NO_TRANSITION",
                "shadow_tag": "NONE",
                "candidate_tier": "NONE",
                "inserted": False,
            }
        return {
            **stored_event["shadow"],
            "inserted": inserted,
            "event": stored_event,
        }
    except Exception as exc:
        return {**_unavailable(type(exc).__name__), "inserted": False}


def context_for_target(target_timestamp):
    """Create an immutable market-context cache anchored to one historical target."""
    target = int(target_timestamp)
    return MarketContextCache(clock=lambda: target + TIMEFRAME_MS)
