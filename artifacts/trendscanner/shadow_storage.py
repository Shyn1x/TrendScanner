"""Durable storage backend for market_regime_shadow: SQLite (default, tests,
local dev) or PostgreSQL when DATABASE_URL is configured. Same event/state
contract either way: events are immutable, state advances only on
False->True (insert) and True/False resets, stale/duplicate timestamps never
roll state backward, and state+event writes are atomic per identity.

Not wired into app.py. No credentials are read from anywhere but the
environment (or an explicit override); nothing is written to the repository.
"""
from __future__ import annotations

import json
import os


EVENTS_TABLE = "market_regime_shadow_events"
STATE_TABLE = "market_regime_shadow_state"


def resolve_database_url(database_url=None):
    """Explicit override wins; otherwise DATABASE_URL from the environment."""
    if database_url is not None:
        return database_url or None
    return os.environ.get("DATABASE_URL") or None


def _key_for(event, identity):
    if identity is not None:
        return identity
    return (event["shadow_version"], event["symbol"], event["timeframe"],
            event["direction"], event["ready_timestamp"])


def persist(event, db_path, *, identity=None, database_url=None):
    """Same (inserted, event) contract regardless of backend.

    If DATABASE_URL is set but PostgreSQL is unreachable, this raises instead
    of silently falling back to SQLite; callers (observe_ready) turn that
    into a safe UNAVAILABLE result.
    """
    url = resolve_database_url(database_url)
    if url:
        return _persist_postgres(event, url, identity=identity)
    return _persist_sqlite(event, db_path, identity=identity)


