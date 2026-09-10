"""Synchronous post-READY adapter; no decision computation or transition state.

Every observer call completes before returning. PostgreSQL is the sole live
state/dedup authority. Statuses contain no credentials or exception payloads.
"""
from copy import deepcopy
import math
import os


def _valid(candidate, source):
    if not isinstance(candidate, dict) or not isinstance(source, dict):
        return False
    if candidate.get("timeframe") != "4h" or type(candidate.get("ready")) is not bool:
        return False
    timestamp = candidate.get("ready_timestamp")
    if type(timestamp) is not int or timestamp < 0 or timestamp != source.get("ready_timestamp"):
        return False
    direction = candidate.get("direction")
    if direction not in ("LONG", "SHORT") or not isinstance(candidate.get("symbol"), str) or not candidate["symbol"]:
        return False
    if source.get("_error") or source.get("trend") == "ERROR":
        return False
    quality = source.get("quality", {}).get(direction, {})
    decision = source.get("decision_details", {}).get(direction, {})
    if quality.get("analysis_available") is not True:
        return False
    if decision.get("decision") not in ("TAKE", "WATCH", "SKIP") or not isinstance(decision.get("blockers"), list):
        return False
    return all(type(candidate.get(key)) in (int, float) and math.isfinite(candidate[key])
               for key in ("confidence", "decision_score"))


def collect_observations(candidates, *, database_url, observer=None):
    """Synchronous collection, injectable for offline tests. Never uses SQLite."""
    result = {"status": "OK", "submitted": 0, "inserted": 0, "failed": 0}
    if not isinstance(database_url, str) or not database_url.strip():
        return {**result, "status": "DISABLED"}
    try:
        if observer is None:
            from market_regime_shadow import observe_ready
            observer = observe_ready
        for candidate in candidates:
            result["submitted"] += 1
            try:
                outcome = observer(deepcopy(candidate), database_url=database_url, db_path=None)
                if outcome.get("shadow_status") not in ("OK", "NOT_READY", "NO_TRANSITION"):
                    result["failed"] += 1
                elif outcome.get("inserted") is True:
                    result["inserted"] += 1
            except Exception:
                result["failed"] += 1
        if result["failed"]:
            result["status"] = "UNAVAILABLE"
    except Exception:
        result["status"] = "ERROR"
    return result


def observe_streamlit_results(evaluations, *, secrets_getter, environ_getter=None):
    """Called after render_ready_section evaluated READY once for the UI.

    Returns a sanitized final summary after all synchronous observer calls.
    Configuration is explicit and passed unchanged, never put into results.
    """
    try:
        get_env = environ_getter if environ_getter is not None else lambda: os.environ.get("DATABASE_URL")
        url = get_env()
        if not url:
            try:
                url = secrets_getter()
            except (KeyError, FileNotFoundError):
                url = None
        if not isinstance(url, str) or not url.strip():
            return {"status": "DISABLED"}
        if not url.startswith(("postgres://", "postgresql://")):
            return {"status": "UNAVAILABLE"}
        candidates = []
        for entry in evaluations:
            try:
                candidate, source = entry
                if _valid(candidate, source):
                    candidates.append(deepcopy(candidate))
            except Exception:
                continue  # Malformed/unknown is not a False observation.
        candidates.sort(key=lambda c: c["ready_timestamp"])
        if not candidates:
            return {"status": "NO_VALID_EVALUATIONS"}
        return collect_observations(candidates, database_url=url)
    except Exception:
        return {"status": "ERROR"}
