"""Durable storage contract tests: SQLite (regression) and PostgreSQL (mocked
DB-API, no real server needed). Same False->True/state contract both ways.
"""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

import shadow_storage
import market_regime_shadow as shadow
from test_market_regime_shadow import candidate, TARGET, TF, NOW


class Context:
    def __init__(self, target=TARGET, regime='MIXED'):
        self.target, self.regime = target, regime
    def snapshot(self):
        return {'shadow_status': 'OK', 'shadow_error': None,
                'market': {**dict.fromkeys(shadow.MARKET_FIELDS),
                           'market_regime': self.regime, 'volatility': 'NORMAL',
                           'target_4h_timestamp': self.target}}


def observe(db_path=None, database_url=None, *, timestamp, ready=True, **changes):
    return shadow.observe_ready(
        {**candidate(), 'ready_timestamp': timestamp, 'ready': ready, **changes},
        context=Context(timestamp), db_path=db_path, observed_at_ms=timestamp + TF + 123,
        database_url=database_url)


# ---------------------------------------------------------------------------
# Fake PostgreSQL DB-API: enough of the wire contract to exercise the exact
# SQL our backend issues, backed by plain dict "tables". A transaction works
# on a private copy that is only merged into the shared store on a clean
# __exit__, so a raised exception rolls back like a real connection.
# ---------------------------------------------------------------------------

class FakePostgresStore:
    def __init__(self):
        self.state = {}
        self.events = {}


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection
        self._result = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        text = " ".join(sql.split())
        state, events = self._connection.state, self._connection.events
        if text.startswith("CREATE TABLE") or text.startswith("DO $$"):
            return
        if "pg_advisory_xact_lock" in text:
            self._connection.locked_keys.append(params[0])
            return
        if text.startswith("SELECT last_timestamp, ready FROM"):
            self._result = state.get(tuple(params))
            return
        if text.startswith("SELECT ready_timestamp, 1 FROM"):
            matches = [ts for key, ts in events.keys_for(tuple(params))]
            self._result = (max(matches), 1) if matches else None
            return
        if text.startswith("SELECT payload FROM") and len(params) == 5:
            payload = events.get(tuple(params))
            self._result = (payload,) if payload is not None else None
            return
        if text.startswith("INSERT INTO") and "market_regime_shadow_state" in text:
            state[tuple(params[:4])] = (params[4], params[5])
            return
        if text.startswith("INSERT INTO") and "market_regime_shadow_events" in text:
            key = tuple(params[:5])
            if key in events:
                self.rowcount = 0
            else:
                events[key] = params[5]
                self.rowcount = 1
            return
        if "UPDATE" in text.split()[0] or ("UPDATE" in text and "market_regime_shadow_events" in text):
            raise RuntimeError("immutable prospective observation")
        if "DELETE" in text.split()[0] or ("DELETE" in text and "market_regime_shadow_events" in text):
            raise RuntimeError("immutable prospective observation")
        raise AssertionError(f"unexpected SQL in fake backend: {text}")

    def fetchone(self):
        return self._result


class _EventsView(dict):
    """Small helper so the fake cursor can query by the 4-part identity."""
    def keys_for(self, prefix):
        return [(k, k[4]) for k in self if k[:4] == prefix]


class FakeConnection:
    def __init__(self, store, fail_before_commit=False):
        self._store = store
        self._fail_before_commit = fail_before_commit
        self.locked_keys = []
        self.state = None
        self.events = None

    def __enter__(self):
        self.state = dict(self._store.state)
        events = _EventsView()
        events.update(self._store.events)
        self.events = events
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and not self._fail_before_commit:
            self._store.state = self.state
            self._store.events = dict(self.events)
        # Any exception (ours or the caller's) leaves the shared store untouched.
        return False

    def cursor(self):
        return FakeCursor(self)


def _patch_connect(monkeypatch, store, *, fail_before_commit=False, connect_error=None):
    def fake_connect(database_url):
        if connect_error is not None:
            raise connect_error
        return FakeConnection(store, fail_before_commit=fail_before_commit)
    monkeypatch.setattr(shadow_storage, "_connect_postgres", fake_connect)


# ---------------------------------------------------------------------------
# SQLite: unchanged behaviour through the new shadow_storage indirection.
# ---------------------------------------------------------------------------

def test_sqlite_transitions_unchanged(tmp_path):
    db = tmp_path / "events.db"
    assert not observe(db, timestamp=TARGET - TF, ready=False)["inserted"]
    first = observe(db, timestamp=TARGET)
    assert first["inserted"] and first["shadow_tag"] == "P3_MATCH"
    assert observe(db, timestamp=TARGET)["event"] == first["event"]
    assert not observe(db, timestamp=TARGET + TF)["inserted"]
    assert not observe(db, timestamp=TARGET, ready=False)["inserted"]  # stale
    assert not observe(db, timestamp=TARGET + 2 * TF, ready=False)["inserted"]
    second = observe(db, timestamp=TARGET + 3 * TF)
    assert second["inserted"]


