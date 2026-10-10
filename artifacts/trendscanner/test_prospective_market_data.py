import json
import io
import os
import subprocess
import sys

import pytest

import prospective_market_data as data


def _row(stamp):
    return [str(stamp), "100", "102", "99", "101", "25", "2525"]


def _fake_api(monkeypatch, *, missing=None, duplicate=False, product="LinearPerpetual", extra_future=False):
    data._validate_instrument.cache_clear()
    calls = []

    def get(path, params):
        calls.append((path, dict(params)))
        if path.endswith("instruments-info"):
            return {"list": [{"symbol": params["symbol"], "status": "Trading",
                              "contractType": product, "quoteCoin": "USDT", "settleCoin": "USDT"}]}
        tf = int(params["interval"]) * 60_000
        last = params["end"] // tf * tf
        stamps = [last - index * tf for index in range(params["limit"])]
        if missing is not None:
            stamps = [stamp for stamp in stamps if stamp != missing]
        rows = [_row(stamp) for stamp in stamps]
        if duplicate:
            rows.append(rows[0])
        if extra_future:
            rows.append(_row(last + tf))
        return {"category": "linear", "symbol": params["symbol"], "list": rows}

    monkeypatch.setattr(data, "_get", get)
    return calls


@pytest.mark.parametrize("tf,step", [("15m", 900_000), ("1h", 3_600_000), ("4h", 14_400_000)])
def test_bybit_request_is_perpetual_and_strictly_before_close(monkeypatch, tf, step):
    calls = _fake_api(monkeypatch)
    boundary = 2000 * step
    frame = data.get_bybit_data_before("BTC/USDT", tf, boundary, 210)
    assert len(frame) == 210
    assert frame.time.tolist() == list(range(boundary - 210 * step, boundary, step))
    assert frame.iloc[-1].time == boundary - step
    assert frame.attrs["market_source"] == "bybit_linear"
    assert calls[1][1]["end"] == boundary - 1
    assert calls[1][1]["category"] == "linear"
    assert calls[1][1]["symbol"] == "BTCUSDT"
    assert frame.iloc[-1].volume == 25  # base-coin volume, not USDT turnover


def test_1213_row_market_context_paginates_backwards_without_overlap(monkeypatch):
    calls = _fake_api(monkeypatch)
    step = 14_400_000
    boundary = 2000 * step
    frame = data.get_bybit_data_before("ETC/USDT", "4h", boundary, 1213)
    pages = [params for path, params in calls if path.endswith("/kline")]
    assert [page["limit"] for page in pages] == [1000, 213]
    assert pages[1]["end"] == boundary - 1000 * step - 1
    assert len(frame) == 1213
    assert frame.time.is_unique


def test_missing_candle_fails_without_filling_or_switching_venue(monkeypatch):
    step = 900_000
    boundary = 2000 * step
    _fake_api(monkeypatch, missing=boundary - 3 * step)
    with pytest.raises(ValueError, match="NONEXACT_BYBIT_CANDLE_WINDOW"):
        data.get_bybit_data_before("ETC/USDT", "15m", boundary, 10)


@pytest.mark.parametrize("flag,error", [("duplicate", "DUPLICATE_BYBIT_CANDLE"), ("extra_future", "OUTSIDE_BOUNDARY")])
def test_duplicates_and_open_candles_are_rejected(monkeypatch, flag, error):
    _fake_api(monkeypatch, **{flag: True})
    with pytest.raises(ValueError, match=error):
        data.get_bybit_data_before("BTC/USDT", "1h", 2000 * 3_600_000, 10)


def test_wrong_product_is_rejected_before_fetching_candles(monkeypatch):
    calls = _fake_api(monkeypatch, product="LinearFutures")
    with pytest.raises(ValueError, match="BYBIT_USDT_PERPETUAL_REQUIRED"):
        data.get_bybit_data_before("BTC/USDT", "1h", 2000 * 3_600_000, 10)
    assert len(calls) == 1


