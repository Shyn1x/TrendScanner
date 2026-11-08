"""Prospective 4h Donchian LONG/SHORT shadow observations, no order surface.

Records signals and subsequently scores predeclared 1/3/6-bar hypothetical
outcomes on the *same* Bybit linear USDT perpetual trade candles.
This is per-signal research, NOT a portfolio PnL or wallet reconciliation.
Funding is unknown and excluded. No historical entries are backfilled.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "artifacts/trendscanner"
sys.path.insert(0, str(ROOT))
from regime_donchian_research import TIMEFRAME_MS, evaluate
from prospective_market_data import get_research_data_before, MARKET_SOURCE
# Frozen universe from ready_outcome_pilot; avoid old ccxt imports.
SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
           "DOGE/USDT", "ADA/USDT", "LINK/USDT", "LTC/USDT", "BCH/USDT",
           "AVAX/USDT", "DOT/USDT", "ATOM/USDT", "NEAR/USDT", "FIL/USDT",
           "ARB/USDT", "OP/USDT", "INJ/USDT", "SUI/USDT", "TIA/USDT",
           "APT/USDT", "PENDLE/USDT", "ETC/USDT", "AAVE/USDT", "UNI/USDT")

VERSION = "regime-donchian-4h-shadow-v1"
HORIZONS = (1, 3, 6)
COST_PCT = 0.16  # 6bp fee + 2bp slippage, on each side; funding omitted.
SYMBOL_LIMIT = 25
BARS = 500


def latest_target(now_ms: int) -> int:
    return int(now_ms) // TIMEFRAME_MS * TIMEFRAME_MS - TIMEFRAME_MS


def known_regime(frames: dict, target: int) -> str:
    """Past-only EMA200/BTC and market-breadth classification.

    Independently derives existing BULL/BEAR/MIXED rules without relying
    on READY-specific persistence. 500 closed bars warm up EMA200.
    """
    if "BTC/USDT" not in frames or len(frames) != SYMBOL_LIMIT:
        raise ValueError("INCOMPLETE_MARKET_CONTEXT")
    features = {}
    for symbol, frame in frames.items():
        if len(frame) < 250 or int(frame.iloc[-1]["time"]) != target:
            raise ValueError("INVALID_REGIME_FRAME")
        close = frame["close"].astype(float)
        if (close <= 0).any():
            raise ValueError("NONPOSITIVE_CLOSE")
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        features[symbol] = {"close": float(close.iloc[-1]),
                            "ema50": ema50, "ema200": ema200,
                            "return_20": float(close.iloc[-1] / close.iloc[-21] - 1) * 100}
    btc = features["BTC/USDT"]
    breadth = sum(v["close"] > v["ema50"] for v in features.values()) / len(features)
    median_return = statistics.median(v["return_20"] for v in features.values())
    if btc["close"] > btc["ema200"] and breadth >= .60 and median_return > 0:
        return "BULL"
    if btc["close"] < btc["ema200"] and breadth <= .40 and median_return < 0:
        return "BEAR"
    return "MIXED"


def outcome(direction: str, stop: float, future: list[dict], horizon: int) -> dict:
    """Next-candle open entry; conservative intrabar stop on trade OHLC."""
    if direction not in ("LONG", "SHORT") or len(future) != horizon:
        raise ValueError("INVALID_OUTCOME_WINDOW")
    entry = float(future[0]["open"])
    if entry <= 0 or stop <= 0:
        raise ValueError("INVALID_ENTRY_OR_STOP")
    if (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        return {"status": "INVALID_STOP_AT_NEXT_OPEN", "entry_price": entry,
                "net_return_pct": None, "funding_included": False}
    exit_price, stop_hit = float(future[-1]["close"]), False
    for candle in future:
        opened, low, high = (float(candle[k]) for k in ("open", "low", "high"))
        hit = low <= stop if direction == "LONG" else high >= stop
        if hit:
            exit_price = min(stop, opened) if direction == "LONG" else max(stop, opened)
            stop_hit = True
            break
    gross = (exit_price / entry - 1) * 100
    if direction == "SHORT":
        gross = -gross
    return {"status": "SCORED", "entry_price": entry, "exit_price": exit_price,
            "stop_hit": stop_hit, "gross_return_pct": gross,
            "net_return_pct": gross - COST_PCT, "funding_included": False}


def initialize(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS regime_donchian_shadow_signals (
        version TEXT NOT NULL, symbol TEXT NOT NULL, target_ms BIGINT NOT NULL,
        regime TEXT NOT NULL, direction TEXT NOT NULL, signal TEXT NOT NULL,
        reference_stop DOUBLE PRECISION NOT NULL, observed_at_ms BIGINT NOT NULL,
        PRIMARY KEY (version, symbol, target_ms)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS regime_donchian_shadow_outcomes (
        version TEXT NOT NULL, symbol TEXT NOT NULL, target_ms BIGINT NOT NULL,
        horizon INTEGER NOT NULL, result JSONB NOT NULL,
        PRIMARY KEY (version, symbol, target_ms, horizon),
        FOREIGN KEY (version, symbol, target_ms)
          REFERENCES regime_donchian_shadow_signals(version, symbol, target_ms)
    )""")


