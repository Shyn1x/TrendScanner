from __future__ import annotations

import copy
from typing import Any

import ccxt

import research_data
from research_data import get_research_data


def test_futures_symbol_passed_to_fetch(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        seen["futures_symbol"] = futures_symbol
        seen["timeframe"] = timeframe
        seen["total_limit"] = total_limit
        return []

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)

    get_research_data("BTC/USDT", "4h", total_limit=1000)

    assert seen["futures_symbol"] == "BTC/USDT:USDT"


def test_total_limit_passed_unchanged(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        seen["total_limit"] = total_limit
        return []

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)

    get_research_data("BTC/USDT", "4h", total_limit=777)

    assert seen["total_limit"] == 777


def test_dataframe_has_expected_columns(monkeypatch) -> None:
    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        return [
            [1000, 1.0, 2.0, 0.5, 1.5, 100.0],
            [2000, 1.5, 2.5, 1.0, 2.0, 200.0],
        ]

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)

    df = get_research_data("BTC/USDT", "4h")

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 2


def test_empty_response_returns_empty_dataframe(monkeypatch) -> None:
    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        return []

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)

    df = get_research_data("BTC/USDT", "4h")

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 0


def test_input_symbol_not_mutated(monkeypatch) -> None:
    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        return []

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)

    symbol = "BTC/USDT"
    original = copy.deepcopy(symbol)

    get_research_data(symbol, "4h")

    assert symbol == original


def test_monthly_timeframe_raises_value_error() -> None:
    raised = False
    try:
        get_research_data("BTC/USDT", "1M")
    except ValueError:
        raised = True
    assert raised


def test_success_on_first_attempt(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        calls["count"] += 1
        return [[1000, 1.0, 2.0, 0.5, 1.5, 100.0]]

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)
    monkeypatch.setattr(research_data.time, "sleep", lambda seconds: None)

    df = get_research_data("BTC/USDT", "4h")

    assert calls["count"] == 1
    assert len(df) == 1


def test_timeout_then_success_on_second_attempt(monkeypatch) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        calls["count"] += 1
        if calls["count"] == 1:
            raise ccxt.RequestTimeout("timeout")
        return [[1000, 1.0, 2.0, 0.5, 1.5, 100.0]]

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)
    monkeypatch.setattr(research_data.time, "sleep", lambda seconds: sleeps.append(seconds))

    df = get_research_data("ETH/USDT", "4h")

    assert calls["count"] == 2
    assert len(df) == 1
    assert sleeps == [2]


def test_timeout_twice_then_success_on_third_attempt(monkeypatch) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        calls["count"] += 1
        if calls["count"] <= 2:
            raise ccxt.RequestTimeout("timeout")
        return [[1000, 1.0, 2.0, 0.5, 1.5, 100.0]]

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)
    monkeypatch.setattr(research_data.time, "sleep", lambda seconds: sleeps.append(seconds))

    df = get_research_data("ETH/USDT", "4h")

    assert calls["count"] == 3
    assert len(df) == 1
    assert sleeps == [2, 5]


def test_three_timeouts_raise_original_error(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        calls["count"] += 1
        raise ccxt.RequestTimeout("timeout")

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)
    monkeypatch.setattr(research_data.time, "sleep", lambda seconds: None)

    raised = False
    try:
        get_research_data("ETH/USDT", "4h")
    except ccxt.RequestTimeout:
        raised = True
    assert raised
    assert calls["count"] == 3


def test_value_error_not_retried(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fetch(futures_symbol: str, timeframe: str, total_limit: int) -> list:
        calls["count"] += 1
        raise ValueError("bad params")

    monkeypatch.setattr(research_data, "_fetch_recent_ohlcv", fake_fetch)
    monkeypatch.setattr(research_data.time, "sleep", lambda seconds: None)

    raised = False
    try:
        get_research_data("BTC/USDT", "4h")
    except ValueError:
        raised = True
    assert raised
    assert calls["count"] == 1


class _MonkeyPatch:
    """Minimal standalone monkeypatch helper (no pytest dependency)."""

    def __init__(self) -> None:
        self._restores: list[tuple[Any, str, Any]] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        self._restores.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, original in reversed(self._restores):
            setattr(target, name, original)
        self._restores.clear()


def _run_with_monkeypatch(test_fn) -> None:
    mp = _MonkeyPatch()
    try:
        test_fn(mp)
    finally:
        mp.undo()


if __name__ == "__main__":
    tests_with_monkeypatch = [
        test_futures_symbol_passed_to_fetch,
        test_total_limit_passed_unchanged,
        test_dataframe_has_expected_columns,
        test_empty_response_returns_empty_dataframe,
        test_input_symbol_not_mutated,
        test_success_on_first_attempt,
        test_timeout_then_success_on_second_attempt,
        test_timeout_twice_then_success_on_third_attempt,
        test_three_timeouts_raise_original_error,
        test_value_error_not_retried,
    ]

    for test in tests_with_monkeypatch:
        _run_with_monkeypatch(test)

    test_monthly_timeframe_raises_value_error()

    total = len(tests_with_monkeypatch) + 1
    print(f"{total} research data tests passed.")
