import hashlib
import hmac
import json
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import bybit_demo_executor as demo
import pytest


def test_demo_domain_is_hard_guarded():
    with pytest.raises(demo.SafetyError, match="DEMO_DOMAIN_REQUIRED"):
        demo.BybitDemoClient("key", "secret", base_url="https://api.bybit.com")


def test_credentials_are_required_even_for_demo():
    with pytest.raises(demo.SafetyError, match="DEMO_CREDENTIALS_REQUIRED"):
        demo.BybitDemoClient("", "")


def test_symbol_mapping_is_strict_usdt():
    assert demo.bybit_symbol("BTC/USDT") == "BTCUSDT"
    for invalid in ("BTCUSD", "btc/USDT", "BTC/USDC", "../BTC/USDT"):
        with pytest.raises(demo.SafetyError, match="UNSUPPORTED_SYMBOL"):
            demo.bybit_symbol(invalid)


def test_order_link_id_is_deterministic_unique_and_short():
    one = demo.order_link_id("BTC/USDT", 1_800_000_000_000)
    assert one == demo.order_link_id("BTC/USDT", 1_800_000_000_000)
    assert one != demo.order_link_id("ETH/USDT", 1_800_000_000_000)
    assert one != demo.order_link_id("BTC/USDT", 1_800_000_000_000, exit_order=True)
    assert len(one) <= 36


def test_order_plan_caps_risk_after_exchange_rounding():
    plan = demo.build_order_plan(
        symbol="BTCUSDT",
        reference_price="60000",
        equity="300",
        risk_pct="1",
        stop_pct="1.5",
        qty_step="0.001",
        min_qty="0.001",
        min_notional="5",
        tick_size="0.1",
    )
    assert plan.risk_amount == Decimal(3)
    assert plan.stop_price == Decimal(59100)
    assert plan.quantity == Decimal("0.003")
    assert plan.quantity * (plan.reference_price - plan.stop_price) <= plan.risk_amount
    assert plan.notional == Decimal("180.000")


def test_long_stop_rounds_toward_entry_not_away():
    plan = demo.build_order_plan(
        symbol="XRPUSDT",
        reference_price="1.003",
        equity="300",
        risk_pct="1",
        stop_pct="1.5",
        qty_step="1",
        min_qty="1",
        min_notional="5",
        tick_size="0.01",
    )
    assert plan.stop_price == Decimal("0.99")
    assert plan.quantity * (plan.reference_price - plan.stop_price) <= Decimal(3)


def test_order_plan_fails_when_exchange_minimum_exceeds_budget():
    with pytest.raises(demo.SafetyError, match="ORDER_BELOW_MIN_QTY"):
        demo.build_order_plan(
            symbol="BTCUSDT",
            reference_price="60000",
            equity="300",
            risk_pct="1",
            stop_pct="1.5",
            qty_step="1",
            min_qty="1",
            min_notional="5",
            tick_size="0.1",
        )


def test_config_requires_hour_aligned_start_and_valid_limits():
    demo.Config(start_ms=3_600_000)
    with pytest.raises(demo.SafetyError, match="START_MS_MUST_ALIGN_TO_1H"):
        demo.Config(start_ms=3_600_001)
    with pytest.raises(demo.SafetyError, match="INVALID_RISK_LIMITS"):
        demo.Config(start_ms=3_600_000, risk_pct=Decimal(4))


