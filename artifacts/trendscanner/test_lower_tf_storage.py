import json
import sqlite3

import lower_tf_storage as storage


def _identity(timestamp):
    return ("lower-tf-p3-v1-preregistered", "BTC/USDT", "15m", "LONG", timestamp)


def _event(timestamp):
    return {
        "experiment_version": "lower-tf-p3-v1-preregistered",
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "direction": "LONG",
        "ready_timestamp": timestamp,
    }


def test_first_true_is_baseline_not_event(tmp_path):
    db = tmp_path / "lower.db"
    result = storage.observe(_event(900_000), identity=_identity(900_000), ready=True, db_path=db)
    assert result == {"status": "BASELINE_READY", "inserted": False}

    with sqlite3.connect(db) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {storage.EVENTS_TABLE}").fetchone()[0] == 0
        assert connection.execute(f"SELECT ready FROM {storage.STATE_TABLE}").fetchone()[0] == 1


def test_false_then_true_creates_one_transition(tmp_path):
    db = tmp_path / "lower.db"
    assert storage.observe(None, identity=_identity(900_000), ready=False, db_path=db)["status"] == "BASELINE_NOT_READY"
    result = storage.observe(_event(1_800_000), identity=_identity(1_800_000), ready=True, db_path=db)
    assert result == {"status": "TRANSITION", "inserted": True}

    duplicate = storage.observe(_event(1_800_000), identity=_identity(1_800_000), ready=True, db_path=db)
    assert duplicate == {"status": "STALE_OR_DUPLICATE", "inserted": False}

    continuing = storage.observe(_event(2_700_000), identity=_identity(2_700_000), ready=True, db_path=db)
    assert continuing == {"status": "NO_TRANSITION", "inserted": False}

    assert storage.observe(None, identity=_identity(3_600_000), ready=False, db_path=db)["status"] == "NOT_READY"
    second = storage.observe(_event(4_500_000), identity=_identity(4_500_000), ready=True, db_path=db)
    assert second == {"status": "TRANSITION", "inserted": True}

    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            f"SELECT ready_timestamp, payload FROM {storage.EVENTS_TABLE} ORDER BY ready_timestamp"
        ).fetchall()
    assert [row[0] for row in rows] == [1_800_000, 4_500_000]
    assert json.loads(rows[0][1])["ready_timestamp"] == 1_800_000


def test_event_rows_are_immutable(tmp_path):
    db = tmp_path / "lower.db"
    storage.observe(None, identity=_identity(900_000), ready=False, db_path=db)
    storage.observe(_event(1_800_000), identity=_identity(1_800_000), ready=True, db_path=db)

    with sqlite3.connect(db) as connection:
        try:
            connection.execute(f"DELETE FROM {storage.EVENTS_TABLE}")
        except sqlite3.DatabaseError:
            blocked = True
        else:
            blocked = False
    assert blocked is True
