from __future__ import annotations

import copy
import hashlib
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Mapping

from confidence import confidence_label
from context_trigger_shadow import build_shadow_context_trigger
from quality_pipeline import SIGNAL_THRESHOLD, STRONG_OPPOSITION_THRESHOLD


ANALYSIS_TABLE = "analysis_records"
SUPPORTED_TRIGGER_TIMEFRAMES = {"1h", "4h"}
HORIZONS = (3, 5, 10, 20)
MIN_FORMAL_SAMPLE_PER_CONTEXT = 20


FetchOHLCV = Callable[[str, str, int | None, int], list[list[Any]]]
NormalizeSymbol = Callable[[str], str]


@dataclass(frozen=True)
class TriggerLookupResult:
    found: bool
    index: int | None
    reason: str | None = None


@dataclass(frozen=True)
class EventExclusion:
    key: str
    reason: str


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(UTC)


def _parse_scan_timestamp(scan_timestamp: str | datetime) -> datetime:
    if isinstance(scan_timestamp, datetime):
        return _as_utc(scan_timestamp)

    if not isinstance(scan_timestamp, str):
        raise TypeError("scan_timestamp must be ISO string or datetime")

    parsed = datetime.fromisoformat(scan_timestamp.replace("Z", "+00:00"))
    return _as_utc(parsed)


