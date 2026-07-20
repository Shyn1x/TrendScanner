"""
product_helpers.py
~~~~~~~~~~~~~~~~~~
Product Layer helper functions — pure Python, no Streamlit import.

Public interface:
    get_local_datetime()                    -> datetime (timezone-aware)
    format_scan_timestamp(dt)               -> str  "19:58:22 MSK"
    build_product_diagnostic(results)       -> dict
    build_mobile_tf_card(tf, tf_data)       -> dict
    order_decision_factors(factors, dec)    -> list[dict]
    build_empty_state_summary(results, top) -> dict
    count_final_signals(results, symbols)   -> dict
    count_final_decisions(results, symbols) -> dict
"""

import math
from datetime import datetime, timezone
from settings import (
    DISPLAY_TIMEZONE,
    TOP_SETUPS_MIN_DECISION_SCORE,
)
from ui_helpers import (
    normalize_timeframe_result,
    normalize_decision_result,
    format_score_percent,
    decision_badge,
    summarize_decision_factors,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Timezone-aware timestamp
# ─────────────────────────────────────────────────────────────────────────────

def get_local_datetime() -> datetime:
    """
    Returns current timezone-aware datetime in DISPLAY_TIMEZONE.
    Falls back to UTC if the timezone cannot be loaded.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(DISPLAY_TIMEZONE))
    except Exception:
        return datetime.now(timezone.utc)


def format_scan_timestamp(dt) -> str:
    """
    Formats a datetime as "HH:MM:SS TZ" (e.g. "19:58:22 MSK").
    Returns "—" for invalid input.
    """
    try:
        if not isinstance(dt, datetime):
            return "—"
        time_str = dt.strftime("%H:%M:%S")
        tz_name  = dt.strftime("%Z") if dt.tzinfo is not None else "UTC"
        return f"{time_str} {tz_name}"
    except Exception:
        return "—"


# ─────────────────────────────────────────────────────────────────────────────
#  Signal / Decision counters (always from FINAL)
# ─────────────────────────────────────────────────────────────────────────────

def count_final_signals(all_results: dict, symbols: list) -> dict:
    """
    Counts LONG / SHORT / WAIT from FINAL.signal for each symbol.
    Symbols without a valid FINAL fall into WAIT.
    """
    counts = {"LONG": 0, "SHORT": 0, "WAIT": 0}
    valid  = {"LONG", "SHORT", "WAIT"}
    for sym in symbols:
        r = all_results.get(sym, {})
        if not isinstance(r, dict):
            counts["WAIT"] += 1
            continue
        final = r.get("FINAL", {})
        if not isinstance(final, dict):
            counts["WAIT"] += 1
            continue
        sig = str(final.get("signal", "WAIT")).upper().strip()
        counts[sig if sig in valid else "WAIT"] += 1
    return counts


def count_final_decisions(all_results: dict, symbols: list) -> dict:
    """
    Counts TAKE / WATCH / SKIP from FINAL.decision for each symbol.
    Symbols without a valid FINAL fall into SKIP.
    """
    counts = {"TAKE": 0, "WATCH": 0, "SKIP": 0}
    valid  = {"TAKE", "WATCH", "SKIP"}
    for sym in symbols:
        r = all_results.get(sym, {})
        if not isinstance(r, dict):
            counts["SKIP"] += 1
            continue
        final = r.get("FINAL", {})
        if not isinstance(final, dict):
            counts["SKIP"] += 1
            continue
        dec = str(final.get("decision", "SKIP")).upper().strip()
        counts[dec if dec in valid else "SKIP"] += 1
    return counts


# ─────────────────────────────────────────────────────────────────────────────
#  Product diagnostic
# ─────────────────────────────────────────────────────────────────────────────

def build_product_diagnostic(all_results: dict) -> dict:
    """
    Analyses all_results and returns a structured diagnostic dict.

    Returns
    -------
    {
        "symbols_total": int,
        "valid_results": int,
        "error_results": int,
        "final_signals":   {"LONG": int, "SHORT": int, "WAIT": int},
        "decisions":       {"TAKE": int, "WATCH": int, "SKIP": int},
        "decision_directions": {"LONG": int, "SHORT": int, "NONE": int},
        "watch_above_min_score": int,
        "excluded_reasons": {
            "skip":                  int,
            "watch_below_threshold": int,
            "direction_none":        int,   # WATCH excluded from Top Setups
            "invalid_data":          int,
        },
    }
    """
    symbols_total    = len(all_results)
    valid_results    = 0
    error_results    = 0
    final_signals    = {"LONG": 0, "SHORT": 0, "WAIT": 0}
    decisions        = {"TAKE": 0, "WATCH": 0, "SKIP": 0}
    decision_dirs    = {"LONG": 0, "SHORT": 0, "NONE": 0}
    watch_above      = 0
    exc_skip         = 0
    exc_watch_below  = 0
    exc_dir_none     = 0
    exc_invalid      = 0

    valid_sigs = {"LONG", "SHORT", "WAIT"}
    valid_decs = {"TAKE", "WATCH", "SKIP"}
    valid_dirs = {"LONG", "SHORT", "NONE"}

    for sym, result in all_results.items():
        if not isinstance(result, dict):
            exc_invalid += 1
            error_results += 1
            continue
        if "_error" in result:
            exc_invalid += 1
            error_results += 1
            continue
        final = result.get("FINAL", {})
        if not isinstance(final, dict):
            exc_invalid += 1
            error_results += 1
            continue

        valid_results += 1

        sig = str(final.get("signal", "WAIT")).upper().strip()
        final_signals[sig if sig in valid_sigs else "WAIT"] += 1

        dec = str(final.get("decision", "SKIP")).upper().strip()
        if dec not in valid_decs:
            dec = "SKIP"
        decisions[dec] += 1

        d_score = 0.0
        try:
            v = float(final.get("decision_score", 0.0))
            d_score = v if math.isfinite(v) else 0.0
        except (TypeError, ValueError):
            d_score = 0.0

        direction = str(final.get("decision_direction", "NONE")).upper().strip()
        decision_dirs[direction if direction in valid_dirs else "NONE"] += 1

        # Categorise Top Setups exclusion reasons
        if dec == "SKIP":
            exc_skip += 1
        elif dec == "WATCH":
            if d_score >= TOP_SETUPS_MIN_DECISION_SCORE:
                watch_above += 1
            else:
                exc_watch_below += 1
            if direction == "NONE":
                exc_dir_none += 1
        # TAKE with direction=NONE is excluded too — counted separately
        elif dec == "TAKE" and direction == "NONE":
            exc_dir_none += 1

    return {
        "symbols_total":   symbols_total,
        "valid_results":   valid_results,
        "error_results":   error_results,
        "final_signals":   final_signals,
        "decisions":       decisions,
        "decision_directions": decision_dirs,
        "watch_above_min_score": watch_above,
        "excluded_reasons": {
            "skip":                  exc_skip,
            "watch_below_threshold": exc_watch_below,
            "direction_none":        exc_dir_none,
            "invalid_data":          exc_invalid,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Mobile timeframe card
# ─────────────────────────────────────────────────────────────────────────────

def build_mobile_tf_card(tf: str, tf_data: dict) -> dict:
    """
    Builds display data for a single mobile-friendly timeframe card.

    Returns
    -------
    {
        "tf":               str,
        "signal":           str,
        "signal_icon":      str,
        "decision":         str,
        "decision_label":   str,
        "decision_score":   str,    # formatted percent
        "confidence":       str,    # formatted percent
        "confidence_label": str,
        "trend":            str,
        "badge":            dict,   # from decision_badge
        "should_expand":    bool,   # True if TAKE or WATCH
    }
    """
    if not isinstance(tf_data, dict):
        return {
            "tf": tf, "signal": "WAIT", "signal_icon": "⚪",
            "decision": "SKIP", "decision_label": "SKIP",
            "decision_score": "N/A", "confidence": "N/A",
            "confidence_label": "LOW", "trend": "—",
            "badge": decision_badge("SKIP", "NONE"),
            "should_expand": False,
        }

    norm = normalize_timeframe_result(tf_data)
    dec  = normalize_decision_result(tf_data)
    badge = decision_badge(dec["decision"], dec["decision_direction"])

    _SIGNAL_ICON = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪", "ERROR": "⚠️"}

    return {
        "tf":               tf,
        "signal":           norm["signal"],
        "signal_icon":      _SIGNAL_ICON.get(norm["signal"], "⚪"),
        "decision":         dec["decision"],
        "decision_label":   badge["label"],
        "decision_score":   format_score_percent(dec["decision_score"]),
        "confidence":       format_score_percent(norm["confidence"]),
        "confidence_label": norm["confidence_label"],
        "trend":            norm["trend"],
        "badge":            badge,
        "should_expand":    dec["decision"] in ("TAKE", "WATCH"),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Decision factor ordering
# ─────────────────────────────────────────────────────────────────────────────

def order_decision_factors(factors: dict, decision: str) -> list:
    """
    Returns factor sections in priority order based on decision type.

    TAKE  → Positive → Warnings → Blockers
    WATCH → Warnings → Positive → Blockers
    SKIP  → Blockers → Warnings → Positive

    Each item: {"title": str, "items": list[str], "css_class": str, "prefix": str}
    """
    _sections = {
        "positive": {
            "title": "✓ Positive",
            "items": factors.get("positive", []),
            "css_class": "reason-positive",
            "prefix": "✓",
        },
        "warnings": {
            "title": "⚠ Warnings",
            "items": factors.get("warnings", []),
            "css_class": "reason-warning",
            "prefix": "⚠",
        },
        "blockers": {
            "title": "✕ Blockers",
            "items": factors.get("blockers", []),
            "css_class": "reason-blocker",
            "prefix": "✕",
        },
    }

    dec = str(decision).upper().strip()
    if dec == "TAKE":
        order = ["positive", "warnings", "blockers"]
    elif dec == "WATCH":
        order = ["warnings", "positive", "blockers"]
    else:  # SKIP
        order = ["blockers", "warnings", "positive"]

    return [_sections[k] for k in order]


# ─────────────────────────────────────────────────────────────────────────────
#  Empty state summary
# ─────────────────────────────────────────────────────────────────────────────

def build_empty_state_summary(all_results: dict, top_setups: list) -> dict:
    """
    Builds a compact empty state summary when Top Setups is empty.

    Returns
    -------
    {
        "take_count":   int,
        "watch_count":  int,
        "most_common_blocker": str,   # "" if not found
    }
    """
    diag = build_product_diagnostic(all_results)
    take_count  = diag["decisions"]["TAKE"]
    watch_count = diag["decisions"]["WATCH"]

    # Collect decision_reasons from all SKIP/WATCH results
    from collections import Counter
    reasons: list[str] = []
    for result in all_results.values():
        if not isinstance(result, dict) or "_error" in result:
            continue
        final = result.get("FINAL", {})
        if not isinstance(final, dict):
            continue
        dec = str(final.get("decision", "SKIP")).upper().strip()
        if dec in ("SKIP", "WATCH"):
            r = str(final.get("decision_reason", "")).strip()
            if r:
                # Extract short phrase: first meaningful clause
                short = r.split(":")[0].strip() if ":" in r else r[:60].strip()
                if short:
                    reasons.append(short)

    most_common = ""
    if reasons:
        counter = Counter(reasons)
        most_common = counter.most_common(1)[0][0]

    return {
        "take_count":          take_count,
        "watch_count":         watch_count,
        "most_common_blocker": most_common,
    }