def test_hmac_get_signature_and_query_are_canonical():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(method=method, url=url, headers=headers, body=body, timeout=timeout)
        return {"retCode": 0, "result": {"list": []}}

    client = demo.BybitDemoClient(
        "api-key",
        "api-secret",
        transport=transport,
        clock_ms=lambda: 1_700_000_000_000,
    )
    client.request("GET", "/v5/order/realtime", {"symbol": "BTCUSDT", "category": "linear"})
    query = "category=linear&symbol=BTCUSDT"
    expected = hmac.new(
        b"api-secret",
        f"1700000000000api-key5000{query}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert captured["url"] == f"{demo.DEMO_BASE_URL}/v5/order/realtime?{query}"
    assert captured["headers"]["X-BAPI-SIGN"] == expected
    assert captured["body"] is None


def test_hmac_post_signs_exact_sorted_json_body():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(method=method, url=url, headers=headers, body=body)
        return {"retCode": 0, "result": {"orderId": "123"}}

    client = demo.BybitDemoClient(
        "api-key",
        "api-secret",
        transport=transport,
        clock_ms=lambda: 1_700_000_000_000,
    )
    result = client.request("POST", "/v5/order/create", {"symbol": "BTCUSDT", "category": "linear"})
    body = b'{"category":"linear","symbol":"BTCUSDT"}'
    expected = hmac.new(
        b"api-secret",
        b"1700000000000api-key5000" + body,
        hashlib.sha256,
    ).hexdigest()
    assert result == {"orderId": "123"}
    assert captured["body"] == body
    assert captured["headers"]["X-BAPI-SIGN"] == expected


def test_public_market_request_has_no_key_or_signature_headers():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(url=url, headers=headers)
        return {"retCode": 0, "result": {"list": [{"lastPrice": "10"}]}}

    client = demo.BybitDemoClient("key", "secret", transport=transport)
    assert client.ticker_price("BTCUSDT") == Decimal(10)
    assert "X-BAPI-API-KEY" not in captured["headers"]
    assert "X-BAPI-SIGN" not in captured["headers"]
    assert parse_qs(urlparse(captured["url"]).query) == {"category": ["linear"], "symbol": ["BTCUSDT"]}


def test_entry_is_market_long_with_attached_mark_price_stop():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(payload=json.loads(body))
        return {"retCode": 0, "result": {"orderId": "entry-1"}}

    client = demo.BybitDemoClient("key", "secret", transport=transport)
    plan = demo.OrderPlan(
        symbol="BTCUSDT",
        reference_price=Decimal(60000),
        stop_price=Decimal(59100),
        quantity=Decimal("0.003"),
        risk_amount=Decimal(3),
        notional=Decimal(180),
    )
    result = client.create_entry(plan, "tsd1-e-example")
    assert result["orderId"] == "entry-1"
    assert captured["payload"] == {
        "category": "linear",
        "orderLinkId": "tsd1-e-example",
        "orderType": "Market",
        "positionIdx": 0,
        "qty": "0.003",
        "side": "Buy",
        "slOrderType": "Market",
        "slTriggerBy": "MarkPrice",
        "stopLoss": "59100",
        "symbol": "BTCUSDT",
        "tpslMode": "Full",
    }


def test_exit_is_reduce_only_market_sell():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(payload=json.loads(body))
        return {"retCode": 0, "result": {"orderId": "exit-1"}}

    client = demo.BybitDemoClient("key", "secret", transport=transport)
    client.create_exit("BTCUSDT", Decimal("0.003"), "tsd1-x-example")
    assert captured["payload"]["side"] == "Sell"
    assert captured["payload"]["reduceOnly"] is True
    assert captured["payload"]["closeOnTrigger"] is True


def test_bybit_error_never_exposes_secret():
    def transport(method, url, headers, body, timeout):
        return {"retCode": 10003, "retMsg": "API key is invalid", "result": {}}

    client = demo.BybitDemoClient("public", "super-secret", transport=transport)
    with pytest.raises(demo.BybitAPIError) as caught:
        client.order("id")
    assert "super-secret" not in str(caught.value)


def test_stop_from_actual_fill_preserves_configured_distance_after_tick_rounding():
    stop = demo._stop_from_fill(Decimal("100.03"), Decimal("0.1"), Decimal("1.5"))
    assert stop == Decimal("98.6")
    assert Decimal("100.03") - stop <= Decimal("100.03") * Decimal("0.015")


class RecordingCursor:
    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


def test_fresh_event_query_is_exactly_1h_long_and_start_bounded():
    now = 100 * demo.TIMEFRAME_MS
    start = 99 * demo.TIMEFRAME_MS
    cursor = RecordingCursor(rows=[("BTC/USDT", "1h", "LONG", 99 * demo.TIMEFRAME_MS)])
    events = demo._load_fresh_events(cursor, now_ms=now, config=demo.Config(start_ms=start))
    sql, params = cursor.calls[0]
    assert "timeframe=%s AND direction=%s" in sql
    assert params[:3] == (demo.EXPERIMENT_VERSION, "1h", "LONG")
    assert events == [{
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "direction": "LONG",
        "ready_timestamp": 99 * demo.TIMEFRAME_MS,
    }]


def test_reservation_persists_twelve_bars_after_signal_close():
    ready = 100 * demo.TIMEFRAME_MS
    cursor = RecordingCursor()
    plan = demo.OrderPlan(
        symbol="BTCUSDT",
        reference_price=Decimal(60000),
        stop_price=Decimal(59100),
        quantity=Decimal("0.003"),
        risk_amount=Decimal(3),
        notional=Decimal(180),
    )
    demo._insert_reservation(
        cursor,
        {"symbol": "BTC/USDT", "ready_timestamp": ready},
        plan,
    )
    _sql, params = cursor.calls[0]
    signal_close = ready + demo.TIMEFRAME_MS
    assert params[6] == signal_close
    assert params[7] == signal_close + 12 * demo.TIMEFRAME_MS
    assert params[8] == demo.order_link_id("BTC/USDT", ready)
    assert params[9] == demo.order_link_id("BTC/USDT", ready, exit_order=True)


def test_position_map_rejects_short_or_hedged_positions():
    class Client:
        def positions(self):
            return [{"symbol": "BTCUSDT", "positionIdx": 2, "side": "Sell", "size": "1"}]

    with pytest.raises(demo.SafetyError, match="ONLY_ONE_WAY_LONG_POSITIONS_SUPPORTED"):
        demo._position_map(Client())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