def collect(db_url: str, now_ms: int, loader=get_research_data_before) -> dict:
    if MARKET_SOURCE != "bybit_linear":
        raise ValueError("BYBIT_SOURCE_REQUIRED")
    if len(SYMBOLS) != SYMBOL_LIMIT or len(set(SYMBOLS)) != SYMBOL_LIMIT:
        raise ValueError("SYMBOL_UNIVERSE_CHANGED")
    target = latest_target(now_ms)
    frames = {}
    for symbol in SYMBOLS:
        frame = loader(symbol, "4h", target + TIMEFRAME_MS, total_limit=BARS)
        if len(frame) != BARS or int(frame.iloc[-1]["time"]) != target:
            raise ValueError("INCOMPLETE_EXACT_MARKET_WINDOW")
        frames[symbol] = frame

    regime = known_regime(frames, target)
    events = []
    fresh = 0 <= now_ms - (target + TIMEFRAME_MS) <= 15 * 60_000
    for symbol, frame in frames.items():
        candles = frame.tail(25).to_dict("records")
        rows = [{**row, "time": int(row["time"])} for row in candles]
        result = evaluate(rows, regime, target)
        if fresh and result["signal"] in ("ENTER_LONG", "ENTER_SHORT"):
            events.append((symbol, result))
        elif result.get("reason") in ("GAPPED_OR_DUPLICATE_CANDLES",
                                      "INVALID_4H_TIMESTAMP", "REGIME_TIMESTAMP_MISMATCH"):
            raise ValueError("STRATEGY_WINDOW_INVALID")

    import psycopg
    from psycopg.types.json import Jsonb
    saved, scored = 0, 0
    with psycopg.connect(db_url) as db:
        with db.cursor() as cur:
            initialize(cur)
            # Frozen prospective time: do not insert earlier signals as if live.
            for symbol, event in events:
                cur.execute("""
                    INSERT INTO regime_donchian_shadow_signals
                    (version,symbol,target_ms,regime,direction,signal,reference_stop,observed_at_ms)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING target_ms""",
                    (VERSION, symbol, target, regime,
                     "LONG" if event["signal"] == "ENTER_LONG" else "SHORT",
                     event["signal"], event["initial_stop_reference"], now_ms))
                saved += int(cur.fetchone() is not None)

            # Only score historical signals already frozen in an earlier poll.
            cur.execute("""
                SELECT s.symbol,s.target_ms,s.direction,s.reference_stop,h.horizon
                FROM regime_donchian_shadow_signals s
                CROSS JOIN (VALUES (1),(3),(6)) AS h(horizon)
                LEFT JOIN regime_donchian_shadow_outcomes o
                  ON (o.version=s.version AND o.symbol=s.symbol
                      AND o.target_ms=s.target_ms AND o.horizon=h.horizon)
                WHERE s.version=%s AND o.version IS NULL
                  AND s.target_ms + h.horizon * %s <= %s
                ORDER BY s.target_ms,s.symbol,h.horizon""",
                (VERSION, TIMEFRAME_MS, target))
            due = cur.fetchall()
            for symbol, signal_ms, direction, stop, horizon in due:
                frame = frames.get(symbol)
                # Missing history remains pending, never silently approximated.
                part = frame[(frame["time"] > signal_ms) &
                             (frame["time"] <= signal_ms + horizon * TIMEFRAME_MS)]
                if len(part) != horizon or int(part.iloc[0]["time"]) != signal_ms + TIMEFRAME_MS:
                    continue
                future = part.to_dict("records")
                times = [int(row["time"]) for row in future]
                if times != list(range(signal_ms + TIMEFRAME_MS,
                                       signal_ms + (horizon + 1) * TIMEFRAME_MS, TIMEFRAME_MS)):
                    continue
                result = outcome(direction, float(stop), future, horizon)
                cur.execute("""
                    INSERT INTO regime_donchian_shadow_outcomes
                    (version,symbol,target_ms,horizon,result) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING horizon""",
                    (VERSION, symbol, signal_ms, horizon, Jsonb(result)))
                scored += int(cur.fetchone() is not None)
            cur.execute("SELECT count(*) FROM regime_donchian_shadow_signals WHERE version=%s", (VERSION,))
            total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM regime_donchian_shadow_outcomes WHERE version=%s", (VERSION,))
            results = cur.fetchone()[0]
            cur.execute("""SELECT direction,count(*) FROM regime_donchian_shadow_signals
                        WHERE version=%s GROUP BY direction""", (VERSION,))
            by_direction = dict(cur.fetchall())
            cur.execute("""SELECT s.direction,o.horizon,count(*),
                           avg((o.result->>'net_return_pct')::double precision)
                           FROM regime_donchian_shadow_outcomes o
                           JOIN regime_donchian_shadow_signals s
                             ON (s.version=o.version AND s.symbol=o.symbol AND s.target_ms=o.target_ms)
                           WHERE o.version=%s AND o.result->>'status'='SCORED'
                           GROUP BY s.direction,o.horizon""", (VERSION,))
            scored_means = [{"direction": direction, "horizon": horizon,
                             "count": count, "avg_net_pct_ex_funding": avg}
                            for direction, horizon, count, avg in cur.fetchall()]
    return {"version": VERSION, "target_ms": target, "market_regime": regime,
            "fresh_signal_window": fresh,
            "new_signals": saved, "total_signals": total, "new_outcomes": scored,
            "total_outcomes": results, "signals_by_direction": by_direction,
            "scored_groups": scored_means, "funding_included": False,
            "portfolio_pnl_available": False, "read_only_exchange": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect", action="store_true", required=True)
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL_REQUIRED")
    print(json.dumps(collect(os.environ["DATABASE_URL"], int(time.time()*1000)), sort_keys=True))


if __name__ == "__main__":
    main()
