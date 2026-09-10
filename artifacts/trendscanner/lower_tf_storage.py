"""Durable storage for the separate 1h/15m prospective experiment.

This module deliberately uses its own tables so the validated 4h experiment
and its state/event population cannot be changed by lower-timeframe work.
The first observation for each identity is baseline only: it seeds state but
never creates a signal event, even when READY=True. Only later False->True
transitions create immutable event rows.
"""
from __future__ import annotations

import json
import os

EVENTS_TABLE = "lower_tf_prospective_events"
STATE_TABLE = "lower_tf_prospective_state"
_BOOTSTRAP_LOCK_KEY = "lower_tf_prospective::schema_bootstrap"


def resolve_database_url(database_url=None):
    if database_url is not None:
        return database_url or None
    return os.environ.get("DATABASE_URL") or None


def observe(event, *, identity, ready, db_path=None, database_url=None):
    """Atomically record one READY state observation.

    Returns one of: BASELINE_READY, BASELINE_NOT_READY, NOT_READY,
    NO_TRANSITION, TRANSITION, STALE_OR_DUPLICATE.
    """
    if type(ready) is not bool:
        raise ValueError("INVALID_READY_STATE")
    if not isinstance(identity, tuple) or len(identity) != 5:
        raise ValueError("INVALID_IDENTITY")
    if ready and not isinstance(event, dict):
        raise ValueError("READY_EVENT_REQUIRED")

    url = resolve_database_url(database_url)
    if url:
        return _observe_postgres(event, identity=identity, ready=ready, database_url=url)
    if db_path is None:
        raise ValueError("DB_PATH_REQUIRED")
    return _observe_sqlite(event, identity=identity, ready=ready, db_path=db_path)


def _schema_sqlite(connection):
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
        experiment_version TEXT NOT NULL, symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL, direction TEXT NOT NULL,
        ready_timestamp INTEGER NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY (experiment_version, symbol, timeframe, direction, ready_timestamp)
    )""")
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
        experiment_version TEXT NOT NULL, symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL, direction TEXT NOT NULL,
        last_timestamp INTEGER NOT NULL, ready INTEGER NOT NULL,
        PRIMARY KEY (experiment_version, symbol, timeframe, direction)
    )""")
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS lower_tf_no_{operation.lower()}
            BEFORE {operation} ON {EVENTS_TABLE}
            BEGIN SELECT RAISE(ABORT, 'immutable prospective observation'); END""")


def _observe_sqlite(event, *, identity, ready, db_path):
    import sqlite3
    from pathlib import Path

    key = identity
    payload = json.dumps(event, sort_keys=True, allow_nan=False, separators=(",", ":")) if event else None
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path, timeout=5) as connection:
        _schema_sqlite(connection)
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            f"SELECT last_timestamp, ready FROM {STATE_TABLE} WHERE "
            "experiment_version=? AND symbol=? AND timeframe=? AND direction=?",
            key[:4],
        ).fetchone()

        if previous is None:
            connection.execute(
                f"INSERT INTO {STATE_TABLE} VALUES (?, ?, ?, ?, ?, ?)",
                (*key, int(ready)),
            )
            return {"status": "BASELINE_READY" if ready else "BASELINE_NOT_READY", "inserted": False}

        if key[4] <= previous[0]:
            return {"status": "STALE_OR_DUPLICATE", "inserted": False}

        connection.execute(
            f"UPDATE {STATE_TABLE} SET last_timestamp=?, ready=? WHERE "
            "experiment_version=? AND symbol=? AND timeframe=? AND direction=?",
            (key[4], int(ready), *key[:4]),
        )

        if not ready:
            return {"status": "NOT_READY", "inserted": False}
        if previous[1]:
            return {"status": "NO_TRANSITION", "inserted": False}

        cursor = connection.execute(
            f"INSERT OR IGNORE INTO {EVENTS_TABLE} VALUES (?, ?, ?, ?, ?, ?)",
            (*key, payload),
        )
        return {"status": "TRANSITION", "inserted": cursor.rowcount == 1}


def _connect_postgres(database_url):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("POSTGRES_DRIVER_MISSING") from exc
    return psycopg.connect(database_url, connect_timeout=5)


def _bootstrap_postgres(cur):
    cur.execute(
        f"SELECT to_regclass('{EVENTS_TABLE}') IS NOT NULL "
        f"AND to_regclass('{STATE_TABLE}') IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='lower_tf_immutable' "
        f"AND tgrelid=to_regclass('{EVENTS_TABLE}'))"
    )
    if cur.fetchone()[0]:
        return
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_BOOTSTRAP_LOCK_KEY,))
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} ("
        "experiment_version TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
        "direction TEXT NOT NULL, ready_timestamp BIGINT NOT NULL, payload TEXT NOT NULL, "
        "PRIMARY KEY (experiment_version, symbol, timeframe, direction, ready_timestamp))"
    )
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
        "experiment_version TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
        "direction TEXT NOT NULL, last_timestamp BIGINT NOT NULL, ready INTEGER NOT NULL, "
        "PRIMARY KEY (experiment_version, symbol, timeframe, direction))"
    )
    cur.execute(f"""DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'lower_tf_immutable'
              AND tgrelid = '{EVENTS_TABLE}'::regclass
        ) THEN
            CREATE OR REPLACE FUNCTION lower_tf_block_mutation() RETURNS trigger AS $f$
            BEGIN
                RAISE EXCEPTION 'immutable prospective observation';
            END;
            $f$ LANGUAGE plpgsql;
            CREATE TRIGGER lower_tf_immutable
                BEFORE UPDATE OR DELETE ON {EVENTS_TABLE}
                FOR EACH ROW EXECUTE FUNCTION lower_tf_block_mutation();
        END IF;
    END;
    $$;""")


def _observe_postgres(event, *, identity, ready, database_url):
    key = identity
    payload = json.dumps(event, sort_keys=True, allow_nan=False, separators=(",", ":")) if event else None

    with _connect_postgres(database_url) as connection:
        with connection.cursor() as cur:
            _bootstrap_postgres(cur)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (":".join(str(part) for part in key[:4]),))
            cur.execute(
                f"SELECT last_timestamp, ready FROM {STATE_TABLE} WHERE "
                "experiment_version=%s AND symbol=%s AND timeframe=%s AND direction=%s",
                key[:4],
            )
            previous = cur.fetchone()

            if previous is None:
                cur.execute(
                    f"INSERT INTO {STATE_TABLE} "
                    "(experiment_version, symbol, timeframe, direction, last_timestamp, ready) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (*key, int(ready)),
                )
                return {"status": "BASELINE_READY" if ready else "BASELINE_NOT_READY", "inserted": False}

            if key[4] <= previous[0]:
                return {"status": "STALE_OR_DUPLICATE", "inserted": False}

            cur.execute(
                f"UPDATE {STATE_TABLE} SET last_timestamp=%s, ready=%s WHERE "
                "experiment_version=%s AND symbol=%s AND timeframe=%s AND direction=%s",
                (key[4], int(ready), *key[:4]),
            )

            if not ready:
                return {"status": "NOT_READY", "inserted": False}
            if previous[1]:
                return {"status": "NO_TRANSITION", "inserted": False}

            cur.execute(
                f"INSERT INTO {EVENTS_TABLE} "
                "(experiment_version, symbol, timeframe, direction, ready_timestamp, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (experiment_version, symbol, timeframe, direction, ready_timestamp) DO NOTHING",
                (*key, payload),
            )
            return {"status": "TRANSITION", "inserted": cur.rowcount == 1}
