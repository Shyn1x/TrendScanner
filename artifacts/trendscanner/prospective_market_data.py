"""Explicit venue selection for prospective collection; archived research stays KuCoin.

Bybit candles are mainnet USDT linear perpetual trade candles. Only completed,
exact consecutive slots are accepted, and no other venue/product is a fallback.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

import pandas as pd

KUCOIN_LOWER_TF_VERSION = "lower-tf-ready-v5-fixed200-catchup"
BYBIT_LOWER_TF_VERSION = "lower-tf-ready-v6-bybit-fixed200-catchup"
KUCOIN_4H_VERSION = "market-regime-v2-sequential"
BYBIT_4H_VERSION = "market-regime-v3-bybit-sequential"
MARKET_SOURCE = os.environ.get("TREND_SCANNER_MARKET_SOURCE", "kucoin_futures")
if MARKET_SOURCE not in {"kucoin_futures", "bybit_linear"}:
    raise ValueError("UNSUPPORTED_MARKET_SOURCE")
LOWER_TF_VERSION = BYBIT_LOWER_TF_VERSION if MARKET_SOURCE == "bybit_linear" else KUCOIN_LOWER_TF_VERSION
FOUR_H_VERSION = BYBIT_4H_VERSION if MARKET_SOURCE == "bybit_linear" else KUCOIN_4H_VERSION
PUBLIC_BASE_URL = "https://api.bybit.com"
INTERVALS = {"15m": ("15", 900_000), "1h": ("60", 3_600_000), "4h": ("240", 14_400_000)}
COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def bybit_symbol(symbol):
    if not isinstance(symbol, str) or not symbol.endswith("/USDT"):
        raise ValueError("UNSUPPORTED_BYBIT_SYMBOL")
    base = symbol[:-5]
    if not base or not base.isalnum() or not base.isupper():
        raise ValueError("UNSUPPORTED_BYBIT_SYMBOL")
    return base + "USDT"


def _get(path, params):
    url = PUBLIC_BASE_URL + path + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            if payload.get("retCode") != 0:
                raise ValueError("BYBIT_PUBLIC_API_ERROR:" + str(payload.get("retCode")))
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("INVALID_BYBIT_RESULT")
            return result
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


@lru_cache(maxsize=100)
def _validate_instrument(api_symbol):
    result = _get("/v5/market/instruments-info", {"category": "linear", "symbol": api_symbol})
    rows = result.get("list", [])
    if len(rows) != 1:
        raise ValueError("BYBIT_INSTRUMENT_NOT_FOUND:" + api_symbol)
    row = rows[0]
    if any(row.get(key) != value for key, value in {
        "symbol": api_symbol, "status": "Trading", "contractType": "LinearPerpetual",
        "quoteCoin": "USDT", "settleCoin": "USDT",
    }.items()):
        raise ValueError("BYBIT_USDT_PERPETUAL_REQUIRED:" + api_symbol)


def get_bybit_data_before(symbol, timeframe, before_timestamp, total_limit=1000):
    if timeframe not in INTERVALS:
        raise ValueError("UNSUPPORTED_BYBIT_TIMEFRAME")
    interval, tf_ms = INTERVALS[timeframe]
    if isinstance(before_timestamp, bool) or not isinstance(before_timestamp, int):
        raise ValueError("INVALID_BYBIT_BOUNDARY")
    if before_timestamp <= 0 or before_timestamp % tf_ms:
        raise ValueError("UNALIGNED_BYBIT_BOUNDARY")
    if isinstance(total_limit, bool) or not isinstance(total_limit, int) or total_limit <= 0:
        raise ValueError("INVALID_BYBIT_LIMIT")
    # Do not treat an open candle's last trade as its completed close.
    if before_timestamp > int(time.time() * 1000) // tf_ms * tf_ms:
        raise ValueError("BYBIT_BOUNDARY_NOT_CLOSED")
    api_symbol = bybit_symbol(symbol)
    _validate_instrument(api_symbol)
    rows_by_time = {}
    end = before_timestamp - 1
    while len(rows_by_time) < total_limit:
        result = _get("/v5/market/kline", {
            "category": "linear", "symbol": api_symbol, "interval": interval,
            "end": end, "limit": min(1000, total_limit - len(rows_by_time)),
        })
        if result.get("category") != "linear" or result.get("symbol") != api_symbol:
            raise ValueError("WRONG_BYBIT_CANDLE_MARKET")
        page = result.get("list")
        if not isinstance(page, list) or not page:
            raise ValueError("INSUFFICIENT_BYBIT_HISTORY:" + api_symbol)
        page_times = []
        for row in page:
            if not isinstance(row, list) or len(row) < 6:
                raise ValueError("INVALID_BYBIT_CANDLE")
            stamp = int(row[0])
            if stamp % tf_ms or stamp > end or stamp >= before_timestamp:
                raise ValueError("BYBIT_CANDLE_OUTSIDE_BOUNDARY")
            values = [float(value) for value in row[1:6]]
            op, hi, lo, close, volume = values
            if any(not math.isfinite(v) for v in values) or min(op, hi, lo, close) <= 0 or volume < 0:
                raise ValueError("INVALID_BYBIT_OHLCV")
            if lo > min(op, close) or hi < max(op, close) or lo > hi:
                raise ValueError("INVALID_BYBIT_OHLC")
            candle = [stamp, *values]
            if stamp in rows_by_time:
                raise ValueError("DUPLICATE_BYBIT_CANDLE")
            rows_by_time[stamp] = candle
            page_times.append(stamp)
        next_end = min(page_times) - 1
        if next_end >= end:
            raise ValueError("BYBIT_PAGINATION_NO_PROGRESS")
        end = next_end
    expected = list(range(before_timestamp - total_limit * tf_ms, before_timestamp, tf_ms))
    if sorted(rows_by_time) != expected:
        raise ValueError("NONEXACT_BYBIT_CANDLE_WINDOW:" + api_symbol)
    frame = pd.DataFrame([rows_by_time[stamp] for stamp in expected], columns=COLUMNS)
    frame.attrs["market_source"] = "bybit_linear"
    return frame


def get_research_data_before(symbol, timeframe, before_timestamp, total_limit=1000):
    if MARKET_SOURCE == "bybit_linear":
        return get_bybit_data_before(symbol, timeframe, before_timestamp, total_limit)
    from research_data import get_research_data_before as kucoin_loader
    return kucoin_loader(symbol, timeframe, before_timestamp, total_limit)
