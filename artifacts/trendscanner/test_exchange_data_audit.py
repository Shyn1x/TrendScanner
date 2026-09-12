import copy
from datetime import UTC, datetime

import ccxt
import pytest

from exchange_data_audit import audit_sources, compare_candles, contract_check, inspect_candles, native_timeframes, next_open

HOUR = 3_600_000


def candle(timestamp, close=100):
    return [timestamp, close, close + 1, close - 1, close, 10]


def market(**changes):
    return {"id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
            "settle": "USDT", "linear": True, "swap": True, "active": True, "contractSize": 1, **changes}


class Client:
    timeframes = {"1h": "60", "1M": "M"}
    options = {"timeframes": {"swap": {"1h": 60, "3m": None}}}

    def __init__(self, rows=None, markets=None, fail=None):
        self.rows = rows if rows is not None else [candle(2 * HOUR), candle(3 * HOUR), candle(4 * HOUR)]
        self.markets = markets if markets is not None else {"BTC/USDT:USDT": market()}
        self.fail = fail
        self.calls = []

    def load_markets(self):
        if self.fail == "markets":
            raise ccxt.NetworkError("unavailable")
        return self.markets

    def fetch_ohlcv(self, symbol, timeframe, since, limit, params):
        self.calls.append((symbol, timeframe, since, limit, params))
        if self.fail == "candles":
            raise ccxt.NetworkError("unavailable")
        return self.rows


def audit(left=None, right=None, **kwargs):
    return audit_sources({"kucoinfutures": left or Client(), "bybit": right or Client()},
                         **{"symbols": ["BTC/USDT"], "universe": ["BTC/USDT"], "timeframes": ["1h"],
                            "as_of_ms": 5 * HOUR, "limit": 3, **kwargs})


def test_only_closed_candles_sorted_without_mutating_input():
    rows = [candle(3 * HOUR), candle(HOUR), candle(2 * HOUR)]
    original = copy.deepcopy(rows)
    result = inspect_candles(rows, "1h", 3 * HOUR, 3)
    assert [row[0] for row in result["candles"]] == [HOUR, 2 * HOUR]
    assert result["open_rows_excluded"] == 1 and not result["stale"]
    assert rows == original


def test_monthly_closure_uses_calendar_including_leap_year():
    stamp = lambda value: int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)
    feb, march = stamp("2024-02-01"), stamp("2024-03-01")
    assert next_open(feb, "1M") == march
    assert inspect_candles([candle(feb)], "1M", march - 1, 1)["closed_rows"] == 0
    assert inspect_candles([candle(feb)], "1M", march, 1)["closed_rows"] == 1
    assert next_open(stamp("2024-12-01"), "1M") == stamp("2025-01-01")


def test_invalid_prices_and_conflicting_duplicates_are_excluded():
    result = inspect_candles([candle(HOUR), candle(HOUR), candle(HOUR, 101),
                              candle(2 * HOUR, float("nan")), [2 * HOUR, 100, 99, 98, 100, 10],
                              [True, 100, 101, 99, 100, 10]], "1h", 4 * HOUR, 3)
    assert result["candles"] == []
    assert result["invalid_rows"] == 3 and result["duplicate_rows"] == 2
    assert result["conflicting_timestamps"] == [HOUR]


def test_gaps_and_stale_data_are_visible():
    result = inspect_candles([candle(HOUR), candle(3 * HOUR)], "1h", 6 * HOUR, 3)
    assert result["gap_intervals"] == 1 and result["stale"]


@pytest.mark.parametrize("changes", [{"swap": False}, {"linear": False}, {"settle": "BTC"},
                                    {"quote": "USDC"}, {"active": False}, {"base": "ETH"}])
def test_spot_inverse_expired_or_other_underlying_are_rejected(changes):
    assert contract_check(market(**changes), "BTC/USDT")["status"] == "UNSUPPORTED_CONTRACT"


def test_only_matching_timestamps_are_compared_and_volume_is_not_equated():
    left = inspect_candles([candle(HOUR), candle(2 * HOUR)], "1h", 3 * HOUR, 2)
    right = inspect_candles([candle(2 * HOUR, 102)], "1h", 3 * HOUR, 2)
    result = compare_candles(left, right, 2)
    assert result["matched_candles"] == 1 and result["status"] == "INCOMPLETE"
    assert result["price_differences_bybit_vs_kucoin"]["close"]["max_abs_pct"] == pytest.approx(2)
    assert result["only_kucoin_timestamps"] == [HOUR]
    assert result["volume_comparison"].startswith("NOT_COMPARED")


def test_complete_audit_uses_explicit_futures_and_same_time_boundary():
    left, right = Client(), Client()
    result = audit(left, right)
    assert result["status"] == "COMPLETE"
    assert left.calls == [("BTC/USDT:USDT", "1h", 0, 5, {"to": 5 * HOUR})]
    assert right.calls == [("BTC/USDT:USDT", "1h", 0, 5, {"until": 5 * HOUR})]


def test_missing_instrument_is_reported_without_alias_guessing():
    left, right = Client(), Client(markets={})
    result = audit(left, right)
    assert result["status"] == "PARTIAL" and not right.calls
    assert result["comparisons"][0]["status"] == "NOT_COMPARABLE"


def test_kucoin_spot_monthly_interval_is_not_mistaken_for_futures_support():
    assert "1M" not in native_timeframes(ccxt.kucoinfutures(), "kucoinfutures")
    assert "1M" in native_timeframes(ccxt.bybit(), "bybit")
    left = Client()
    result = audit(left, timeframes=["1M"])
    assert not left.calls
    assert result["windows"]["kucoinfutures"]["BTC/USDT"]["1M"]["status"] == "UNSUPPORTED_NATIVE_TIMEFRAME"


def test_market_failure_is_blocked_not_empty_success():
    result = audit(Client(fail="markets"))
    assert result["status"] == "BLOCKED"
    assert result["exchanges"]["kucoinfutures"]["stage"] == "load_markets"
    assert result["comparisons"][0]["matched_candles"] == 0


def test_candle_failure_is_distinguished_from_missing_instrument():
    result = audit(Client(fail="candles"))
    assert result["status"] == "PARTIAL"
    assert result["windows"]["kucoinfutures"]["BTC/USDT"]["1h"]["stage"] == "fetch_ohlcv"


def test_invalid_arguments_fail_before_any_requests():
    client = Client()
    for kwargs in ({"limit": 0}, {"limit": 199}, {"as_of_ms": True}, {"timeframes": []}):
        with pytest.raises(ValueError):
            audit(client, **kwargs)
    assert not client.calls
