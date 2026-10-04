"""Fail-closed Bybit Demo executor for the validated prospective 1h LONG signal.

The module is intentionally isolated from the signal collector.  It reads only
immutable ``lower_tf_prospective_events`` rows, accepts only fresh 1h LONG
False->True transitions, and can talk only to Bybit's mainnet demo REST host.

Execution contract:

* virtual starting equity: 300 USDT plus reconciled closed PnL;
* risk per trade: 1% of virtual equity;
* fixed stop: 1.5% below the observed/filled entry;
* maximum three positions, 3% aggregate open risk, and 3x notional/equity;
* market exit after twelve complete 1h bars following the signal close;
* deterministic ``orderLinkId`` and a PostgreSQL journal for idempotency.

Nothing executes unless both ``--execute`` and ``BYBIT_DEMO_ENABLED=true`` are
present.  API credentials are read from environment variables and never logged.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

DEMO_BASE_URL = "https://api-demo.bybit.com"
EXPERIMENT_VERSION = "lower-tf-ready-v5-fixed200-catchup"
EVENTS_TABLE = "lower_tf_prospective_events"
TRADES_TABLE = "bybit_demo_strategy_trades"
EXECUTOR_VERSION = "bybit-demo-v1-1h-long-12bar-stop150"
TIMEFRAME = "1h"
DIRECTION = "LONG"
TIMEFRAME_MS = 3_600_000
HOLD_BARS = 12
DEFAULT_STARTING_EQUITY = Decimal(300)
DEFAULT_RISK_PCT = Decimal(1)
DEFAULT_STOP_PCT = Decimal("1.5")
DEFAULT_MAX_TOTAL_RISK_PCT = Decimal(3)
DEFAULT_MAX_OPEN_POSITIONS = 3
DEFAULT_MAX_LEVERAGE = Decimal(3)
DEFAULT_MAX_SIGNAL_AGE_MS = 90 * 60 * 1000
ORDER_PREFIX = "tsd1"
ACTIVE_STATUSES = {"RESERVED", "SUBMITTED", "OPEN", "EXIT_SUBMITTED"}


class SafetyError(RuntimeError):
    """Raised when an invariant fails and trading must stop."""


class BybitAPIError(RuntimeError):
    def __init__(self, code: int | str, message: str):
        super().__init__(f"BYBIT_{code}:{message}")
        self.code = int(code) if str(code).lstrip("-").isdigit() else code
        self.message = message


def _decimal(value: Any, label: str, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise SafetyError(f"INVALID_{label}") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise SafetyError(f"INVALID_{label}")
    return result


def _plain(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _step(value: Decimal, increment: Decimal, rounding: str) -> Decimal:
    if increment <= 0:
        raise SafetyError("INVALID_INCREMENT")
    units = (value / increment).to_integral_value(rounding=rounding)
    return units * increment


def bybit_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.endswith("/USDT"):
        raise SafetyError("UNSUPPORTED_SYMBOL")
    base = symbol[:-5]
    if not base or not base.isalnum() or not base.isupper():
        raise SafetyError("UNSUPPORTED_SYMBOL")
    return f"{base}USDT"


def order_link_id(symbol: str, ready_timestamp: int, *, exit_order: bool = False) -> str:
    identity = f"{EXECUTOR_VERSION}|{symbol}|{int(ready_timestamp)}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:20]
    suffix = "x" if exit_order else "e"
    value = f"{ORDER_PREFIX}-{suffix}-{digest}"
    if len(value) > 36:
        raise AssertionError("orderLinkId too long")
    return value


@dataclass(frozen=True)
class Config:
    start_ms: int
    starting_equity: Decimal = DEFAULT_STARTING_EQUITY
    risk_pct: Decimal = DEFAULT_RISK_PCT
    stop_pct: Decimal = DEFAULT_STOP_PCT
    max_total_risk_pct: Decimal = DEFAULT_MAX_TOTAL_RISK_PCT
    max_open_positions: int = DEFAULT_MAX_OPEN_POSITIONS
    max_leverage: Decimal = DEFAULT_MAX_LEVERAGE
    max_signal_age_ms: int = DEFAULT_MAX_SIGNAL_AGE_MS

    def __post_init__(self) -> None:
        if isinstance(self.start_ms, bool) or int(self.start_ms) <= 0:
            raise SafetyError("INVALID_START_MS")
        if self.start_ms % TIMEFRAME_MS:
            raise SafetyError("START_MS_MUST_ALIGN_TO_1H")
        if not 0 < self.risk_pct <= self.max_total_risk_pct <= 100:
            raise SafetyError("INVALID_RISK_LIMITS")
        if not 0 < self.stop_pct < 100:
            raise SafetyError("INVALID_STOP_PCT")
        if isinstance(self.max_open_positions, bool) or self.max_open_positions <= 0:
            raise SafetyError("INVALID_MAX_OPEN_POSITIONS")
        if self.max_leverage <= 0 or self.max_signal_age_ms <= 0:
            raise SafetyError("INVALID_LIMIT")


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    reference_price: Decimal
    stop_price: Decimal
    quantity: Decimal
    risk_amount: Decimal
    notional: Decimal


def build_order_plan(
    *,
    symbol: str,
    reference_price: Any,
    equity: Any,
    risk_pct: Any,
    stop_pct: Any,
    qty_step: Any,
    min_qty: Any,
    min_notional: Any,
    tick_size: Any,
) -> OrderPlan:
    """Size a LONG so the rounded protective stop risks at most the budget."""
    price = _decimal(reference_price, "REFERENCE_PRICE")
    balance = _decimal(equity, "EQUITY")
    risk = _decimal(risk_pct, "RISK_PCT")
    stop = _decimal(stop_pct, "STOP_PCT")
    quantity_step = _decimal(qty_step, "QTY_STEP")
    minimum_qty = _decimal(min_qty, "MIN_QTY", positive=False)
    minimum_notional = _decimal(min_notional, "MIN_NOTIONAL", positive=False)
    price_tick = _decimal(tick_size, "TICK_SIZE")

    risk_amount = balance * risk / Decimal(100)
    raw_stop = price * (Decimal(1) - stop / Decimal(100))
    # A LONG stop rounds upward, never farther away than the configured risk.
    stop_price = _step(raw_stop, price_tick, ROUND_CEILING)
    distance = price - stop_price
    if distance <= 0:
        raise SafetyError("STOP_COLLAPSED_TO_ENTRY")
    quantity = _step(risk_amount / distance, quantity_step, ROUND_FLOOR)
    if quantity <= 0 or quantity < minimum_qty:
        raise SafetyError("ORDER_BELOW_MIN_QTY")
    notional = quantity * price
    if notional < minimum_notional:
        raise SafetyError("ORDER_BELOW_MIN_NOTIONAL")
    if quantity * distance > risk_amount:
        raise AssertionError("rounded order exceeds risk budget")
    return OrderPlan(
        symbol=symbol,
        reference_price=price,
        stop_price=stop_price,
        quantity=quantity,
        risk_amount=risk_amount,
        notional=notional,
    )


Transport = Callable[[str, str, dict[str, str], bytes | None, float], dict[str, Any]]


def _urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise BybitAPIError(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise BybitAPIError("NETWORK", str(exc.reason)[:200]) from exc
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BybitAPIError("BAD_JSON", payload[:200]) from exc
    if not isinstance(result, dict):
        raise BybitAPIError("BAD_RESPONSE", "response is not an object")
    return result


class BybitDemoClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = DEMO_BASE_URL,
        transport: Transport = _urllib_transport,
        clock_ms: Callable[[], int] | None = None,
        timeout: float = 10.0,
    ) -> None:
        if base_url != DEMO_BASE_URL:
            raise SafetyError("DEMO_DOMAIN_REQUIRED")
        if not api_key or not api_secret:
            raise SafetyError("DEMO_CREDENTIALS_REQUIRED")
        self.api_key = api_key
        self._secret = api_secret.encode()
        self.base_url = base_url
        self._transport = transport
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> dict[str, Any]:
        if not path.startswith("/v5/"):
            raise SafetyError("INVALID_BYBIT_PATH")
        method = method.upper()
        values = {key: value for key, value in (params or {}).items() if value is not None}
        query = ""
        body_text = ""
        if method == "GET":
            query = urllib.parse.urlencode(sorted((key, str(value)) for key, value in values.items()))
        elif method == "POST":
            body_text = json.dumps(values, separators=(",", ":"), sort_keys=True)
        else:
            raise SafetyError("UNSUPPORTED_HTTP_METHOD")

        headers = {"Content-Type": "application/json"}
        if auth:
            timestamp = str(int(self._clock_ms()))
            recv_window = "5000"
            signed = timestamp + self.api_key + recv_window + (query if method == "GET" else body_text)
            signature = hmac.new(self._secret, signed.encode(), hashlib.sha256).hexdigest()
            headers.update({
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature,
            })
        url = self.base_url + path + (f"?{query}" if query else "")
        response = self._transport(
            method,
            url,
            headers,
            body_text.encode() if body_text else None,
            self.timeout,
        )
        code = response.get("retCode")
        if code != 0:
            raise BybitAPIError(code if code is not None else "MISSING_CODE", str(response.get("retMsg", ""))[:200])
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise BybitAPIError("BAD_RESULT", "result is not an object")
        return result

    def positions(self) -> list[dict[str, Any]]:
        result = self.request("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": 200})
        rows = result.get("list", [])
        if not isinstance(rows, list):
            raise BybitAPIError("BAD_POSITIONS", "list missing")
        return [row for row in rows if _decimal(row.get("size", "0"), "POSITION_SIZE", positive=False) > 0]

    def position(self, symbol: str) -> dict[str, Any] | None:
        result = self.request("GET", "/v5/position/list", {"category": "linear", "symbol": symbol})
        rows = result.get("list", [])
        active = [row for row in rows if _decimal(row.get("size", "0"), "POSITION_SIZE", positive=False) > 0]
        if len(active) > 1:
            raise SafetyError("HEDGE_MODE_NOT_SUPPORTED")
        return active[0] if active else None

    def ticker_price(self, symbol: str) -> Decimal:
        result = self.request("GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol}, auth=False)
        rows = result.get("list", [])
        if len(rows) != 1:
            raise SafetyError("TICKER_NOT_FOUND")
        return _decimal(rows[0].get("lastPrice"), "LAST_PRICE")

    def instrument(self, symbol: str) -> dict[str, Decimal]:
        result = self.request("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": symbol}, auth=False)
        rows = result.get("list", [])
        if len(rows) != 1:
            raise SafetyError("INSTRUMENT_NOT_FOUND")
        lot = rows[0].get("lotSizeFilter", {})
        price = rows[0].get("priceFilter", {})
        return {
            "qty_step": _decimal(lot.get("qtyStep"), "QTY_STEP"),
            "min_qty": _decimal(lot.get("minOrderQty", "0"), "MIN_QTY", positive=False),
            "min_notional": _decimal(lot.get("minNotionalValue", "0"), "MIN_NOTIONAL", positive=False),
            "tick_size": _decimal(price.get("tickSize"), "TICK_SIZE"),
        }

    def order(self, link_id: str) -> dict[str, Any] | None:
        result = self.request("GET", "/v5/order/realtime", {"category": "linear", "orderLinkId": link_id})
        rows = result.get("list", [])
        return rows[0] if rows else None

    def set_leverage(self, symbol: str, leverage: Decimal) -> None:
        try:
            self.request("POST", "/v5/position/set-leverage", {
                "category": "linear",
                "symbol": symbol,
                "buyLeverage": _plain(leverage),
                "sellLeverage": _plain(leverage),
            })
        except BybitAPIError as exc:
            if exc.code != 110043:  # leverage already set
                raise

    def create_entry(self, plan: OrderPlan, link_id: str) -> dict[str, Any]:
        return self.request("POST", "/v5/order/create", {
            "category": "linear",
            "symbol": plan.symbol,
            "side": "Buy",
            "orderType": "Market",
            "qty": _plain(plan.quantity),
            "positionIdx": 0,
            "orderLinkId": link_id,
            "stopLoss": _plain(plan.stop_price),
            "slTriggerBy": "MarkPrice",
            "tpslMode": "Full",
            "slOrderType": "Market",
        })

    def set_stop(self, symbol: str, stop_price: Decimal) -> None:
        self.request("POST", "/v5/position/trading-stop", {
            "category": "linear",
            "symbol": symbol,
            "tpslMode": "Full",
            "positionIdx": 0,
            "stopLoss": _plain(stop_price),
            "slTriggerBy": "MarkPrice",
            "slOrderType": "Market",
        })

    def create_exit(self, symbol: str, quantity: Decimal, link_id: str) -> dict[str, Any]:
        return self.request("POST", "/v5/order/create", {
            "category": "linear",
            "symbol": symbol,
            "side": "Sell",
            "orderType": "Market",
            "qty": _plain(quantity),
            "positionIdx": 0,
            "orderLinkId": link_id,
            "reduceOnly": True,
            "closeOnTrigger": True,
        })

    def closed_pnl(self, symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        result = self.request("GET", "/v5/position/closed-pnl", {
            "category": "linear",
            "symbol": symbol,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 100,
        })
        rows = result.get("list", [])
        if not isinstance(rows, list):
            raise BybitAPIError("BAD_CLOSED_PNL", "list missing")
        return rows


def _bootstrap(cur: Any) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {TRADES_TABLE} ("
        "executor_version TEXT NOT NULL, experiment_version TEXT NOT NULL, "
        "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
        "ready_timestamp BIGINT NOT NULL, signal_close_ms BIGINT NOT NULL, "
        "exit_due_ms BIGINT NOT NULL, order_link_id TEXT NOT NULL UNIQUE, "
        "exit_order_link_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, "
        "risk_amount NUMERIC NOT NULL, reference_price NUMERIC NOT NULL, "
        "quantity NUMERIC NOT NULL, stop_price NUMERIC NOT NULL, "
        "entry_order_id TEXT, exit_order_id TEXT, avg_entry_price NUMERIC, "
        "closed_pnl NUMERIC, submitted_ms BIGINT, last_error TEXT, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (executor_version, experiment_version, symbol, timeframe, direction, ready_timestamp), "
        "CHECK (status IN ('RESERVED','SUBMITTED','OPEN','EXIT_SUBMITTED','CLOSED','FAILED','SKIPPED')))"
    )


def _load_trades(cur: Any) -> list[dict[str, Any]]:
    cur.execute(
        f"SELECT symbol, ready_timestamp, signal_close_ms, exit_due_ms, order_link_id, "
        "exit_order_link_id, status, risk_amount, reference_price, quantity, stop_price, "
        "entry_order_id, exit_order_id, avg_entry_price, closed_pnl, submitted_ms "
        f"FROM {TRADES_TABLE} WHERE executor_version=%s ORDER BY ready_timestamp, symbol",
        (EXECUTOR_VERSION,),
    )
    keys = (
        "symbol", "ready_timestamp", "signal_close_ms", "exit_due_ms", "order_link_id",
        "exit_order_link_id", "status", "risk_amount", "reference_price", "quantity",
        "stop_price", "entry_order_id", "exit_order_id", "avg_entry_price", "closed_pnl",
        "submitted_ms",
    )
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def _load_fresh_events(cur: Any, *, now_ms: int, config: Config) -> list[dict[str, Any]]:
    earliest_close = max(config.start_ms, now_ms - config.max_signal_age_ms)
    latest_ready = now_ms - TIMEFRAME_MS
    earliest_ready = earliest_close - TIMEFRAME_MS
    cur.execute(
        f"SELECT symbol, timeframe, direction, ready_timestamp FROM {EVENTS_TABLE} "
        "WHERE experiment_version=%s AND timeframe=%s AND direction=%s "
        "AND ready_timestamp BETWEEN %s AND %s ORDER BY ready_timestamp, symbol",
        (EXPERIMENT_VERSION, TIMEFRAME, DIRECTION, earliest_ready, latest_ready),
    )
    events = []
    for symbol, timeframe, direction, ready_timestamp in cur.fetchall():
        timestamp = int(ready_timestamp)
        if timestamp % TIMEFRAME_MS:
            raise SafetyError("UNALIGNED_EVENT_TIMESTAMP")
        events.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "ready_timestamp": timestamp,
        })
    return events


def _update_trade(cur: Any, symbol: str, ready_timestamp: int, **changes: Any) -> None:
    allowed = {
        "status", "entry_order_id", "exit_order_id", "avg_entry_price", "quantity",
        "stop_price", "closed_pnl", "submitted_ms", "last_error",
    }
    if not changes or set(changes) - allowed:
        raise AssertionError("invalid trade update")
    assignments = ", ".join(f"{key}=%s" for key in changes)
    cur.execute(
        f"UPDATE {TRADES_TABLE} SET {assignments}, updated_at=now() "
        "WHERE executor_version=%s AND symbol=%s AND ready_timestamp=%s",
        (*changes.values(), EXECUTOR_VERSION, symbol, ready_timestamp),
    )
    if cur.rowcount != 1:
        raise SafetyError("TRADE_JOURNAL_UPDATE_FAILED")


def _insert_reservation(cur: Any, event: dict[str, Any], plan: OrderPlan) -> None:
    ready = int(event["ready_timestamp"])
    signal_close = ready + TIMEFRAME_MS
    cur.execute(
        f"INSERT INTO {TRADES_TABLE} (executor_version, experiment_version, symbol, timeframe, "
        "direction, ready_timestamp, signal_close_ms, exit_due_ms, order_link_id, "
        "exit_order_link_id, status, risk_amount, reference_price, quantity, stop_price) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'RESERVED',%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (
            EXECUTOR_VERSION, EXPERIMENT_VERSION, event["symbol"], TIMEFRAME, DIRECTION, ready,
            signal_close, signal_close + HOLD_BARS * TIMEFRAME_MS,
            order_link_id(event["symbol"], ready), order_link_id(event["symbol"], ready, exit_order=True),
            plan.risk_amount, plan.reference_price, plan.quantity, plan.stop_price,
        ),
    )
    if cur.rowcount != 1:
        raise SafetyError("DUPLICATE_TRADE_RESERVATION")


def _find_closed_pnl(client: BybitDemoClient, trade: dict[str, Any], now_ms: int) -> Decimal | None:
    submitted = int(trade.get("submitted_ms") or trade["signal_close_ms"])
    start = max(submitted - 60_000, now_ms - 7 * 24 * 60 * 60 * 1000)
    rows = client.closed_pnl(bybit_symbol(trade["symbol"]), start, now_ms)
    candidates = [row for row in rows if int(row.get("createdTime", 0) or 0) >= start]
    if trade.get("exit_order_id"):
        exact = [row for row in candidates if row.get("orderId") == trade["exit_order_id"]]
        if exact:
            candidates = exact
    if not candidates:
        return None
    latest = max(candidates, key=lambda row: int(row.get("updatedTime", 0) or 0))
    return _decimal(latest.get("closedPnl"), "CLOSED_PNL", positive=False)


def _position_map(client: BybitDemoClient) -> dict[str, dict[str, Any]]:
    result = {}
    for row in client.positions():
        if int(row.get("positionIdx", 0)) != 0 or row.get("side") != "Buy":
            raise SafetyError("ONLY_ONE_WAY_LONG_POSITIONS_SUPPORTED")
        symbol = row.get("symbol")
        if symbol in result:
            raise SafetyError("DUPLICATE_POSITION")
        result[symbol] = row
    return result


def _stop_from_fill(avg_price: Decimal, tick_size: Decimal, stop_pct: Decimal) -> Decimal:
    raw = avg_price * (Decimal(1) - stop_pct / Decimal(100))
    return _step(raw, tick_size, ROUND_CEILING)


def execute_cycle(connection: Any, client: BybitDemoClient, *, now_ms: int, config: Config) -> dict[str, Any]:
    """Reconcile exits first, then submit at most the fresh prospective events."""
    if now_ms < config.start_ms:
        raise SafetyError("EXECUTION_BEFORE_START")
    summary: dict[str, Any] = {"closed": 0, "exits_submitted": 0, "entries_submitted": 0, "skipped": {}}
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"{EXECUTOR_VERSION}:cycle",))
        try:
            _bootstrap(cur)
            connection.commit()
            trades = _load_trades(cur)
            active = [trade for trade in trades if trade["status"] in ACTIVE_STATUSES]
            positions = _position_map(client)
            tracked_symbols = {bybit_symbol(trade["symbol"]) for trade in active}
            untracked = sorted(set(positions) - tracked_symbols)
            if untracked:
                raise SafetyError(f"UNTRACKED_DEMO_POSITIONS:{','.join(untracked)}")

            unresolved_close = False
            for trade in active:
                api_symbol = bybit_symbol(trade["symbol"])
                position = positions.get(api_symbol)
                ready = int(trade["ready_timestamp"])

                if trade["status"] == "RESERVED":
                    existing = client.order(trade["order_link_id"])
                    if existing:
                        _update_trade(
                            cur, trade["symbol"], ready, status="SUBMITTED",
                            entry_order_id=existing.get("orderId"), submitted_ms=now_ms,
                        )
                        connection.commit()
                        trade["status"] = "SUBMITTED"
                        trade["entry_order_id"] = existing.get("orderId")
                        position = client.position(api_symbol)
                    else:
                        signal_age = now_ms - int(trade["signal_close_ms"])
                        if signal_age > config.max_signal_age_ms:
                            _update_trade(
                                cur, trade["symbol"], ready, status="SKIPPED",
                                last_error="STALE_RESERVED_WITHOUT_BYBIT_ORDER",
                            )
                            connection.commit()
                            trade["status"] = "SKIPPED"
                            continue
                        plan = OrderPlan(
                            symbol=api_symbol,
                            reference_price=_decimal(trade["reference_price"], "REFERENCE_PRICE"),
                            stop_price=_decimal(trade["stop_price"], "STOP_PRICE"),
                            quantity=_decimal(trade["quantity"], "QUANTITY"),
                            risk_amount=_decimal(trade["risk_amount"], "RISK_AMOUNT"),
                            notional=(
                                _decimal(trade["reference_price"], "REFERENCE_PRICE")
                                * _decimal(trade["quantity"], "QUANTITY")
                            ),
                        )
                        client.set_leverage(api_symbol, config.max_leverage)
                        response = client.create_entry(plan, trade["order_link_id"])
                        _update_trade(
                            cur, trade["symbol"], ready, status="SUBMITTED",
                            entry_order_id=response.get("orderId"), submitted_ms=now_ms,
                        )
                        connection.commit()
                        trade["status"] = "SUBMITTED"
                        trade["entry_order_id"] = response.get("orderId")
                        trade["submitted_ms"] = now_ms
                        summary["entries_submitted"] += 1
                        position = client.position(api_symbol)

                if position is None:
                    if trade["status"] == "SUBMITTED":
                        order = client.order(trade["order_link_id"])
                        order_status = (order or {}).get("orderStatus")
                        if order_status in {"Rejected", "Cancelled", "Deactivated"}:
                            _update_trade(cur, trade["symbol"], ready, status="FAILED", last_error=f"ENTRY_{order_status}")
                            connection.commit()
                        elif order_status == "Filled":
                            closed = _find_closed_pnl(client, trade, now_ms)
                            if closed is None:
                                unresolved_close = True
                            else:
                                _update_trade(cur, trade["symbol"], ready, status="CLOSED", closed_pnl=closed)
                                connection.commit()
                                trade["status"] = "CLOSED"
                                trade["closed_pnl"] = closed
                                summary["closed"] += 1
                        continue
                    closed = _find_closed_pnl(client, trade, now_ms)
                    if closed is None:
                        unresolved_close = True
                        continue
                    _update_trade(cur, trade["symbol"], ready, status="CLOSED", closed_pnl=closed)
                    connection.commit()
                    trade["status"] = "CLOSED"
                    trade["closed_pnl"] = closed
                    summary["closed"] += 1
                    continue

                avg_price = _decimal(position.get("avgPrice"), "AVG_ENTRY_PRICE")
                quantity = _decimal(position.get("size"), "POSITION_SIZE")
                instrument = client.instrument(api_symbol)
                exact_stop = _stop_from_fill(avg_price, instrument["tick_size"], config.stop_pct)
                current_stop = _decimal(position.get("stopLoss", "0"), "POSITION_STOP", positive=False)
                if current_stop != exact_stop:
                    client.set_stop(api_symbol, exact_stop)
                _update_trade(
                    cur, trade["symbol"], ready, status="OPEN", avg_entry_price=avg_price,
                    quantity=quantity, stop_price=exact_stop,
                )
                connection.commit()
                trade.update(status="OPEN", avg_entry_price=avg_price, quantity=quantity, stop_price=exact_stop)

                if now_ms >= int(trade["exit_due_ms"]):
                    existing_exit = client.order(trade["exit_order_link_id"])
                    if existing_exit:
                        response = existing_exit
                    else:
                        response = client.create_exit(api_symbol, quantity, trade["exit_order_link_id"])
                    _update_trade(
                        cur, trade["symbol"], ready, status="EXIT_SUBMITTED",
                        exit_order_id=response.get("orderId"),
                    )
                    connection.commit()
                    trade["status"] = "EXIT_SUBMITTED"
                    trade["exit_order_id"] = response.get("orderId")
                    summary["exits_submitted"] += 1

            if unresolved_close:
                raise SafetyError("CLOSED_POSITION_PNL_NOT_YET_RECONCILED")

            trades = _load_trades(cur)
            equity = config.starting_equity + sum(
                (_decimal(trade["closed_pnl"], "CLOSED_PNL", positive=False) for trade in trades if trade["closed_pnl"] is not None),
                Decimal(0),
            )
            if equity <= 0:
                raise SafetyError("VIRTUAL_EQUITY_DEPLETED")
            active = [trade for trade in trades if trade["status"] in ACTIVE_STATUSES]
            events = _load_fresh_events(cur, now_ms=now_ms, config=config)
            known = {(trade["symbol"], int(trade["ready_timestamp"])) for trade in trades}

            for event in events:
                identity = (event["symbol"], int(event["ready_timestamp"]))
                if identity in known:
                    continue
                if len(active) >= config.max_open_positions:
                    summary["skipped"][event["symbol"]] = "MAX_OPEN_POSITIONS"
                    continue
                if any(trade["symbol"] == event["symbol"] for trade in active):
                    summary["skipped"][event["symbol"]] = "DUPLICATE_SYMBOL"
                    continue
                reserved_risk = sum((_decimal(trade["risk_amount"], "RISK_AMOUNT") for trade in active), Decimal(0))
                next_risk = equity * config.risk_pct / Decimal(100)
                if reserved_risk + next_risk > equity * config.max_total_risk_pct / Decimal(100):
                    summary["skipped"][event["symbol"]] = "MAX_TOTAL_RISK"
                    continue

                api_symbol = bybit_symbol(event["symbol"])
                reference = client.ticker_price(api_symbol)
                instrument = client.instrument(api_symbol)
                plan = build_order_plan(
                    symbol=api_symbol,
                    reference_price=reference,
                    equity=equity,
                    risk_pct=config.risk_pct,
                    stop_pct=config.stop_pct,
                    **instrument,
                )
                open_notional = sum(
                    _decimal(trade["quantity"], "QUANTITY")
                    * _decimal(trade.get("avg_entry_price") or trade["reference_price"], "ENTRY_PRICE")
                    for trade in active
                )
                if open_notional + plan.notional > equity * config.max_leverage:
                    summary["skipped"][event["symbol"]] = "MAX_LEVERAGE"
                    continue

                _insert_reservation(cur, event, plan)
                connection.commit()
                link_id = order_link_id(event["symbol"], event["ready_timestamp"])
                try:
                    existing = client.order(link_id)
                    if existing:
                        response = existing
                    else:
                        client.set_leverage(api_symbol, config.max_leverage)
                        response = client.create_entry(plan, link_id)
                    _update_trade(
                        cur, event["symbol"], event["ready_timestamp"], status="SUBMITTED",
                        entry_order_id=response.get("orderId"), submitted_ms=now_ms,
                    )
                    connection.commit()
                except Exception as exc:
                    _update_trade(
                        cur, event["symbol"], event["ready_timestamp"], status="FAILED",
                        last_error=f"{type(exc).__name__}:{str(exc)[:180]}",
                    )
                    connection.commit()
                    raise
                active.append({
                    **event,
                    "status": "SUBMITTED",
                    "risk_amount": plan.risk_amount,
                    "quantity": plan.quantity,
                    "reference_price": plan.reference_price,
                })
                known.add(identity)
                summary["entries_submitted"] += 1

            summary["virtual_equity"] = _plain(equity)
            summary["active_trades"] = len([trade for trade in _load_trades(cur) if trade["status"] in ACTIVE_STATUSES])
            return summary
        finally:
            try:
                connection.rollback()
                with connection.cursor() as unlock_cur:
                    unlock_cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"{EXECUTOR_VERSION}:cycle",))
                connection.commit()
            except Exception:  # noqa: BLE001 - never mask the cycle's original failure
                connection.rollback()


def preflight(connection: Any, client: BybitDemoClient, *, now_ms: int, config: Config) -> dict[str, Any]:
    """Read-only connectivity and account-safety check."""
    with connection.cursor() as cur:
        cur.execute("SELECT to_regclass(%s), to_regclass(%s)", (EVENTS_TABLE, TRADES_TABLE))
        events_exists, trades_exists = cur.fetchone()
        if events_exists is None:
            raise SafetyError("PROSPECTIVE_EVENTS_TABLE_MISSING")
        fresh = _load_fresh_events(cur, now_ms=now_ms, config=config)
        trades = _load_trades(cur) if trades_exists is not None else []
    positions = _position_map(client)
    tracked = {bybit_symbol(trade["symbol"]) for trade in trades if trade["status"] in ACTIVE_STATUSES}
    untracked = sorted(set(positions) - tracked)
    if untracked:
        raise SafetyError(f"UNTRACKED_DEMO_POSITIONS:{','.join(untracked)}")
    return {
        "endpoint": DEMO_BASE_URL,
        "fresh_eligible_events": len(fresh),
        "tracked_active_trades": len(tracked),
        "demo_open_positions": len(positions),
        "start_ms": config.start_ms,
        "execute_enabled": False,
    }


def _config_from_env() -> Config:
    raw_start = os.environ.get("BYBIT_DEMO_START_MS", "")
    if not raw_start.isdigit():
        raise SafetyError("BYBIT_DEMO_START_MS_REQUIRED")
    return Config(start_ms=int(raw_start))


def _connect(database_url: str):
    try:
        import psycopg
    except ImportError as exc:
        raise SafetyError("POSTGRES_DRIVER_MISSING") from exc
    return psycopg.connect(database_url, connect_timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    api_key = os.environ.get("BYBIT_DEMO_API_KEY", "")
    api_secret = os.environ.get("BYBIT_DEMO_API_SECRET", "")
    if not database_url:
        raise SystemExit("FAIL: DATABASE_URL is required")
    config = _config_from_env()
    client = BybitDemoClient(api_key, api_secret)
    now_ms = int(time.time() * 1000)

    if args.execute and os.environ.get("BYBIT_DEMO_ENABLED", "").lower() != "true":
        raise SystemExit("FAIL: BYBIT_DEMO_ENABLED must be true")

    with _connect(database_url) as connection:
        result = (
            execute_cycle(connection, client, now_ms=now_ms, config=config)
            if args.execute
            else preflight(connection, client, now_ms=now_ms, config=config)
        )
    print(json.dumps(result, sort_keys=True, default=str))
    print("BYBIT DEMO EXECUTOR: PASS")


if __name__ == "__main__":
    main()
