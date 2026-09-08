"""Prospective P2/P3 observation only. Deliberately not wired into live READY UI.

The caller must supply an existing READY candidate AND its original candle
open timestamp. Neither READY nor that timestamp is inferred here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from threading import Lock

import ready_market_regime_analysis as validated
from ready_outcome_pilot import SYMBOLS
from research_data import get_research_data_before
from scanner import exchange
from strategy_analytics import DEFAULT_DB_PATH

TIMEFRAME_MS = validated.TIMEFRAME_MS
CONTEXT_BARS = validated.CONTEXT_BARS
SHADOW_VERSION = "market-regime-v1-preregistered"
MARKET_FIELDS = (
    "market_regime", "volatility", "btc_return_60", "ema_distance",
    "breadth_above_ema50", "median_return_20", "atr_pct",
    "atr_percentile_past_200", "available_symbols", "prior_atr_observations",
)


def shadow_tag(direction, market_regime, volatility):
    if direction == "LONG" and volatility == "NORMAL":
        if market_regime == "MIXED":
            return "P3_MATCH", "PRIMARY"
        if market_regime == "BULL":
            return "P2_MATCH", "EXPERIMENTAL"
    return "NONE", "NONE"


def _unavailable(reason, target=None):
    return {"shadow_status": "UNAVAILABLE", "shadow_tag": "NONE",
            "candidate_tier": "NONE", "shadow_error": reason,
            "market": {**dict.fromkeys(MARKET_FIELDS),
                       "target_4h_timestamp": target, "available_symbols": 0}}


def _milliseconds(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("INVALID_TIMESTAMP")
    return value


def _validated_frame(frame, target):
    data = frame.copy(deep=True).sort_values("time").reset_index(drop=True)
    expected = [target - i * TIMEFRAME_MS for i in reversed(range(CONTEXT_BARS + 1))]
    times = data["time"].tolist()
    if len(times) != CONTEXT_BARS + 1:
        raise ValueError("INSUFFICIENT_HISTORY")
    if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or int(t) != t for t in times):
        raise ValueError("INVALID_TIMESTAMP")
    if times != expected:
        raise ValueError("NONEXACT_CANDLE_WINDOW")
    for row in data[["open", "high", "low", "close"]].itertuples(index=False, name=None):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in row):
            raise ValueError("INVALID_OHLC")
        op, hi, lo, close = row
        if lo > min(op, close) or hi < max(op, close) or lo > hi:
            raise ValueError("INVALID_OHLC")
    return data


class MarketContextCache:
    """One immutable snapshot per target, including failures, under a lock.

    The module singleton is the process-wide live entry point. Dependency-
    injected instances exist for offline tests. Old targets remain cached so
    concurrent/stale refreshes cannot re-fetch a previously observed candle.
    """
    def __init__(self, loader=get_research_data_before, clock=exchange.milliseconds, symbols=SYMBOLS):
        self._loader, self._clock = loader, clock
        self._symbols = tuple(dict.fromkeys(symbols))
        self._snapshots, self._lock = {}, Lock()

    def _build(self, target):
        contexts, errors = {}, {}
        for symbol in self._symbols:
            try:
                frame = self._loader(symbol, "4h", before_timestamp=target + TIMEFRAME_MS,
                                     total_limit=CONTEXT_BARS + 1)
                frame = _validated_frame(frame, target)
                feature = validated._frame_features(frame).get(target)
                if not feature or feature["prior_atr_observations"] != 200:
                    raise ValueError("INSUFFICIENT_ATR_HISTORY")
                contexts[("PROSPECTIVE", symbol)] = {target: feature}
            except Exception as exc:
                # Never serialize arbitrary exception text (URLs/credentials).
                errors[symbol] = type(exc).__name__
        if errors or ("PROSPECTIVE", "BTC/USDT") not in contexts:
            result = _unavailable("INCOMPLETE_CONTEXT" if errors else "MISSING_BTC", target)
            result["market"]["available_symbols"] = len(contexts)
            result["context_errors"] = errors
            return result
        # Context anchor only, not a synthetic READY observation; never logged.
        anchors, _ = validated._attach_market(
            [{"period": "PROSPECTIVE", "ready_timestamp": target}], contexts)
        if len(anchors) != 1:
            return _unavailable("MISSING_EXACT_CONTEXT", target)
        anchor = anchors[0]
        market = {key: anchor.get(key) for key in MARKET_FIELDS}
        market.update(target_4h_timestamp=target, btc_return_60=anchor["return_60"])
        return {"shadow_status": "OK", "shadow_error": None, "market": market}

    def snapshot(self):
        target = None
        try:
            now = _milliseconds(self._clock())
            target = (now // TIMEFRAME_MS) * TIMEFRAME_MS - TIMEFRAME_MS
            with self._lock:
                if target not in self._snapshots:
                    try:
                        self._snapshots[target] = self._build(target)
                    except Exception as exc:
                        self._snapshots[target] = _unavailable(type(exc).__name__, target)
                return deepcopy(self._snapshots[target])
        except Exception as exc:
            return _unavailable(type(exc).__name__, target)


_PROCESS_CONTEXT = MarketContextCache()


def _persist(event, db_path):
    """Use the analytics SQLite file, with a separate immutable event table.

    Transactions + UNIQUE serialize concurrent processes as well as threads.
    First observation wins, including UNAVAILABLE; later refreshes never revise it.
    """
    payload = json.dumps(event, sort_keys=True, allow_nan=False, separators=(",", ":"))
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=1) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS market_regime_shadow_events (
            shadow_version TEXT NOT NULL, symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL, direction TEXT NOT NULL,
            ready_timestamp INTEGER NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (shadow_version, symbol, timeframe, direction, ready_timestamp)
        )""")
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS market_shadow_no_{operation.lower()}
                BEFORE {operation} ON market_regime_shadow_events
                BEGIN SELECT RAISE(ABORT, 'immutable prospective observation'); END""")
        cursor = connection.execute("""INSERT OR IGNORE INTO market_regime_shadow_events
            VALUES (?, ?, ?, ?, ?, ?)""", (
            event["shadow_version"], event["symbol"], event["timeframe"],
            event["direction"], event["ready_timestamp"], payload))
        inserted = cursor.rowcount == 1
        stored = connection.execute("""SELECT payload FROM market_regime_shadow_events
            WHERE shadow_version=? AND symbol=? AND timeframe=? AND direction=? AND ready_timestamp=?""",
            (event["shadow_version"], event["symbol"], event["timeframe"],
             event["direction"], event["ready_timestamp"])).fetchone()
        return inserted, json.loads(stored[0])


def observe_ready(candidate, *, context=None, db_path=DEFAULT_DB_PATH, observed_at_ms=None):
    """Return a separate shadow result, never mutate/recompute production READY.

    No caller is installed in production until a trustworthy source candle
    timestamp is available. Do not fill ready_timestamp with a refresh time,
    breakout timestamp or market-context timestamp.
    """
    try:
        if candidate.get("ready") is not True:
            return {"shadow_status": "NOT_READY", "shadow_tag": "NONE", "candidate_tier": "NONE", "inserted": False}
        timestamp = _milliseconds(candidate["ready_timestamp"])
        symbol, timeframe, direction = candidate["symbol"], candidate["timeframe"], candidate["direction"]
        if not isinstance(symbol, str) or not symbol or timeframe not in ("1h", "4h") or direction not in ("LONG", "SHORT"):
            raise ValueError("INVALID_READY_IDENTITY")
        now = _milliseconds(exchange.milliseconds() if observed_at_ms is None else observed_at_ms)
        if timestamp > now:
            raise ValueError("FUTURE_READY_TIMESTAMP")
        snapshot = (context if context is not None else _PROCESS_CONTEXT).snapshot()
        tag, tier = shadow_tag(direction, snapshot["market"]["market_regime"], snapshot["market"]["volatility"]) if snapshot["shadow_status"] == "OK" else ("NONE", "NONE")
        event = {
            "schema_version": 1, "shadow_version": SHADOW_VERSION,
            "observed_at": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
            "symbol": symbol, "timeframe": timeframe, "direction": direction,
            "ready_timestamp": timestamp,
            "production": {"decision": candidate["production_decision"],
                           "decision_score": candidate["decision_score"], "confidence": candidate["confidence"]},
            "market": deepcopy(snapshot["market"]),
            "shadow": {"shadow_status": snapshot["shadow_status"], "shadow_error": snapshot["shadow_error"],
                       "shadow_tag": tag, "candidate_tier": tier},
        }
        inserted, event = _persist(event, db_path)
        return {**event["shadow"], "inserted": inserted, "event": event}
    except Exception as exc:
        return {**_unavailable(type(exc).__name__), "inserted": False}
