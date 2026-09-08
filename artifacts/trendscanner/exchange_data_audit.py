"""Read-only Bybit/KuCoin data comparison, separate from the live scanner.

No credentials, orders, scoring, signal evaluation or historical replay.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import ccxt

from config import SYMBOLS, TIMEFRAMES

INTERVALS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}
SOURCES = ("kucoinfutures", "bybit")


def native_timeframes(client, name: str) -> list[str]:
    # CCXT's KuCoin top-level table also includes spot-only intervals.
    if name == "kucoinfutures":
        swaps = client.options.get("timeframes", {}).get("swap", {})
        return sorted(key for key, value in swaps.items() if value is not None)
    return sorted(client.timeframes or {})


def next_open(timestamp: int, timeframe: str) -> int:
    if timeframe in INTERVALS:
        return timestamp + INTERVALS[timeframe]
    if timeframe != "1M":
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    value = datetime.fromtimestamp(timestamp / 1000, UTC)
    return int(datetime(value.year + (value.month == 12), value.month % 12 + 1, 1, tzinfo=UTC).timestamp() * 1000)


def window_start(as_of_ms: int, timeframe: str, limit: int) -> int:
    if timeframe in INTERVALS:
        return max(0, as_of_ms - (limit + 2) * INTERVALS[timeframe])
    if timeframe != "1M":
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    value = datetime.fromtimestamp(as_of_ms / 1000, UTC)
    year, month = divmod(value.year * 12 + value.month - 1 - limit - 2, 12)
    return max(0, int(datetime(year, month + 1, 1, tzinfo=UTC).timestamp() * 1000))


def inspect_candles(rows: list, timeframe: str, as_of_ms: int, limit: int) -> dict:
    """Exclude open candles and conflicting duplicates; retain quality evidence."""
    next_open(as_of_ms, timeframe)  # Validate even for an empty response.
    candles, conflicts = {}, set()
    invalid, duplicates, open_rows = 0, 0, 0
    for row in rows:
        try:
            if not isinstance(row, (list, tuple)) or len(row) < 6 or any(isinstance(value, bool) for value in row[:6]):
                raise ValueError("Invalid OHLCV row")
            timestamp = int(row[0])
            values = [float(value) for value in row[1:6]]
            op, high, low, close, volume = values
            if timestamp < 0 or timestamp != float(row[0]) or any(not math.isfinite(value) for value in values):
                raise ValueError("Invalid number")
            if min(op, high, low, close) <= 0 or volume < 0 or high < max(op, low, close) or low > min(op, close):
                raise ValueError("Invalid price/volume")
            if timeframe == "1M":
                value = datetime.fromtimestamp(timestamp / 1000, UTC)
                if (value.day, value.hour, value.minute, value.second, value.microsecond) != (1, 0, 0, 0, 0):
                    raise ValueError("Monthly candle must start at a UTC month boundary")
            closes_at = next_open(timestamp, timeframe)
        except (TypeError, ValueError, OverflowError):
            invalid += 1
            continue
        if closes_at > as_of_ms:
            open_rows += 1
            continue
        if timestamp in candles:
            duplicates += 1
            if candles[timestamp] != values:
                conflicts.add(timestamp)
        else:
            candles[timestamp] = values
    for timestamp in conflicts:
        candles.pop(timestamp, None)
    timestamps = sorted(candles)[-limit:]
    selected = [[timestamp, *candles[timestamp]] for timestamp in timestamps]
    gaps = sum(next_open(left, timeframe) != right for left, right in zip(timestamps, timestamps[1:]))
    stale = not timestamps or next_open(next_open(timestamps[-1], timeframe), timeframe) <= as_of_ms
    return {"status": "OK", "candles": selected, "raw_rows": len(rows), "closed_rows": len(selected),
            "invalid_rows": invalid, "duplicate_rows": duplicates, "conflicting_timestamps": sorted(conflicts),
            "open_rows_excluded": open_rows, "gap_intervals": gaps, "stale": stale}


def contract_check(market: dict | None, symbol: str) -> dict:
    expected_base = symbol.split("/")[0]
    valid = bool(market and market.get("active") is True and market.get("swap") is True
                 and market.get("linear") is True and market.get("quote") == "USDT"
                 and market.get("settle") == "USDT" and market.get("base") == expected_base)
    return {"status": "OK" if valid else "UNSUPPORTED_CONTRACT",
            "market": {key: market.get(key) for key in ("id", "symbol", "base", "quote", "settle", "swap", "linear", "active", "contractSize")} if market else None}


def compare_candles(kucoin: dict, bybit: dict, limit: int) -> dict:
    if kucoin["status"] != "OK" or bybit["status"] != "OK":
        return {"status": "NOT_COMPARABLE", "sources": {"kucoinfutures": kucoin["status"], "bybit": bybit["status"]}, "matched_candles": 0}
    left = {row[0]: row[1:] for row in kucoin["candles"]}
    right = {row[0]: row[1:] for row in bybit["candles"]}
    common = sorted(left.keys() & right.keys())
    clean = all(not source["invalid_rows"] and not source["conflicting_timestamps"]
                and not source["gap_intervals"] and not source["stale"] for source in (kucoin, bybit))
    differences = {}
    for index, name in enumerate(("open", "high", "low", "close")):
        values = [(right[stamp][index] / left[stamp][index] - 1) * 100 for stamp in common]
        differences[name] = {"mean_signed_pct": statistics.mean(values) if values else None,
                             "max_abs_pct": max(map(abs, values)) if values else None}
    return {"status": "COMPARED" if clean and len(common) == limit else "INCOMPLETE",
            "matched_candles": len(common), "only_kucoin_timestamps": sorted(left.keys() - right.keys()),
            "only_bybit_timestamps": sorted(right.keys() - left.keys()),
            "price_differences_bybit_vs_kucoin": differences,
            "volume_comparison": "NOT_COMPARED: exchange-specific activity and volume units"}


def audit_sources(clients: dict, *, symbols: list[str], universe: list[str],
                  timeframes: list[str], as_of_ms: int, limit: int = 100) -> dict:
    if isinstance(as_of_ms, bool) or not isinstance(as_of_ms, int) or as_of_ms <= 0:
        raise ValueError("as_of_ms must be a positive integer timestamp")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 198:
        raise ValueError("limit must be between 1 and 198 (one bounded KuCoin page)")
    if not symbols or not timeframes:
        raise ValueError("At least one symbol and timeframe is required")
    for timeframe in timeframes:
        next_open(as_of_ms, timeframe)
    all_symbols = sorted(set(universe) | set(symbols))
    exchanges, windows = {}, {}
    for name in SOURCES:
        client = clients[name]
        supported = native_timeframes(client, name)
        windows[name] = {}
        try:
            markets = client.load_markets()
        except Exception as exc:
            exchanges[name] = {"status": "BLOCKED", "native_timeframes": supported, "stage": "load_markets", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            continue
        contracts = {symbol: contract_check(markets.get(symbol if ":" in symbol else symbol + ":USDT"), symbol) for symbol in all_symbols}
        exchanges[name] = {"status": "OK", "contracts": contracts, "native_timeframes": supported}
        for symbol in symbols:
            windows[name][symbol] = {}
            for timeframe in timeframes:
                if contracts[symbol]["status"] != "OK":
                    result = {"status": "UNSUPPORTED_CONTRACT"}
                elif timeframe not in supported:
                    result = {"status": "UNSUPPORTED_NATIVE_TIMEFRAME",
                              "note": "The scanner's KuCoin monthly resampling needs a separate daily-history comparison."}
                else:
                    try:
                        rows = client.fetch_ohlcv(symbol if ":" in symbol else symbol + ":USDT", timeframe,
                                                  since=window_start(as_of_ms, timeframe, limit), limit=limit + 2,
                                                  params={"until" if name == "bybit" else "to": as_of_ms})
                        result = inspect_candles(rows, timeframe, as_of_ms, limit)
                    except Exception as exc:
                        result = {"status": "BLOCKED", "stage": "fetch_ohlcv", "error_type": type(exc).__name__, "error": str(exc)[:500]}
                windows[name][symbol][timeframe] = result
    comparisons = []
    for symbol in symbols:
        for timeframe in timeframes:
            inputs = [windows[name].get(symbol, {}).get(timeframe, {"status": "BLOCKED"}) for name in SOURCES]
            comparisons.append({"symbol": symbol, "timeframe": timeframe, **compare_candles(*inputs, limit)})
    contracts_ok = all(item["status"] == "OK" and all(check["status"] == "OK" for check in item["contracts"].values()) for item in exchanges.values())
    if any(item["status"] == "BLOCKED" for item in exchanges.values()):
        status = "BLOCKED"
    elif contracts_ok and all(item["status"] == "COMPARED" for item in comparisons):
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    return {"schema_version": 1, "status": status, "as_of_ms": as_of_ms, "ccxt_version": ccxt.__version__,
            "sample_symbols": symbols, "universe": all_symbols, "timeframes": timeframes, "requested_closed_candles": limit,
            "exchanges": exchanges, "windows": windows, "comparisons": comparisons,
            "interpretation": "Descriptive data audit only; COMPARED does not establish signal parity or strategy validity."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    parser.add_argument("--timeframes", nargs="+", choices=list(INTERVALS) + ["1M"], default=TIMEFRAMES)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--as-of-ms", type=int, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    options = {"enableRateLimit": True, "timeout": 8000}
    clients = {"kucoinfutures": ccxt.kucoinfutures(options),
               "bybit": ccxt.bybit({**options, "options": {"defaultType": "swap", "defaultSubType": "linear", "fetchMarkets": {"types": ["linear"]}}})}
    report = audit_sources(clients, symbols=args.symbols, universe=SYMBOLS, timeframes=args.timeframes,
                           as_of_ms=args.as_of_ms if args.as_of_ms is not None else int(time.time() * 1000), limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output), "exchanges": {name: value["status"] for name, value in report["exchanges"].items()}}))
    return 0 if report["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