# ---------------------------------------------------------------------------
# PostgreSQL (mocked): the same contract, exercised through the real SQL the
# backend issues against a fake DB-API connection/cursor.
# ---------------------------------------------------------------------------

def test_postgres_false_then_true_creates_one_event(monkeypatch):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    assert not observe(database_url="postgres://fake", timestamp=TARGET - TF, ready=False)["inserted"]
    result = observe(database_url="postgres://fake", timestamp=TARGET)
    assert result["inserted"] and result["shadow_tag"] == "P3_MATCH"


def test_postgres_true_then_true_creates_nothing(monkeypatch):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    assert observe(database_url="postgres://fake", timestamp=TARGET)["inserted"]
    assert not observe(database_url="postgres://fake", timestamp=TARGET + TF)["inserted"]


def test_postgres_false_resets_then_new_true_event(monkeypatch):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    assert observe(database_url="postgres://fake", timestamp=TARGET)["inserted"]
    assert not observe(database_url="postgres://fake", timestamp=TARGET + TF, ready=False)["inserted"]
    second = observe(database_url="postgres://fake", timestamp=TARGET + 2 * TF)
    assert second["inserted"]
    assert len(store.events) == 2


def test_postgres_stale_timestamp_ignored(monkeypatch):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    assert observe(database_url="postgres://fake", timestamp=TARGET + TF)["inserted"]
    # An older/equal timestamp arriving late must not roll state backward.
    assert not observe(database_url="postgres://fake", timestamp=TARGET, ready=False)["inserted"]
    assert not observe(database_url="postgres://fake", timestamp=TARGET + 2 * TF)["inserted"]  # still True->True


def test_postgres_duplicate_call_returns_same_event_no_new_row(monkeypatch):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    first = observe(database_url="postgres://fake", timestamp=TARGET)
    again = observe(database_url="postgres://fake", timestamp=TARGET)
    assert first["inserted"] and not again["inserted"]
    assert again["event"] == first["event"]
    assert len(store.events) == 1


def test_postgres_restart_preserves_state(monkeypatch):
    """Each observe() call opens a brand new FakeConnection bound to the same
    shared store, simulating a fresh process reading persisted state after a
    restart (nothing is cached in-process)."""
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    assert observe(database_url="postgres://fake", timestamp=TARGET)["inserted"]
    # New "process": a fresh call must still see ready=True and refuse True->True.
    assert not observe(database_url="postgres://fake", timestamp=TARGET + TF)["inserted"]


def test_postgres_event_row_immutable(monkeypatch):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    observe(database_url="postgres://fake", timestamp=TARGET)
    with FakeConnection(store) as connection:
        with connection.cursor() as cur:
            with pytest.raises(RuntimeError):
                cur.execute("UPDATE market_regime_shadow_events SET payload='x'", ())
            with pytest.raises(RuntimeError):
                cur.execute("DELETE FROM market_regime_shadow_events", ())


def test_postgres_transaction_rollback_on_failure(monkeypatch):
    """A crash after the event insert but before commit must not leave a
    partially-applied state row behind; a retry then behaves like the first
    attempt (still False->True)."""
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store, fail_before_commit=True)
    result = observe(database_url="postgres://fake", timestamp=TARGET)
    assert result["inserted"]  # the in-flight transaction computed an insert...
    assert store.state == {} and store.events == {}  # ...but it never committed

    _patch_connect(monkeypatch, store)  # real connection resumes normally
    retry = observe(database_url="postgres://fake", timestamp=TARGET)
    assert retry["inserted"] and retry["shadow_tag"] == "P3_MATCH"


def test_postgres_connection_failure_is_safe_unavailable(monkeypatch, tmp_path):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store, connect_error=ConnectionError("unreachable"))
    db_path = tmp_path / "should_not_be_created.db"
    result = observe(db_path, database_url="postgres://fake", timestamp=TARGET)
    assert result["shadow_status"] == "UNAVAILABLE" and not result["inserted"]
    assert not db_path.exists()  # no silent SQLite fallback


def test_postgres_driver_missing_is_safe_unavailable(monkeypatch):
    real_import = __import__

    def blocking_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("no module named psycopg")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(shadow_storage, "_connect_postgres", shadow_storage._connect_postgres)
    with patch("builtins.__import__", side_effect=blocking_import):
        result = observe(database_url="postgres://fake", timestamp=TARGET)
    assert result["shadow_status"] == "UNAVAILABLE" and not result["inserted"]


def test_database_url_env_var_selects_postgres(monkeypatch):
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    monkeypatch.setenv("DATABASE_URL", "postgres://from-env")
    result = observe(timestamp=TARGET)  # no explicit database_url override
    assert result["inserted"] and store.events


def test_no_database_url_uses_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = tmp_path / "events.db"
    result = observe(db, timestamp=TARGET)
    assert result["inserted"] and db.exists()
