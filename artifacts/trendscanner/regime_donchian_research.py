"""Research-only Donchian 4h strategy gated by existing market regime context.

No exchange API, database writes, or order execution. Every input candle must
be a completed 4h candle. The regime label must be the regime known at the
same candle close, not a later/recomputed label.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

TIMEFRAME_MS = 4 * 60 * 60 * 1000
ENTRY_LOOKBACK = 20
EXIT_LOOKBACK = 10
ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0
MIN_HISTORY = max(ENTRY_LOOKBACK, ATR_PERIOD + 1)


def _candle(row: Mapping) -> tuple[int, float, float, float, float]:
    timestamp = row["time"]
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp % TIMEFRAME_MS:
        raise ValueError("INVALID_4H_TIMESTAMP")
    values = tuple(float(row[key]) for key in ("open", "high", "low", "close"))
    o, h, l, c = values
    if not all(math.isfinite(v) and v > 0 for v in values) or l > min(o, c) or h < max(o, c):
        raise ValueError("INVALID_OHLC")
    return timestamp, o, h, l, c


def evaluate(candles: Sequence[Mapping], regime: str, regime_timestamp: int) -> dict:
    """Evaluate one finished 4h signal, never place an order.

    Regime must be the exact-time 4h BULL/BEAR/MIXED context already produced by
    TrendScanner; missing/stale context blocks entries, never defaults to BULL.
    """
    if len(candles) < MIN_HISTORY + 1:
        return {"signal": "NO_TRADE", "reason": "INSUFFICIENT_HISTORY"}
    parsed = [_candle(row) for row in candles]
    times = [r[0] for r in parsed]
    if any(b - a != TIMEFRAME_MS for a, b in zip(times, times[1:])):
        return {"signal": "NO_TRADE", "reason": "GAPPED_OR_DUPLICATE_CANDLES"}
    if isinstance(regime_timestamp, bool) or regime_timestamp != times[-1]:
        return {"signal": "NO_TRADE", "reason": "REGIME_TIMESTAMP_MISMATCH"}
    if regime not in ("BULL", "BEAR", "MIXED"):
        return {"signal": "NO_TRADE", "reason": "REGIME_UNAVAILABLE"}

    current = parsed[-1]
    prior20 = parsed[-ENTRY_LOOKBACK-1:-1]
    prior10 = parsed[-EXIT_LOOKBACK-1:-1]
    # No future candles, no current candle in channel boundaries.
    upper = max(r[2] for r in prior20)
    lower = min(r[3] for r in prior10)
    recent = parsed[-ATR_PERIOD:]
    previous_closes = [r[4] for r in parsed[-ATR_PERIOD-1:-1]]
    true_ranges = [max(r[2]-r[3], abs(r[2]-prev), abs(r[3]-prev))
                   for r, prev in zip(recent, previous_closes)]
    atr = sum(true_ranges) / ATR_PERIOD
    base = {"regime": regime, "signal_close_ms": times[-1] + TIMEFRAME_MS,
            "upper_20": upper, "lower_10": lower, "atr_14": atr,
            "research_only": True}
    if current[4] < lower:
        return {**base, "signal": "EXIT_LONG", "reason": "DONCHIAN_10_CLOSE_BREAK"}
    if regime != "BULL":
        return {**base, "signal": "NO_TRADE", "reason": "REGIME_NOT_BULL"}
    if current[4] <= upper:
        return {**base, "signal": "NO_TRADE", "reason": "NO_20BAR_CLOSE_BREAKOUT"}
    return {**base, "signal": "ENTER_LONG", "reason": "BULL_20BAR_CLOSE_BREAKOUT",
            "initial_stop_reference": current[4] - ATR_MULTIPLIER * atr}
