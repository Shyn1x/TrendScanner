from __future__ import annotations

from unittest import mock

from analytics_scan_lifecycle import (
    ACTIVE_SCAN_ID_KEY,
    PENDING_NEW_SCAN_KEY,
    PERSISTED_SCAN_ID_KEY,
    SCAN_SYMBOLS_KEY,
    SESSION_UID_KEY,
    ensure_active_scan_id,
    request_new_scan,
)


def test_initial_scan_creates_session_and_active_scan_id() -> None:
    state: dict = {}

    scan_id = ensure_active_scan_id(state, ["BTC/USDT", "ETH/USDT"])

    assert isinstance(scan_id, str)
    assert scan_id
    assert state[ACTIVE_SCAN_ID_KEY] == scan_id
    assert SESSION_UID_KEY in state
    assert state[SCAN_SYMBOLS_KEY] == ("BTC/USDT", "ETH/USDT")


def test_initial_render_followed_by_immediate_rerun_keeps_one_scan_id() -> None:
    state: dict = {}

    with mock.patch(
        "analytics_scan_lifecycle.create_scan_id",
        side_effect=["scan-1", "scan-2"],
    ) as create_scan_id_mock:
        first = ensure_active_scan_id(state, ["BTC/USDT", "ETH/USDT"])
        second = ensure_active_scan_id(state, ["BTC/USDT", "ETH/USDT"])

    assert first == "scan-1"
    assert second == "scan-1"
    assert create_scan_id_mock.call_count == 1


def test_separate_simulated_sessions_cannot_collide() -> None:
    state_a: dict = {}
    state_b: dict = {}

    scan_a = ensure_active_scan_id(state_a, ["BTC/USDT"])
    scan_b = ensure_active_scan_id(state_b, ["BTC/USDT"])

    assert scan_a != scan_b
    assert state_a[SESSION_UID_KEY] != state_b[SESSION_UID_KEY]


def test_changed_symbol_selection_receives_new_scan_id() -> None:
    state: dict = {}

    with mock.patch(
        "analytics_scan_lifecycle.create_scan_id",
        side_effect=["scan-1", "scan-2"],
    ):
        first = ensure_active_scan_id(state, ["BTC/USDT", "ETH/USDT"])
        second = ensure_active_scan_id(state, ["BTC/USDT", "SOL/USDT"])

    assert first == "scan-1"
    assert second == "scan-2"
    assert first != second
    assert state[SCAN_SYMBOLS_KEY] == ("BTC/USDT", "SOL/USDT")


def test_manual_refresh_handler_followed_by_rerun_creates_one_new_scan_id_total() -> None:
    state: dict = {}

    with mock.patch(
        "analytics_scan_lifecycle.create_scan_id",
        side_effect=["scan-1", "scan-2", "scan-3"],
    ) as create_scan_id_mock:
        first = ensure_active_scan_id(state, ["BTC/USDT"])
        request_new_scan(state)
        assert state[PENDING_NEW_SCAN_KEY] is True
        second = ensure_active_scan_id(state, ["BTC/USDT"])
        third = ensure_active_scan_id(state, ["BTC/USDT"])

    assert first == "scan-1"
    assert second == "scan-2"
    assert third == "scan-2"
    assert first != second
    assert create_scan_id_mock.call_count == 2


def test_auto_refresh_handler_followed_by_rerun_creates_one_new_scan_id_total() -> None:
    state: dict = {}

    with mock.patch(
        "analytics_scan_lifecycle.create_scan_id",
        side_effect=["scan-1", "scan-2", "scan-3"],
    ) as create_scan_id_mock:
        first = ensure_active_scan_id(state, ["BTC/USDT"])
        request_new_scan(state)
        second = ensure_active_scan_id(state, ["BTC/USDT"])
        third = ensure_active_scan_id(state, ["BTC/USDT"])

    assert first == "scan-1"
    assert second == "scan-2"
    assert third == "scan-2"
    assert create_scan_id_mock.call_count == 2


def test_ordinary_widget_rerun_keeps_same_scan_id() -> None:
    state: dict = {}

    with mock.patch(
        "analytics_scan_lifecycle.create_scan_id",
        side_effect=["scan-1", "scan-2"],
    ) as create_scan_id_mock:
        first = ensure_active_scan_id(state, ["BTC/USDT"])
        second = ensure_active_scan_id(state, ["BTC/USDT"])

    assert first == "scan-1"
    assert second == "scan-1"
    assert first == second
    assert create_scan_id_mock.call_count == 1


def test_reordered_equivalent_selected_symbols_keep_same_scan_id() -> None:
    state: dict = {}

    with mock.patch(
        "analytics_scan_lifecycle.create_scan_id",
        side_effect=["scan-1", "scan-2"],
    ) as create_scan_id_mock:
        first = ensure_active_scan_id(state, ["ETH/USDT", "BTC/USDT"])
        second = ensure_active_scan_id(state, ["BTC/USDT", "ETH/USDT"])

    assert first == "scan-1"
    assert second == "scan-1"
    assert create_scan_id_mock.call_count == 1


def test_new_scan_clears_persisted_marker() -> None:
    state: dict = {}

    first = ensure_active_scan_id(state, ["BTC/USDT"])
    state[PERSISTED_SCAN_ID_KEY] = first

    request_new_scan(state)
    second = ensure_active_scan_id(state, ["BTC/USDT"])

    assert second != first
    assert state[PERSISTED_SCAN_ID_KEY] is None
