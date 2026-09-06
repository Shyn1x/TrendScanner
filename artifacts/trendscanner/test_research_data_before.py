from __future__ import annotations

import ccxt

import research_data


class _Exchange:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def parse_timeframe(self, timeframe):
        assert timeframe == "4h"
        return 4 * 60 * 60

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append((symbol, timeframe, since, limit))
        return self.batches.pop(0) if self.batches else []


def _candle(timestamp):
    return [timestamp, 1.0, 2.0, 0.5, 1.5, 100.0]


def _patch(name, value):
    original = getattr(research_data, name)
    setattr(research_data, name, value)
    return original


def test_boundary_is_strict_and_duplicates_removed():
    exchange = _Exchange([[_candle(100), _candle(200), _candle(200), _candle(300)], []])
    original = _patch("exchange", exchange)
    try:
        frame = research_data.get_research_data_before("BTC/USDT", "4h", 300, total_limit=3)
    finally:
        _patch("exchange", original)
    assert frame["time"].tolist() == [100, 200]
    assert all(value < 300 for value in frame["time"])


def test_returns_last_total_limit():
    exchange = _Exchange([[_candle(100), _candle(200), _candle(300), _candle(400)], []])
    original = _patch("exchange", exchange)
    try:
        frame = research_data.get_research_data_before("BTC/USDT", "4h", 500, total_limit=2)
    finally:
        _patch("exchange", original)
    assert frame["time"].tolist() == [300, 400]


def test_network_retry_per_batch():
    calls = {"count": 0}
    sleeps = []
    exchange = _Exchange([])

    def fetch(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ccxt.NetworkError("temporary")
        return []

    exchange.fetch_ohlcv = fetch
    original_exchange = _patch("exchange", exchange)
    original_sleep = _patch("time", type("Clock", (), {"sleep": staticmethod(sleeps.append)})())
    try:
        research_data.get_research_data_before("BTC/USDT", "4h", 500, total_limit=2)
    finally:
        _patch("exchange", original_exchange)
        _patch("time", original_sleep)
    assert calls["count"] == 2
    assert sleeps == [2]


if __name__ == "__main__":
    tests = [test_boundary_is_strict_and_duplicates_removed, test_returns_last_total_limit, test_network_retry_per_batch]
    for test in tests:
        test()
    print(f"{len(tests)} research data before tests passed.")