@pytest.mark.parametrize("bad", ["BTCUSD", "btc/USDT", "BTC/USDC", "../BTC/USDT"])
def test_symbol_mapping_has_no_silent_alias_or_spot_fallback(bad):
    with pytest.raises(ValueError, match="UNSUPPORTED_BYBIT_SYMBOL"):
        data.bybit_symbol(bad)


def test_current_open_window_is_rejected_before_network(monkeypatch):
    monkeypatch.setattr(data.time, "time", lambda: 100 * 3600 + 100)
    with pytest.raises(ValueError, match="NOT_CLOSED"):
        data.get_bybit_data_before("BTC/USDT", "1h", 101 * 3_600_000, 10)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_volume_is_rejected(monkeypatch, value):
    _fake_api(monkeypatch)
    get = data._get

    def corrupt(path, params):
        result = get(path, params)
        if path.endswith("/kline"):
            result["list"][0][5] = str(value)
        return result

    monkeypatch.setattr(data, "_get", corrupt)
    with pytest.raises(ValueError, match="INVALID_BYBIT_OHLCV"):
        data.get_bybit_data_before("BTC/USDT", "1h", 2000 * 3_600_000, 10)


def test_venue_switch_isolates_experiment_state_and_collectors_in_fresh_processes():
    code = (
        "import json, prospective_market_data as d, lower_tf_shadow as s, "
        "prospective_lower_tf_collector as c; "
        "print(json.dumps([d.MARKET_SOURCE,s.EXPERIMENT_VERSION,d.FOUR_H_VERSION,"
        "c.get_research_data_before is d.get_research_data_before]))"
    )
    values = {}
    for source in ("kucoin_futures", "bybit_linear"):
        output = subprocess.check_output([sys.executable, "-c", code], env={
            **os.environ, "TREND_SCANNER_MARKET_SOURCE": source,
        }, text=True)
        values[source] = json.loads(output)
    assert values["kucoin_futures"] == ["kucoin_futures", data.KUCOIN_LOWER_TF_VERSION, data.KUCOIN_4H_VERSION, True]
    assert values["bybit_linear"] == ["bybit_linear", data.BYBIT_LOWER_TF_VERSION, data.BYBIT_4H_VERSION, True]


def test_invalid_source_fails_instead_of_using_kucoin():
    result = subprocess.run([sys.executable, "-c", "import prospective_market_data"], env={
        **os.environ, "TREND_SCANNER_MARKET_SOURCE": "bybt",
    }, text=True, capture_output=True)
    assert result.returncode != 0
    assert "UNSUPPORTED_MARKET_SOURCE" in result.stderr


@pytest.mark.parametrize("codes,success", [([10006, 0], True), ([10006] * 3, False)])
def test_rate_limit_retries_are_bounded_and_wait_for_reset(monkeypatch, codes, success):
    sleeps = []
    calls = []

    class Response(io.StringIO):
        headers = {"X-Bapi-Limit-Reset-Timestamp": "109000"}

    def open_request(request, timeout):
        calls.append(request.full_url)
        return Response(json.dumps({"retCode": codes[len(calls) - 1], "result": {"ok": True}}))

    monkeypatch.setattr(data.urllib.request, "urlopen", open_request)
    monkeypatch.setattr(data.time, "sleep", sleeps.append)
    monkeypatch.setattr(data.time, "time", lambda: 100)
    monkeypatch.setattr(data.time, "monotonic", lambda: 100)
    monkeypatch.setattr(data, "_last_request_at", 0)
    if success:
        assert data._get("/v5/market/kline", {}) == {"ok": True}
    else:
        with pytest.raises(ValueError, match="10006"):
            data._get("/v5/market/kline", {})
    assert len(calls) == len(codes)
    assert 10 in sleeps  # reset timestamp plus one-second margin


def test_forbidden_is_not_retried(monkeypatch):
    calls = []

    def forbidden(request, timeout):
        calls.append(request.full_url)
        raise data.urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(data.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(data.time, "sleep", lambda _: None)
    with pytest.raises(data.urllib.error.HTTPError):
        data._get("/v5/market/kline", {})
    assert len(calls) == 1
