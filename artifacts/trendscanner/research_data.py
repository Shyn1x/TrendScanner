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

from scanner import _fetch_recent_ohlcv, _to_futures_symbol

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
