"""Research-only historical data loader.

This module is intentionally separate from scanner.get_data(): it exists to
fetch deeper OHLCV history for offline research/backtesting (ready_outcome_pilot
and similar tools) without touching the live scanner path or its pagination
logic. It reuses scanner._to_futures_symbol and scanner._fetch_recent_ohlcv.
"""

from __future__ import annotations

import time

import ccxt
import pandas as pd

from scanner import _fetch_recent_ohlcv, _to_futures_symbol, exchange

_RETRY_DELAYS_SECONDS = (2, 5)
_MAX_ATTEMPTS = 1 + len(_RETRY_DELAYS_SECONDS)


def _fetch_recent_ohlcv_with_retry(
    futures_symbol: str,
    timeframe: str,
    total_limit: int,
    symbol: str,
) -> list:
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return _fetch_recent_ohlcv(futures_symbol, timeframe, total_limit)
        except ccxt.NetworkError as exc:
            if attempt >= _MAX_ATTEMPTS:
                raise
            print(
                f"Research data retry {attempt}/{_MAX_ATTEMPTS} for {symbol} {timeframe}: "
                f"{type(exc).__name__}",
                flush=True,
            )
            time.sleep(_RETRY_DELAYS_SECONDS[attempt - 1])

    raise AssertionError("unreachable")  # loop always returns or raises


def _fetch_ohlcv_batch_with_retry(
    futures_symbol: str,
    timeframe: str,
    since: int,
    symbol: str,
) -> list:
    """Fetch one bounded research batch while preserving retry behavior."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return exchange.fetch_ohlcv(
                futures_symbol,
                timeframe,
                since=since,
                limit=200,
            )
        except ccxt.NetworkError as exc:
            if attempt >= _MAX_ATTEMPTS:
                raise
            print(
                f"Research data retry {attempt}/{_MAX_ATTEMPTS} for {symbol} {timeframe}: "
                f"{type(exc).__name__}",
                flush=True,
            )
            time.sleep(_RETRY_DELAYS_SECONDS[attempt - 1])

    raise AssertionError("unreachable")


def get_research_data(
    symbol: str,
    timeframe: str,
    total_limit: int = 1000,
) -> pd.DataFrame:
    if timeframe == "1M":
        raise ValueError(
            "get_research_data does not support timeframe='1M' yet: "
            "research MVP is validated only on regular timeframes (e.g. '4h')."
        )

    futures_symbol = _to_futures_symbol(symbol)

    candles = _fetch_recent_ohlcv_with_retry(
        futures_symbol,
        timeframe,
        total_limit,
        symbol,
    )

    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    return df


def get_research_data_before(
    symbol: str,
    timeframe: str,
    before_timestamp: int,
    total_limit: int = 1000,
) -> pd.DataFrame:
    """Return up to ``total_limit`` completed candles strictly before a boundary.

    The production scanner helper is intentionally not extended: it always
    anchors its pagination to the current time. This research-only path grows
    its initial lookback when an exchange response has gaps or is truncated.
    """
    if timeframe == "1M":
        raise ValueError("get_research_data_before does not support timeframe='1M'")
    if total_limit <= 0:
        raise ValueError("total_limit must be positive")
    if isinstance(before_timestamp, bool):
        raise ValueError("before_timestamp must be an integer timestamp")
    try:
        boundary = int(before_timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError("before_timestamp must be an integer timestamp") from exc
    if boundary <= 0:
        raise ValueError("before_timestamp must be positive")

    futures_symbol = _to_futures_symbol(symbol)
    timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    if timeframe_ms <= 0:
        raise ValueError(f"invalid timeframe: {timeframe}")

    lookback_bars = total_limit + 5
    candles_by_timestamp: dict[int, list] = {}
    # Six expansions reach a lookback 64 times wider than requested.
    for _ in range(6):
        since = max(0, boundary - lookback_bars * timeframe_ms)
        max_batches = (lookback_bars + 199) // 200 + 3

        for _ in range(max_batches):
            batch = _fetch_ohlcv_batch_with_retry(futures_symbol, timeframe, since, symbol)
            if not batch:
                break

            last_timestamp = since
            for candle in batch:
                if not isinstance(candle, (list, tuple)) or not candle:
                    continue
                try:
                    timestamp = int(candle[0])
                except (TypeError, ValueError):
                    continue
                last_timestamp = max(last_timestamp, timestamp)
                if timestamp < boundary:
                    candles_by_timestamp[timestamp] = list(candle)

            next_since = last_timestamp + timeframe_ms
            if next_since <= since or last_timestamp >= boundary - timeframe_ms:
                break
            since = next_since

        if len(candles_by_timestamp) >= total_limit or since == 0:
            break
        lookback_bars *= 2

    candles = [candles_by_timestamp[timestamp] for timestamp in sorted(candles_by_timestamp)]
    candles = candles[-total_limit:]
    return pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume"])