def _persist_sqlite(event, db_path, *, identity=None):
    """Use the analytics SQLite file, with a separate immutable event table.

    Transactions + UNIQUE serialize concurrent processes as well as threads.
    First observation wins, including UNAVAILABLE; later refreshes never revise it.
    """
    import sqlite3
    from pathlib import Path

    # False observations reset state only; they are never event rows.
    is_ready = event is not None
    key = _key_for(event, identity)
    payload = json.dumps(event, sort_keys=True, allow_nan=False, separators=(",", ":"))
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=1) as connection:
        connection.execute(f"""CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
            shadow_version TEXT NOT NULL, symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL, direction TEXT NOT NULL,
            ready_timestamp INTEGER NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (shadow_version, symbol, timeframe, direction, ready_timestamp)
        )""")
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS market_shadow_no_{operation.lower()}
                BEFORE {operation} ON {EVENTS_TABLE}
                BEGIN SELECT RAISE(ABORT, 'immutable prospective observation'); END""")
        connection.execute(f"""CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
            shadow_version TEXT, symbol TEXT, timeframe TEXT, direction TEXT,
            last_timestamp INTEGER NOT NULL, ready INTEGER NOT NULL,
            PRIMARY KEY (shadow_version, symbol, timeframe, direction))""")
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            f"SELECT last_timestamp, ready FROM {STATE_TABLE} "
            "WHERE shadow_version=? AND symbol=? AND timeframe=? AND direction=?", key[:4]).fetchone()
        if previous is None:
            # Existing immutable events also seed state across checkpoint upgrades.
            previous = connection.execute(
                f"SELECT ready_timestamp, 1 FROM {EVENTS_TABLE} "
                "WHERE shadow_version=? AND symbol=? AND timeframe=? AND direction=? "
                "ORDER BY ready_timestamp DESC LIMIT 1", key[:4]).fetchone()
        if previous is not None and key[4] <= previous[0]:
            stored = connection.execute(
                f"SELECT payload FROM {EVENTS_TABLE} WHERE shadow_version=? "
                "AND symbol=? AND timeframe=? AND direction=? AND ready_timestamp=?", key).fetchone()
            return False, json.loads(stored[0]) if stored and is_ready else None
        connection.execute(f"INSERT OR REPLACE INTO {STATE_TABLE} VALUES (?, ?, ?, ?, ?, ?)",
                           (*key, int(is_ready)))
        if not is_ready or (previous is not None and previous[1]):
            return False, None
        cursor = connection.execute(f"""INSERT OR IGNORE INTO {EVENTS_TABLE}
            VALUES (?, ?, ?, ?, ?, ?)""", (
            event["shadow_version"], event["symbol"], event["timeframe"],
            event["direction"], event["ready_timestamp"], payload))
        inserted = cursor.rowcount == 1
        stored = connection.execute(f"""SELECT payload FROM {EVENTS_TABLE}
            WHERE shadow_version=? AND symbol=? AND timeframe=? AND direction=? AND ready_timestamp=?""",
            (event["shadow_version"], event["symbol"], event["timeframe"],
             event["direction"], event["ready_timestamp"])).fetchone()
        return inserted, json.loads(stored[0])


def _connect_postgres(database_url):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("POSTGRES_DRIVER_MISSING") from exc
    return psycopg.connect(database_url, connect_timeout=5)


def _persist_postgres(event, database_url, *, identity=None):
    """Mirrors `_persist_sqlite` exactly; only the SQL dialect differs.

    A transaction-scoped advisory lock keyed by the identity serializes
    concurrent writers the same way SQLite's BEGIN IMMEDIATE does. The
    connection context manager commits on success and rolls back on any
    exception, so a mid-transaction failure never leaves partial state.
    """
    is_ready = event is not None
    key = _key_for(event, identity)
    payload = json.dumps(event, sort_keys=True, allow_nan=False, separators=(",", ":"))

    with _connect_postgres(database_url) as connection:
        with connection.cursor() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
                shadow_version TEXT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, direction TEXT NOT NULL,
                ready_timestamp BIGINT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (shadow_version, symbol, timeframe, direction, ready_timestamp)
            )""")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                shadow_version TEXT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, direction TEXT NOT NULL,
                last_timestamp BIGINT NOT NULL, ready INTEGER NOT NULL,
                PRIMARY KEY (shadow_version, symbol, timeframe, direction)
            )""")
            cur.execute(f"""DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'market_shadow_immutable') THEN
                    CREATE FUNCTION market_shadow_block_mutation() RETURNS trigger AS $f$
                    BEGIN
                        RAISE EXCEPTION 'immutable prospective observation';
                    END;
                    $f$ LANGUAGE plpgsql;
                    CREATE TRIGGER market_shadow_immutable
                        BEFORE UPDATE OR DELETE ON {EVENTS_TABLE}
                        FOR EACH ROW EXECUTE FUNCTION market_shadow_block_mutation();
                END IF;
            END;
            $$;""")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (":".join(str(part) for part in key[:4]),))
            cur.execute(f"""SELECT last_timestamp, ready FROM {STATE_TABLE}
                WHERE shadow_version=%s AND symbol=%s AND timeframe=%s AND direction=%s""", key[:4])
            previous = cur.fetchone()
            if previous is None:
                # Existing immutable events also seed state across checkpoint upgrades.
                cur.execute(f"""SELECT ready_timestamp, 1 FROM {EVENTS_TABLE}
                    WHERE shadow_version=%s AND symbol=%s AND timeframe=%s AND direction=%s
                    ORDER BY ready_timestamp DESC LIMIT 1""", key[:4])
                previous = cur.fetchone()
            if previous is not None and key[4] <= previous[0]:
                cur.execute(f"""SELECT payload FROM {EVENTS_TABLE}
                    WHERE shadow_version=%s AND symbol=%s AND timeframe=%s
                    AND direction=%s AND ready_timestamp=%s""", key)
                stored = cur.fetchone()
                return False, json.loads(stored[0]) if stored and is_ready else None
            cur.execute(f"""INSERT INTO {STATE_TABLE}
                (shadow_version, symbol, timeframe, direction, last_timestamp, ready)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (shadow_version, symbol, timeframe, direction)
                DO UPDATE SET last_timestamp = EXCLUDED.last_timestamp, ready = EXCLUDED.ready""",
                (*key, int(is_ready)))
            if not is_ready or (previous is not None and previous[1]):
                return False, None
            cur.execute(f"""INSERT INTO {EVENTS_TABLE} VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (shadow_version, symbol, timeframe, direction, ready_timestamp) DO NOTHING""", (
                event["shadow_version"], event["symbol"], event["timeframe"],
                event["direction"], event["ready_timestamp"], payload))
            inserted = cur.rowcount == 1
            cur.execute(f"""SELECT payload FROM {EVENTS_TABLE}
                WHERE shadow_version=%s AND symbol=%s AND timeframe=%s
                AND direction=%s AND ready_timestamp=%s""", (
                event["shadow_version"], event["symbol"], event["timeframe"],
                event["direction"], event["ready_timestamp"]))
            stored = cur.fetchone()
            return inserted, json.loads(stored[0])
