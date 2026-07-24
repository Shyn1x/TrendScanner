from __future__ import annotations

import uuid
from typing import Any, Iterable, MutableMapping

from strategy_analytics import create_scan_id


SESSION_UID_KEY = "analytics_session_uid"
ACTIVE_SCAN_ID_KEY = "analytics_active_scan_id"
SCAN_SYMBOLS_KEY = "analytics_scan_symbols_key"
PERSISTED_SCAN_ID_KEY = "analytics_persisted_scan_id"
PENDING_NEW_SCAN_KEY = "analytics_pending_new_scan"


def _normalize_symbol_set(symbols: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(symbol) for symbol in symbols}))


def request_new_scan(state: MutableMapping[str, Any]) -> None:
    if SESSION_UID_KEY not in state:
        state[SESSION_UID_KEY] = str(uuid.uuid4())

    state[PENDING_NEW_SCAN_KEY] = True


def ensure_active_scan_id(
    state: MutableMapping[str, Any],
    selected_symbols: Iterable[Any],
) -> str:
    """
    Ensure one globally unique scan ID per logical scan cycle.

    A new scan ID is created when:
    - session has no active scan yet;
    - a pending refresh request is consumed;
    - selected symbol set changed.
    """
    if SESSION_UID_KEY not in state:
        state[SESSION_UID_KEY] = str(uuid.uuid4())

    normalized_symbols = _normalize_symbol_set(selected_symbols)
    pending_new_scan = bool(state.pop(PENDING_NEW_SCAN_KEY, False))

    needs_new_scan = (
        pending_new_scan
        or state.get(ACTIVE_SCAN_ID_KEY) is None
        or state.get(SCAN_SYMBOLS_KEY) != normalized_symbols
    )

    if needs_new_scan:
        state[ACTIVE_SCAN_ID_KEY] = create_scan_id()
        state[SCAN_SYMBOLS_KEY] = normalized_symbols
        state[PERSISTED_SCAN_ID_KEY] = None

    return str(state[ACTIVE_SCAN_ID_KEY])
