"""Durable storage contract tests: SQLite (regression) and PostgreSQL (mocked
DB-API, no real server needed). Same False->True/state contract both ways.
"""
from copy import deepcopy
import inspect
import json
import threading
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
    """Shared 'database': tables plus a real lock registry, so tests can
    exercise genuine thread contention on advisory locks (not just a
    single-threaded call sequence).
    """
    def __init__(self):
        self.state = {}
        self.events = {}
        self.schema_ready = False
        self._lock_registry_guard = threading.Lock()
        self._lock_registry = {}

    def _lock_for(self, key):
        with self._lock_registry_guard:
            if key not in self._lock_registry:
                self._lock_registry[key] = threading.Lock()
            return self._lock_registry[key]


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
        if text.startswith("SELECT to_regclass"):
            self._result = (self._connection.schema_ready,)
            return
        if text.startswith("CREATE TABLE"):
            return
        if text.startswith("DO $$"):
            # Real bootstrap only reaches here once, past the lock+recheck.
            self._connection.schema_ready = True
            return
        if "pg_advisory_xact_lock" in text:
            self._connection.locked_keys.append(params[0])
            self._connection.acquire_lock(params[0])
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
        self._held_locks = []
        self.state = None
        self.events = None
        self.schema_ready = None

    def __enter__(self):
        self.state = dict(self._store.state)
        events = _EventsView()
        events.update(self._store.events)
        self.events = events
        self.schema_ready = self._store.schema_ready
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and not self._fail_before_commit:
                self._store.state = self.state
                self._store.events = dict(self.events)
                self._store.schema_ready = self.schema_ready
            # Any exception (ours or the caller's) leaves the shared store untouched.
        finally:
            # Advisory xact locks always release when the transaction ends,
            # commit or rollback - never held past this point.
            for lock in self._held_locks:
                lock.release()
            self._held_locks = []
        return False

    def cursor(self):
        return FakeCursor(self)

    def acquire_lock(self, key):
        lock = self._store._lock_for(key)
        lock.acquire()
        self._held_locks.append(lock)


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


# ---------------------------------------------------------------------------
# Bootstrap concurrency + trigger scoping (the three fixes from REJECT_STORAGE)
# ---------------------------------------------------------------------------

def test_bootstrap_lock_precedes_schema_ddl():
    """Manual SQL-order check: the fixed bootstrap lock must be acquired
    before any CREATE TABLE/FUNCTION/TRIGGER, so concurrent first callers
    serialize on DDL instead of racing on it."""
    source = inspect.getsource(shadow_storage._bootstrap_schema)
    lock_pos = source.index("pg_advisory_xact_lock")
    assert source.index("CREATE TABLE IF NOT EXISTS {EVENTS_TABLE}") > lock_pos
    assert source.index("CREATE OR REPLACE FUNCTION") > lock_pos
    assert source.index("CREATE TRIGGER market_shadow_immutable") > lock_pos


def test_trigger_check_scoped_to_events_table_by_relid():
    """Manual SQL check: both the fast existence check and the guarded
    CREATE TRIGGER filter by tgrelid on our exact table, not just tgname, so
    a same-named trigger on an unrelated table can never be mistaken for
    ours (real PostgreSQL is required to prove pg_trigger behavior at
    runtime; this pins the SQL text that would be sent)."""
    assert "tgrelid = to_regclass('market_regime_shadow_events')" in shadow_storage._SCHEMA_READY_SQL
    assert "::regclass" not in shadow_storage._SCHEMA_READY_SQL
    source = inspect.getsource(shadow_storage._bootstrap_schema)
    assert source.count("tgrelid = '{EVENTS_TABLE}'::regclass") == 1
    assert "tgname = 'market_shadow_immutable'" in source


def test_postgres_concurrent_first_bootstrap_different_symbols(monkeypatch):
    """Real OS threads racing through _persist_postgres for different
    symbols on a brand-new store: the shared bootstrap lock must serialize
    DDL so nobody crashes, and every symbol still gets its own event."""
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    symbols = [f"COIN{i}/USDT" for i in range(6)]
    results, errors = {}, []

    def run(symbol):
        try:
            payload = {**candidate(), "symbol": symbol, "ready_timestamp": TARGET, "ready": True}
            results[symbol] = shadow.observe_ready(
                payload, context=Context(TARGET), db_path=None,
                observed_at_ms=TARGET + TF + 123, database_url="postgres://fake")
        except Exception as exc:  # pragma: no cover - failure path under test
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(symbol,)) for symbol in symbols]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert store.schema_ready
    assert all(results[symbol]["inserted"] for symbol in symbols)
    assert len(store.events) == len(symbols)


def test_postgres_concurrent_same_identity_no_duplicate(monkeypatch):
    """Real thread contention on the *same* identity: the per-identity
    advisory lock must still serialize writers so exactly one event wins."""
    store = FakePostgresStore()
    _patch_connect(monkeypatch, store)
    outcomes = []
    outcomes_lock = threading.Lock()

    def run():
        result = observe(database_url="postgres://fake", timestamp=TARGET)
        with outcomes_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in outcomes if r["inserted"]) == 1
    assert len(store.events) == 1


def test_postgres_bootstrap_skips_lock_once_schema_ready(monkeypatch):
    """Steady state (schema already bootstrapped) must not pay the global
    bootstrap lock on every write - only the per-identity lock is taken."""
    store = FakePostgresStore()
    store.schema_ready = True
    connections = []

    def tracking_connect(database_url):
        connection = FakeConnection(store)
        connections.append(connection)
        return connection
    monkeypatch.setattr(shadow_storage, "_connect_postgres", tracking_connect)

    result = observe(database_url="postgres://fake", timestamp=TARGET)
    assert result["inserted"]
    assert shadow_storage._BOOTSTRAP_LOCK_KEY not in connections[-1].locked_keys