def _safe_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _timeframe_ms(timeframe: str) -> int:
    if timeframe == "1h":
        return 3_600_000
    if timeframe == "4h":
        return 14_400_000
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _format_ts_compact(ts: datetime) -> str:
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def reconstruct_trigger_candle_timestamp(
    scan_timestamp: str | datetime,
    timeframe: str,
) -> datetime:
    """
    Reconstruct trigger candle timestamp from scan timestamp.

    Rules:
    - 1h: floor scan timestamp to hour, subtract 1 hour
    - 4h: floor scan timestamp to current 4h bucket, subtract 4 hours
    """
    if timeframe not in SUPPORTED_TRIGGER_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. Supported: {sorted(SUPPORTED_TRIGGER_TIMEFRAMES)}"
        )

    scan_dt = _parse_scan_timestamp(scan_timestamp)

    if timeframe == "1h":
        bucket = scan_dt.replace(minute=0, second=0, microsecond=0)
        return bucket - timedelta(hours=1)

    bucket_hour = (scan_dt.hour // 4) * 4
    bucket = scan_dt.replace(hour=bucket_hour, minute=0, second=0, microsecond=0)
    return bucket - timedelta(hours=4)


def resolve_trigger_candle_timestamp(
    record: Mapping[str, Any],
    timeframe: str,
    scan_timestamp: str | datetime,
) -> tuple[datetime, str, str]:
    """
    Resolve trigger timestamp for historical validation.

    Priority:
    1) persisted trigger_candle_ts in analytics row (if valid)
    2) reconstruction from scan timestamp (existing behavior)
    """
    persisted = record.get("trigger_candle_ts")
    persisted_raw = str(persisted).strip() if persisted is not None else ""
    if persisted_raw:
        try:
            return (
                _parse_scan_timestamp(persisted_raw),
                "PERSISTED_TRIGGER_CANDLE_TS",
                "analysis_records.trigger_candle_ts",
            )
        except Exception:
            # Invalid persisted value falls back to existing reconstruction path.
            pass

    return (
        reconstruct_trigger_candle_timestamp(scan_timestamp, timeframe),
        "RECONSTRUCTED_FROM_SCAN_TIMESTAMP",
        "analysis_records.timestamp_utc",
    )


def build_event_key(
    symbol: str,
    timeframe: str,
    direction: str,
    trigger_timestamp: str | datetime,
) -> str:
    if not symbol or not timeframe or not direction:
        raise ValueError("symbol, timeframe and direction are required")

    trigger_dt = _parse_scan_timestamp(trigger_timestamp)
    return "|".join(
        [
            str(symbol).upper().strip(),
            str(timeframe).strip(),
            str(direction).upper().strip(),
            _format_ts_compact(trigger_dt),
        ]
    )


def deduplicate_shadow_events(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """
    Deduplicate by event_key.

    Deterministic retained record policy:
    - minimum by (scan_timestamp_utc, scan_id, symbol, timeframe, direction)
    """
    event_list = [copy.deepcopy(dict(event)) for event in events]

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in event_list:
        key = event.get("event_key")
        if not isinstance(key, str) or not key:
            raise ValueError("Each event must include non-empty event_key")
        groups[key].append(event)

    def _sort_key(item: Mapping[str, Any]) -> tuple:
        return (
            str(item.get("scan_timestamp_utc") or ""),
            str(item.get("scan_id") or ""),
            str(item.get("symbol") or ""),
            str(item.get("timeframe") or ""),
            str(item.get("direction") or ""),
        )

    unique_events: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []

    for key in sorted(groups.keys()):
        records = groups[key]
        retained = sorted(records, key=_sort_key)[0]
        unique_events.append(retained)

        duplicate_groups.append(
            {
                "event_key": key,
                "count": len(records),
                "retained_scan_id": retained.get("scan_id"),
                "retained_scan_timestamp_utc": retained.get("scan_timestamp_utc"),
                "scan_ids": sorted(str(r.get("scan_id")) for r in records),
            }
        )

    return {
        "unique_events": unique_events,
        "duplicate_groups": duplicate_groups,
        "raw_count": len(event_list),
        "unique_count": len(unique_events),
    }


def _default_symbol_normalizer(symbol: str) -> str:
    from scanner import _to_futures_symbol

    return _to_futures_symbol(symbol)


def _default_fetch_ohlcv(symbol: str, timeframe: str, since: int | None, limit: int) -> list[list[Any]]:
    from scanner import exchange

    return exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)


def fetch_historical_candles_since(
    symbol: str,
    timeframe: str,
    since_ms: int,
    *,
    fetch_ohlcv: FetchOHLCV | None = None,
    symbol_normalizer: NormalizeSymbol | None = None,
    limit: int = 400,
    max_pages: int = 6,
) -> dict[str, Any]:
    if not isinstance(since_ms, int):
        raise TypeError("since_ms must be int milliseconds")

    fetcher = fetch_ohlcv or _default_fetch_ohlcv
    normalizer = symbol_normalizer or _default_symbol_normalizer

    exchange_symbol = normalizer(symbol)

    candles: list[list[Any]] = []
    cursor = since_ms

    for _ in range(max_pages):
        batch = fetcher(exchange_symbol, timeframe, cursor, limit)
        if not isinstance(batch, list) or not batch:
            break

        for row in batch:
            if not isinstance(row, list) or len(row) < 6:
                continue
            ts = int(row[0])
            if not candles or ts > int(candles[-1][0]):
                candles.append([ts, row[1], row[2], row[3], row[4], row[5]])

        if len(batch) < limit:
            break

        cursor = int(candles[-1][0]) + 1

    # Remove any duplicates and guarantee ascending order.
    dedup: dict[int, list[Any]] = {}
    for row in candles:
        dedup[int(row[0])] = row

    ordered = [dedup[k] for k in sorted(dedup.keys())]

    return {
        "symbol": symbol,
        "exchange_symbol": exchange_symbol,
        "timeframe": timeframe,
        "since_ms": since_ms,
        "candles": ordered,
    }


def split_closed_candles(
    candles: list[list[Any]],
    now_utc: datetime,
    timeframe: str,
) -> list[list[Any]]:
    if not candles:
        return []

    now_dt = _as_utc(now_utc)
    tf_ms = _timeframe_ms(timeframe)
    now_ms = int(now_dt.timestamp() * 1000)

    ordered = sorted(candles, key=lambda row: int(row[0]))
    last_open_ms = int(ordered[-1][0])

    # Candle is complete if its close time is <= now.
    last_close_ms = last_open_ms + tf_ms
    if last_close_ms > now_ms:
        return ordered[:-1]

    return ordered


def locate_trigger_candle(
    candles: list[list[Any]],
    trigger_timestamp: str | datetime,
) -> TriggerLookupResult:
    trigger_dt = _parse_scan_timestamp(trigger_timestamp)
    target_ms = int(trigger_dt.timestamp() * 1000)

    for idx, row in enumerate(candles):
        if int(row[0]) == target_ms:
            return TriggerLookupResult(found=True, index=idx, reason=None)

    return TriggerLookupResult(
        found=False,
        index=None,
        reason=f"Trigger candle {target_ms} not present in fetched candles",
    )


def _atr14_at_index(closed_candles: list[list[Any]], trigger_idx: int) -> float | None:
    if trigger_idx < 14:
        return None

    trs: list[float] = []
    start = trigger_idx - 13

    for i in range(start, trigger_idx + 1):
        high = _safe_float(closed_candles[i][2])
        low = _safe_float(closed_candles[i][3])
        prev_close = _safe_float(closed_candles[i - 1][4])
        if high is None or low is None or prev_close is None:
            return None

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        if not math.isfinite(tr):
            return None
        trs.append(tr)

    atr = sum(trs) / 14.0
    return atr if atr > 0 and math.isfinite(atr) else None


def calculate_forward_outcomes(
    closed_candles: list[list[Any]],
    trigger_index: int,
    direction: str,
    *,
    horizons: tuple[int, ...] = HORIZONS,
    entry_price: float | None = None,
    atr_value: float | None = None,
    atr_source: str = "RECONSTRUCTED_ATR14",
) -> dict[str, Any]:
    if trigger_index < 0 or trigger_index >= len(closed_candles):
        raise IndexError("trigger_index is out of bounds")

    direction_u = str(direction).upper()
    if direction_u not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    trigger_close = _safe_float(closed_candles[trigger_index][4])
    if trigger_close is None or trigger_close <= 0:
        raise ValueError("trigger close is missing or invalid")

    resolved_entry = float(entry_price) if entry_price is not None else trigger_close
    if resolved_entry <= 0:
        raise ValueError("entry_price must be positive")

    resolved_atr = _safe_float(atr_value)
    if resolved_atr is None:
        resolved_atr = _atr14_at_index(closed_candles, trigger_index)
        resolved_atr_source = "RECONSTRUCTED_ATR14"
    else:
        resolved_atr_source = atr_source

    future = closed_candles[trigger_index + 1 :]

    outcomes: dict[str, Any] = {
        "entry_price": resolved_entry,
        "entry_source": "TRIGGER_CLOSE" if entry_price is None else "STORED_ENTRY_PRICE",
        "atr": resolved_atr,
        "atr_source": resolved_atr_source,
        "horizons": {},
    }

    for horizon in horizons:
        if len(future) < horizon:
            outcomes["horizons"][horizon] = {
                "available": False,
                "reason": "INSUFFICIENT_FUTURE_CANDLES",
            }
            continue

        window = future[:horizon]
        close_h = _safe_float(window[-1][4])
        if close_h is None:
            outcomes["horizons"][horizon] = {
                "available": False,
                "reason": "INVALID_FORWARD_CLOSE",
            }
            continue

        highs = [_safe_float(row[2]) for row in window]
        lows = [_safe_float(row[3]) for row in window]
        if any(v is None for v in highs) or any(v is None for v in lows):
            outcomes["horizons"][horizon] = {
                "available": False,
                "reason": "INVALID_FORWARD_RANGE",
            }
            continue

        max_high = max(v for v in highs if v is not None)
        min_low = min(v for v in lows if v is not None)

        if direction_u == "LONG":
            directional_return_pct = ((close_h - resolved_entry) / resolved_entry) * 100.0
            favorable = max_high - resolved_entry
            adverse = resolved_entry - min_low
        else:
            directional_return_pct = ((resolved_entry - close_h) / resolved_entry) * 100.0
            favorable = resolved_entry - min_low
            adverse = max_high - resolved_entry

        favorable = max(0.0, favorable)
        adverse = max(0.0, adverse)

        mfe_atr = None
        mae_atr = None
        hit_plus_05 = None
        hit_plus_10 = None
        hit_minus_05 = None
        hit_minus_10 = None
        first_hit_pm1 = "UNAVAILABLE_ATR"

        if resolved_atr is not None and resolved_atr > 0:
            mfe_atr = favorable / resolved_atr
            mae_atr = adverse / resolved_atr

            hit_plus_05 = False
            hit_plus_10 = False
            hit_minus_05 = False
            hit_minus_10 = False

            first_hit_pm1 = "NONE"

            for row in window:
                high = _safe_float(row[2])
                low = _safe_float(row[3])
                if high is None or low is None:
                    continue

                if direction_u == "LONG":
                    plus_05 = high >= (resolved_entry + 0.5 * resolved_atr)
                    plus_10 = high >= (resolved_entry + 1.0 * resolved_atr)
                    minus_05 = low <= (resolved_entry - 0.5 * resolved_atr)
                    minus_10 = low <= (resolved_entry - 1.0 * resolved_atr)
                else:
                    plus_05 = low <= (resolved_entry - 0.5 * resolved_atr)
                    plus_10 = low <= (resolved_entry - 1.0 * resolved_atr)
                    minus_05 = high >= (resolved_entry + 0.5 * resolved_atr)
                    minus_10 = high >= (resolved_entry + 1.0 * resolved_atr)

                hit_plus_05 = hit_plus_05 or plus_05
                hit_plus_10 = hit_plus_10 or plus_10
                hit_minus_05 = hit_minus_05 or minus_05
                hit_minus_10 = hit_minus_10 or minus_10

                if first_hit_pm1 == "NONE":
                    if plus_10 and minus_10:
                        first_hit_pm1 = "BOTH_SAME_BAR"
                    elif plus_10:
                        first_hit_pm1 = "+1ATR"
                    elif minus_10:
                        first_hit_pm1 = "-1ATR"

        outcomes["horizons"][horizon] = {
            "available": True,
            "directional_return_pct": directional_return_pct,
            "mfe_atr": mfe_atr,
            "mae_atr": mae_atr,
            "hit_plus_0_5_atr": hit_plus_05,
            "hit_plus_1_0_atr": hit_plus_10,
            "hit_minus_0_5_atr": hit_minus_05,
            "hit_minus_1_0_atr": hit_minus_10,
            "first_hit_plus1_vs_minus1": first_hit_pm1,
        }

    return outcomes


def classify_outcome_by_context(
    unique_events: Iterable[Mapping[str, Any]],
    outcome_by_event_key: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {
        "SUPPORTS": [],
        "OPPOSES": [],
        "MIXED": [],
        "NEUTRAL": [],
        "UNAVAILABLE": [],
    }

    for event in unique_events:
        key = str(event["event_key"])
        context = str(event.get("context_class", "UNAVAILABLE")).upper()
        if context not in groups:
            context = "UNAVAILABLE"

        payload = {
            "event": dict(event),
            "outcomes": copy.deepcopy(dict(outcome_by_event_key.get(key, {}))),
        }
        groups[context].append(payload)

    return groups


def _median(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return float(median(vals)) if vals else None


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def _db_sha256(db_path: str | Path) -> str:
    path = Path(db_path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_analysis_records_readonly(db_path: str | Path) -> list[dict[str, Any]]:
    path = Path(db_path)
    uri = f"file:{path.resolve()}?mode=ro"

    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT *
            FROM {ANALYSIS_TABLE}
            ORDER BY timestamp_utc, scan_id, symbol, timeframe, direction
            """
        ).fetchall()

    return [dict(row) for row in rows]


def _directional_signal_proxy(record: Mapping[str, Any]) -> tuple[str, bool]:
    direction = str(record.get("direction") or "").upper()
    confidence = _safe_float(record.get("confidence"))

    breakout_confirmed = record.get("breakout_confirmed")
    is_breakout_confirmed = bool(breakout_confirmed) if breakout_confirmed is not None else False

    alignment = str(record.get("structure_alignment") or "").upper()
    structure_score = _safe_float(record.get("structure_score"))

    strong_opposition = (
        alignment == "OPPOSED"
        and structure_score is not None
        and structure_score < STRONG_OPPOSITION_THRESHOLD
    )

    if strong_opposition:
        return "WAIT", False

    if is_breakout_confirmed and confidence is not None and confidence >= SIGNAL_THRESHOLD:
        return direction, True

    return "WAIT", False


def _build_shadow_events_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    scan_map: dict[str, dict[str, dict[str, dict[str, dict[str, Any]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(dict))
    )

    for record in records:
        scan_id = str(record.get("scan_id"))
        symbol = str(record.get("symbol"))
        timeframe = str(record.get("timeframe"))
        direction = str(record.get("direction"))
        scan_map[scan_id][symbol][timeframe][direction] = record

    raw_events: list[dict[str, Any]] = []
    confluence_count = 0
    conflict_count = 0

    for scan_id in sorted(scan_map.keys()):
        symbols_map = scan_map[scan_id]

        for symbol in sorted(symbols_map.keys()):
            tf_map = symbols_map[symbol]
            symbol_result: dict[str, dict[str, Any]] = {}

            for timeframe in sorted(tf_map.keys()):
                dirs = tf_map[timeframe]
                tf_result: dict[str, Any] = {"quality": {}, "decision_details": {}}

                for direction in ("LONG", "SHORT"):
                    record = dirs.get(direction)
                    if not record:
                        continue

                    signal, confirmed = _directional_signal_proxy(record)
                    confidence = _safe_float(record.get("confidence"))
                    confidence_lbl = confidence_label(confidence) if confidence is not None else "LOW"

                    tf_result["quality"][direction] = {
                        "signal": signal,
                        "confirmed": confirmed,
                        "confidence": {
                            "confidence": confidence,
                            "label": confidence_lbl,
                        },
                        "breakout_quality": {
                            "breakout_score": _safe_float(record.get("breakout_score")),
                        },
                        "volume_quality": {
                            "volume_score": _safe_float(record.get("volume_score")),
                        },
                        "trend_quality": {
                            "trend_quality_score": _safe_float(record.get("trend_quality_score")),
                        },
                        "structure_quality": {
                            "alignment": record.get("structure_alignment") or "UNKNOWN",
                            "structure_score": _safe_float(record.get("structure_score")),
                        },
                    }

                    blockers: list[dict[str, str]] = []
                    if record.get("primary_blocker"):
                        blockers.append({"code": str(record["primary_blocker"])})

                    tf_result["decision_details"][direction] = {
                        "decision": record.get("decision") or "SKIP",
                        "decision_score": _safe_float(record.get("decision_score")),
                        "blockers": blockers,
                    }

                symbol_result[timeframe] = tf_result

            shadow = build_shadow_context_trigger(symbol_result)
            if shadow.get("trigger_confluence"):
                confluence_count += 1
            if shadow.get("trigger_conflict"):
                conflict_count += 1

            if not shadow.get("candidate"):
                continue

            trigger_tf = str(shadow.get("trigger_timeframe"))
            trigger_direction = str(shadow.get("direction"))
            trigger_record = tf_map.get(trigger_tf, {}).get(trigger_direction)
            if not trigger_record:
                continue

            scan_timestamp = str(trigger_record.get("timestamp_utc"))
            trigger_ts, ts_source, ts_source_field = resolve_trigger_candle_timestamp(
                trigger_record,
                trigger_tf,
                scan_timestamp,
            )
            event_key = build_event_key(symbol, trigger_tf, trigger_direction, trigger_ts)

            raw_events.append(
                {
                    "event_key": event_key,
                    "scan_id": scan_id,
                    "symbol": symbol,
                    "timeframe": trigger_tf,
                    "direction": trigger_direction,
                    "scan_timestamp_utc": _format_ts_compact(_parse_scan_timestamp(scan_timestamp)),
                    "trigger_candle_timestamp_utc": _format_ts_compact(trigger_ts),
                    "trigger_timestamp_source": ts_source,
                    "trigger_timestamp_source_field": ts_source_field,
                    "context_class": str(shadow.get("combined_context") or "UNAVAILABLE"),
                    "trigger_confluence": bool(shadow.get("trigger_confluence")),
                    "trigger_conflict": bool(shadow.get("trigger_conflict")),
                    "confidence": _safe_float(trigger_record.get("confidence")),
                    "breakout_score": _safe_float(trigger_record.get("breakout_score")),
                    "decision_score": _safe_float(trigger_record.get("decision_score")),
                    "production_decision": trigger_record.get("decision") or "SKIP",
                    "production_signal": _directional_signal_proxy(trigger_record)[0],
                    "entry_price": _safe_float(trigger_record.get("entry_price")),
                }
            )

    return {
        "raw_events": raw_events,
        "confluence_count": confluence_count,
        "conflict_count": conflict_count,
    }


def _stats_for_bucket(items: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(items)
    if n == 0:
        return {
            "sample_size": 0,
            "mean_directional_return_pct": None,
            "median_directional_return_pct": None,
            "mean_mfe_atr": None,
            "median_mfe_atr": None,
            "mean_mae_atr": None,
            "median_mae_atr": None,
            "hit_plus_1atr_rate": None,
            "hit_minus_1atr_rate": None,
            "first_hit_win_rate": None,
        }

    returns = [item.get("directional_return_pct") for item in items]
    mfe = [item.get("mfe_atr") for item in items]
    mae = [item.get("mae_atr") for item in items]

    plus_hits = [item.get("hit_plus_1_0_atr") for item in items if item.get("hit_plus_1_0_atr") is not None]
    minus_hits = [item.get("hit_minus_1_0_atr") for item in items if item.get("hit_minus_1_0_atr") is not None]

    first_hit_values = [item.get("first_hit_plus1_vs_minus1") for item in items]
    decisive = [v for v in first_hit_values if v in {"+1ATR", "-1ATR"}]
    first_hit_wins = sum(1 for v in decisive if v == "+1ATR")

    return {
        "sample_size": n,
        "mean_directional_return_pct": _mean(returns),
        "median_directional_return_pct": _median(returns),
        "mean_mfe_atr": _mean(mfe),
        "median_mfe_atr": _median(mfe),
        "mean_mae_atr": _mean(mae),
        "median_mae_atr": _median(mae),
        "hit_plus_1atr_rate": (sum(1 for v in plus_hits if v) / len(plus_hits)) if plus_hits else None,
        "hit_minus_1atr_rate": (sum(1 for v in minus_hits if v) / len(minus_hits)) if minus_hits else None,
        "first_hit_win_rate": (first_hit_wins / len(decisive)) if decisive else None,
    }


def _determine_evidence_state(
    outcome_rows: list[dict[str, Any]],
    grouped: Mapping[str, Mapping[int, list[dict[str, Any]]]],
) -> str:
    if not outcome_rows:
        return "NO_OUTCOME_DATA"

    # Formal sample requirement: each context with data should have >= MIN samples
    has_formal = False
    for context in ("SUPPORTS", "OPPOSES", "MIXED", "NEUTRAL", "UNAVAILABLE"):
        all_context_rows = []
        for horizon in HORIZONS:
            all_context_rows.extend(grouped.get(context, {}).get(horizon, []))
        if all_context_rows and len(all_context_rows) >= MIN_FORMAL_SAMPLE_PER_CONTEXT:
            has_formal = True

    # Early separation heuristic on H3/H5 mean returns.
    means = {}
    for context in ("SUPPORTS", "OPPOSES", "MIXED", "NEUTRAL", "UNAVAILABLE"):
        rows = []
        rows.extend(grouped.get(context, {}).get(3, []))
        rows.extend(grouped.get(context, {}).get(5, []))
        values = [r.get("directional_return_pct") for r in rows]
        means[context] = _mean(values)

    positive_contexts = [k for k, v in means.items() if v is not None and v > 0]
    negative_contexts = [k for k, v in means.items() if v is not None and v < 0]

    if has_formal:
        if positive_contexts and negative_contexts:
            return "SUFFICIENT_FOR_FORMAL_TESTING"
        return "NO_MEASURABLE_SEPARATION"

    if positive_contexts and negative_contexts:
        return "EARLY_SEPARATION"

    return "INSUFFICIENT_SAMPLE"


def build_historical_shadow_report(
    db_path: str | Path,
    *,
    now_utc: datetime | None = None,
    fetch_ohlcv: FetchOHLCV | None = None,
    symbol_normalizer: NormalizeSymbol | None = None,
) -> dict[str, Any]:
    """
    Build deterministic historical shadow outcome report.

    Isolation guarantees:
    - production logic is read-only via existing shadow module;
    - SQLite is opened in read-only mode;
    - no writes to DB or repository files.
    """
    now = _as_utc(now_utc) if now_utc is not None else datetime.now(UTC)

    db_hash_before = _db_sha256(db_path)
    records = read_analysis_records_readonly(db_path)
    db_hash_after = _db_sha256(db_path)

    raw_shadow = _build_shadow_events_from_records(records)
    raw_events = raw_shadow["raw_events"]

    dedup = deduplicate_shadow_events(raw_events)
    unique_events = dedup["unique_events"]

    outcome_by_event_key: dict[str, dict[str, Any]] = {}
    exclusions: list[EventExclusion] = []
    missing_trigger_count = 0
    fetch_failures = 0
    invalid_or_missing_atr = 0
    insufficient_horizons = {h: 0 for h in HORIZONS}

    for event in unique_events:
        event_key = str(event["event_key"])
        trigger_ts = _parse_scan_timestamp(event["trigger_candle_timestamp_utc"])
        trigger_ms = int(trigger_ts.timestamp() * 1000)
        timeframe = str(event["timeframe"])

        try:
            fetched = fetch_historical_candles_since(
                symbol=str(event["symbol"]),
                timeframe=timeframe,
                since_ms=trigger_ms - 120 * _timeframe_ms(timeframe),
                fetch_ohlcv=fetch_ohlcv,
                symbol_normalizer=symbol_normalizer,
            )
            candles = fetched["candles"]
        except Exception as exc:
            fetch_failures += 1
            exclusions.append(EventExclusion(event_key, f"FETCH_FAILURE:{exc}"))
            continue

        closed = split_closed_candles(candles, now, timeframe)
        lookup = locate_trigger_candle(closed, trigger_ts)
        if not lookup.found or lookup.index is None:
            missing_trigger_count += 1
            exclusions.append(EventExclusion(event_key, f"MISSING_TRIGGER:{lookup.reason}"))
            continue

        outcome = calculate_forward_outcomes(
            closed,
            lookup.index,
            str(event["direction"]),
            entry_price=_safe_float(event.get("entry_price")),
            atr_value=None,
            atr_source="RECONSTRUCTED_ATR14",
        )

        if outcome.get("atr") is None:
            invalid_or_missing_atr += 1

        for horizon, payload in outcome["horizons"].items():
            if not payload.get("available"):
                insufficient_horizons[horizon] += 1

        outcome_by_event_key[event_key] = outcome

    grouped = classify_outcome_by_context(unique_events, outcome_by_event_key)

    # Flatten available outcome rows for summaries.
    outcome_rows: list[dict[str, Any]] = []
    grouped_by_context_h: dict[str, dict[int, list[dict[str, Any]]]] = {
        ctx: {h: [] for h in HORIZONS}
        for ctx in ("SUPPORTS", "OPPOSES", "MIXED", "NEUTRAL", "UNAVAILABLE")
    }

    for context, event_payloads in grouped.items():
        for payload in event_payloads:
            event = payload["event"]
            outcomes = payload["outcomes"]
            horizons_map = outcomes.get("horizons", {}) if isinstance(outcomes, dict) else {}

            for horizon in HORIZONS:
                h_payload = horizons_map.get(horizon)
                if not isinstance(h_payload, Mapping) or not h_payload.get("available"):
                    continue

                row = {
                    "event_key": event["event_key"],
                    "context_class": context,
                    "horizon": horizon,
                    "directional_return_pct": h_payload.get("directional_return_pct"),
                    "mfe_atr": h_payload.get("mfe_atr"),
                    "mae_atr": h_payload.get("mae_atr"),
                    "hit_plus_1_0_atr": h_payload.get("hit_plus_1_0_atr"),
                    "hit_minus_1_0_atr": h_payload.get("hit_minus_1_0_atr"),
                    "first_hit_plus1_vs_minus1": h_payload.get("first_hit_plus1_vs_minus1"),
                }
                outcome_rows.append(row)
                grouped_by_context_h[context][horizon].append(row)

    by_context_horizon = {
        context: {
            horizon: _stats_for_bucket(grouped_by_context_h[context][horizon])
            for horizon in HORIZONS
        }
        for context in grouped_by_context_h
    }

    rows_count = len(records)
    scan_ids = sorted({str(r.get("scan_id")) for r in records if r.get("scan_id")})

    first_scan_ts = None
    last_scan_ts = None
    if records:
        timestamps = [str(r.get("timestamp_utc")) for r in records if r.get("timestamp_utc")]
        if timestamps:
            first_scan_ts = min(timestamps)
            last_scan_ts = max(timestamps)

    raw_context_counts = Counter(str(e.get("context_class") or "UNAVAILABLE") for e in raw_events)
    unique_context_counts = Counter(str(e.get("context_class") or "UNAVAILABLE") for e in unique_events)

    timeframe_counts = Counter(str(e.get("timeframe")) for e in unique_events)
    direction_counts = Counter(str(e.get("direction")) for e in unique_events)

    evidence_state = _determine_evidence_state(outcome_rows, grouped_by_context_h)

    return {
        "dataset": {
            "scan_count": len(scan_ids),
            "analysis_row_count": rows_count,
            "raw_trigger_count": dedup["raw_count"],
            "unique_event_count": dedup["unique_count"],
            "first_scan_timestamp_utc": first_scan_ts,
            "last_scan_timestamp_utc": last_scan_ts,
            "db_sha256_before": db_hash_before,
            "db_sha256_after": db_hash_after,
            "db_unchanged": db_hash_before == db_hash_after,
        },
        "deduplication": {
            "duplicate_group_count": sum(1 for g in dedup["duplicate_groups"] if g["count"] > 1),
            "duplicate_groups": dedup["duplicate_groups"],
            "retention_policy": "earliest (scan_timestamp_utc, scan_id, symbol, timeframe, direction)",
        },
        "context": {
            "raw_context_counts": dict(raw_context_counts),
            "unique_event_context_counts": dict(unique_context_counts),
            "timeframe_counts": dict(timeframe_counts),
            "direction_counts": dict(direction_counts),
            "confluence_count": raw_shadow["confluence_count"],
            "conflict_count": raw_shadow["conflict_count"],
        },
        "outcome_by_horizon_and_context": by_context_horizon,
        "integrity": {
            "missing_trigger_candle_count": missing_trigger_count,
            "fetch_failure_count": fetch_failures,
            "insufficient_horizon_counts": insufficient_horizons,
            "events_with_invalid_or_missing_atr": invalid_or_missing_atr,
            "excluded_events": [
                {"event_key": exclusion.key, "reason": exclusion.reason}
                for exclusion in exclusions
            ],
            "outcome_row_count": len(outcome_rows),
        },
        "verdict": {
            "evidence_state": evidence_state,
            "minimum_formal_sample_per_context": MIN_FORMAL_SAMPLE_PER_CONTEXT,
        },
        "raw_events": raw_events,
        "unique_events": unique_events,
    }
