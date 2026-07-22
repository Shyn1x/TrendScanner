"""
breakout_diagnostic_report.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Read-only diagnostics for breakout bottlenecks based on already-computed
pipeline results (all_results passed from the scan).

No trading logic is changed.
"""
from __future__ import annotations

import math
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional

from quality_pipeline import STRONG_OPPOSITION_THRESHOLD
from trendlines import line_value

_VALID_DIRECTIONS = ("LONG", "SHORT")


def _safe_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _summary_stats(values: Iterable[float]) -> Dict[str, Optional[float]]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"avg": None, "med": None}
    return {"avg": mean(vals), "med": median(vals)}


def build_breakout_diagnostic_report(all_results: Dict[str, Dict[str, dict]]) -> dict:
    """
    Builds a diagnostic report summarizing breakout bottlenecks.

    Input: all_results mapping symbol -> timeframe -> analysis dict
    (the same `all_results` structure used by the app). This function must
    NOT recalculate indicators or change trading logic; it reads already
    computed fields only.
    """
    totals = {
        "total_direction_analyses": 0,
        "data_unavailable": 0,
        "trendline_unavailable": 0,
        "breakout_crossed": 0,
        "breakout_not_crossed": 0,
        "breakout_score_ge_50": 0,
        "breakout_confirmed": 0,
        "decision_take": 0,
        "decision_watch": 0,
        "decision_skip": 0,
    }

    cross_scores: List[float] = []
    distance_scores: List[float] = []
    body_scores: List[float] = []
    wick_scores: List[float] = []
    final_scores: List[float] = []

    buckets = {
        "0-19.99": 0,
        "20-39.99": 0,
        "40-49.99": 0,
        "50-69.99": 0,
        "70-100": 0,
    }

    bottleneck_counts = {
        "NO_TRENDLINE": 0,
        "NOT_CROSSED": 0,
        "WEAK_DISTANCE": 0,
        "WEAK_BODY": 0,
        "LARGE_REJECTION_WICK": 0,
        "BREAKOUT_SCORE_BELOW_50": 0,
        "STRONG_STRUCTURE_OPPOSITION": 0,
        "CONFIDENCE_TOO_LOW": 0,
        "OTHER": 0,
    }

    rejected_candidates: List[dict] = []
    # Cross-state classification accumulators
    CROSS_STATES = (
        "FRESH_CROSS",
        "ALREADY_BEYOND_LINE",
        "NOT_REACHED_LINE",
        "CROSSED_BACK",
        "TOUCH_OR_EQUAL",
        "CROSSED_ON_LATEST_CANDLE",
        "UNKNOWN",
    )
    cross_counts: dict[str, int] = {k: 0 for k in CROSS_STATES}
    cross_by_tf: dict[str, dict[str, int]] = {}
    cross_by_dir: dict[str, dict[str, int]] = {"LONG": {k: 0 for k in CROSS_STATES}, "SHORT": {k: 0 for k in CROSS_STATES}}
    examples_already: List[dict] = []
    examples_crossed_latest: List[dict] = []

    # Iterate through symbols/timeframes/directions
    for symbol, tfs in (all_results or {}).items():
        if not isinstance(tfs, dict):
            continue
        for tf, tf_result in tfs.items():
            if tf == "FINAL":
                continue
            totals_dir = 0
            # tf_result may be non-dict when errors
            if not isinstance(tf_result, dict):
                # count both directions as data unavailable
                for _ in _VALID_DIRECTIONS:
                    totals["total_direction_analyses"] += 1
                    totals["data_unavailable"] += 1
                    bottleneck_counts["NO_TRENDLINE"] += 1
                continue

            for direction in _VALID_DIRECTIONS:
                totals["total_direction_analyses"] += 1

                quality = tf_result.get("quality") or {}
                dir_quality = quality.get(direction) if isinstance(quality, dict) else None
                if not isinstance(dir_quality, dict):
                    totals["data_unavailable"] += 1
                    bottleneck_counts["NO_TRENDLINE"] += 1
                    continue

                # trendline present?
                if dir_quality.get("line") is None:
                    totals["trendline_unavailable"] += 1
                    bottleneck_counts["NO_TRENDLINE"] += 1
                    # still attempt to collect decision field
                    detail = (tf_result.get("decision_details") or {}).get(direction) or {}
                    dec = detail.get("decision")
                    if dec == "TAKE":
                        totals["decision_take"] += 1
                    elif dec == "WATCH":
                        totals["decision_watch"] += 1
                    else:
                        totals["decision_skip"] += 1
                    continue

                bq = dir_quality.get("breakout_quality") or {}
                # components and scores
                comps = bq.get("components") or {}
                cross = _safe_float(comps.get("cross"))
                dist_score = _safe_float(comps.get("close_distance"))
                body_score = _safe_float(comps.get("candle_body"))
                wick_score = _safe_float(comps.get("rejection_wick"))
                final_score = _safe_float(bq.get("breakout_score"))

                if cross is not None:
                    cross_scores.append(cross)
                if dist_score is not None:
                    distance_scores.append(dist_score)
                if body_score is not None:
                    body_scores.append(body_score)
                if wick_score is not None:
                    wick_scores.append(wick_score)
                if final_score is not None:
                    final_scores.append(final_score)

                # totals
                crossed = bool(cross and cross > 0.0)
                if crossed:
                    totals["breakout_crossed"] += 1
                else:
                    totals["breakout_not_crossed"] += 1

                # --- Cross-state classification ---------------------------------
                # Attempt to obtain closes and lines. Prefer explicit fields
                # if present in the breakout_quality payload; otherwise use
                # what we can (signal_close + line + signal_index).
                sig_idx = None
                try:
                    sig_idx = int(bq.get("signal_index")) if bq.get("signal_index") is not None else None
                except Exception:
                    sig_idx = None

                # line dict if present (trendline)
                line_obj = dir_quality.get("line") if isinstance(dir_quality, dict) else None

                # closes: prefer explicit prev/ signal/ latest if provided by pipeline
                prev_close_val = _safe_float(bq.get("prev_close"))
                sig_close_val = _safe_float(bq.get("signal_close"))
                latest_close_val = _safe_float(bq.get("latest_close"))

                # lines at positions: try compute via line_value if line dict available
                prev_line_val = None
                sig_line_val = None
                latest_line_val = None
                if line_obj and isinstance(line_obj, dict) and sig_idx is not None:
                    try:
                        prev_line_val = float(line_value(line_obj, sig_idx - 1))
                        sig_line_val = float(line_value(line_obj, sig_idx))
                        latest_line_val = float(line_value(line_obj, sig_idx + 1))
                    except Exception:
                        prev_line_val = sig_line_val = latest_line_val = None

                # as fallback, use stored line_price for signal
                if sig_line_val is None:
                    sig_line_val = _safe_float(bq.get("line_price"))

                # ATR for distance units
                atr_val = _safe_float(bq.get("atr"))

                # Decide state using available numeric values. We will pick
                # FRESH_CROSS when component cross > 0 (as computed in pipeline)
                state = "UNKNOWN"
                try:
                    # Use stored cross component if available
                    if isinstance(bq.get("components"), dict) and _safe_float(bq.get("components").get("cross")) is not None:
                        bq_cross_flag = _safe_float(bq.get("components").get("cross")) > 0.0
                    else:
                        bq_cross_flag = None

                    # If we have explicit closes for prev and signal, use them
                    if prev_close_val is not None and sig_close_val is not None and sig_line_val is not None and prev_line_val is not None:
                        # LONG checks
                        if direction == "LONG":
                            if bq_cross_flag:
                                state = "FRESH_CROSS"
                            elif prev_close_val > prev_line_val and sig_close_val > sig_line_val:
                                state = "ALREADY_BEYOND_LINE"
                            elif prev_close_val <= prev_line_val and sig_close_val <= sig_line_val:
                                # If latest candle crosses beyond the line, prefer CROSSED_ON_LATEST_CANDLE
                                if latest_close_val is not None and latest_line_val is not None and (sig_close_val <= sig_line_val) and (latest_close_val > latest_line_val):
                                    state = "CROSSED_ON_LATEST_CANDLE"
                                else:
                                    # equality falls through to TOUCH_OR_EQUAL if exact
                                    if sig_close_val == sig_line_val:
                                        state = "TOUCH_OR_EQUAL"
                                    else:
                                        state = "NOT_REACHED_LINE"
                            elif prev_close_val >= prev_line_val and sig_close_val < sig_line_val:
                                state = "CROSSED_BACK"
                            else:
                                # check latest crossing possibility
                                if latest_close_val is not None and latest_line_val is not None:
                                    # would crossing hold for signal->latest?
                                    if (sig_close_val <= sig_line_val) and (latest_close_val > latest_line_val):
                                        state = "CROSSED_ON_LATEST_CANDLE"
                                    else:
                                        state = "UNKNOWN"
                                else:
                                    state = "UNKNOWN"
                        else:  # SHORT
                            if bq_cross_flag:
                                state = "FRESH_CROSS"
                            elif prev_close_val < prev_line_val and sig_close_val < sig_line_val:
                                state = "ALREADY_BEYOND_LINE"
                            elif prev_close_val >= prev_line_val and sig_close_val >= sig_line_val:
                                # If latest candle crosses beyond the line in SHORT direction, check
                                if latest_close_val is not None and latest_line_val is not None and (sig_close_val >= sig_line_val) and (latest_close_val < latest_line_val):
                                    state = "CROSSED_ON_LATEST_CANDLE"
                                else:
                                    if sig_close_val == sig_line_val:
                                        state = "TOUCH_OR_EQUAL"
                                    else:
                                        state = "NOT_REACHED_LINE"
                            elif prev_close_val <= prev_line_val and sig_close_val > sig_line_val:
                                state = "CROSSED_BACK"
                            else:
                                if latest_close_val is not None and latest_line_val is not None:
                                    if (sig_close_val >= sig_line_val) and (latest_close_val < latest_line_val):
                                        state = "CROSSED_ON_LATEST_CANDLE"
                                    else:
                                        state = "UNKNOWN"
                                else:
                                    state = "UNKNOWN"
                    else:
                        # If explicit prev not available but we have bq cross flag, use it
                        if bq_cross_flag:
                            state = "FRESH_CROSS"
                        else:
                            # Attempt weaker classification from signal_close and signal_line only
                            if sig_close_val is not None and sig_line_val is not None:
                                if direction == "LONG":
                                    if sig_close_val > sig_line_val:
                                        # signal beyond but no prev info → ALREADY or FRESH unknown
                                        state = "ALREADY_BEYOND_LINE"
                                    elif sig_close_val == sig_line_val:
                                        state = "TOUCH_OR_EQUAL"
                                    else:
                                        state = "NOT_REACHED_LINE"
                                else:
                                    if sig_close_val < sig_line_val:
                                        state = "ALREADY_BEYOND_LINE"
                                    elif sig_close_val == sig_line_val:
                                        state = "TOUCH_OR_EQUAL"
                                    else:
                                        state = "NOT_REACHED_LINE"
                            else:
                                state = "UNKNOWN"
                except Exception:
                    state = "UNKNOWN"

                # record counts
                cross_counts[state] = cross_counts.get(state, 0) + 1
                cross_by_tf.setdefault(tf, {})
                cross_by_tf[tf][state] = cross_by_tf[tf].get(state, 0) + 1
                cross_by_dir.setdefault(direction, {})
                cross_by_dir[direction][state] = cross_by_dir[direction].get(state, 0) + 1

                # collect examples
                def _make_example():
                    return {
                        "symbol": symbol,
                        "timeframe": tf,
                        "direction": direction,
                        "prev_close": prev_close_val,
                        "signal_close": sig_close_val,
                        "latest_close": latest_close_val,
                        "prev_line": prev_line_val,
                        "signal_line": sig_line_val,
                        "latest_line": latest_line_val,
                        "atr": atr_val,
                        "prev_distance_atr": None if prev_close_val is None or prev_line_val is None or atr_val is None or atr_val==0 else ((prev_close_val - prev_line_val)/atr_val if direction=="LONG" else (prev_line_val - prev_close_val)/atr_val),
                        "signal_distance_atr": None if sig_close_val is None or sig_line_val is None or atr_val is None or atr_val==0 else ((sig_close_val - sig_line_val)/atr_val if direction=="LONG" else (sig_line_val - sig_close_val)/atr_val),
                        "latest_distance_atr": None if latest_close_val is None or latest_line_val is None or atr_val is None or atr_val==0 else ((latest_close_val - latest_line_val)/atr_val if direction=="LONG" else (latest_line_val - latest_close_val)/atr_val),
                    }

                if state == "ALREADY_BEYOND_LINE" and len(examples_already) < 10:
                    examples_already.append(_make_example())
                if state == "CROSSED_ON_LATEST_CANDLE" and len(examples_crossed_latest) < 10:
                    examples_crossed_latest.append(_make_example())

                if final_score is not None and final_score >= 50.0:
                    totals["breakout_score_ge_50"] += 1
                if bq.get("confirmed"):
                    totals["breakout_confirmed"] += 1

                # decision
                detail = (tf_result.get("decision_details") or {}).get(direction) or {}
                dec = detail.get("decision")
                if dec == "TAKE":
                    totals["decision_take"] += 1
                elif dec == "WATCH":
                    totals["decision_watch"] += 1
                else:
                    totals["decision_skip"] += 1

                # bucket
                if final_score is None:
                    pass
                else:
                    if final_score < 20.0:
                        buckets["0-19.99"] += 1
                    elif final_score < 40.0:
                        buckets["20-39.99"] += 1
                    elif final_score < 50.0:
                        buckets["40-49.99"] += 1
                    elif final_score < 70.0:
                        buckets["50-69.99"] += 1
                    else:
                        buckets["70-100"] += 1

                # Primary bottleneck decision for rejected directions (not confirmed)
                if not bool(bq.get("confirmed", False)):
                    primary = "OTHER"

                    # 1. no data / no trendline — already handled above

                    # 2. not crossed
                    if not crossed:
                        primary = "NOT_CROSSED"
                    else:
                        # 3. breakout score below 50 -> weakest normalized component
                        if final_score is None or final_score < 50.0:
                            # normalize components to comparable 0..1
                            dn = (dist_score or 0.0) / 30.0
                            bn = (body_score or 0.0) / 20.0
                            wn = (wick_score or 0.0) / 20.0
                            # weakest = min
                            m = min(dn, bn, wn)
                            if m == dn:
                                primary = "WEAK_DISTANCE"
                            elif m == bn:
                                primary = "WEAK_BODY"
                            else:
                                primary = "LARGE_REJECTION_WICK"
                        else:
                            # 4. strong structure opposition
                            ms = dir_quality.get("market_structure") or {}
                            sq = dir_quality.get("structure_quality") or {}
                            alignment = (ms.get("alignment") or "").upper()
                            structure_score = _safe_float(sq.get("structure_score"))
                            if alignment == "OPPOSED" and structure_score is not None and structure_score < STRONG_OPPOSITION_THRESHOLD:
                                primary = "STRONG_STRUCTURE_OPPOSITION"
                            else:
                                # 5. confidence too low
                                conf = _safe_float(detail.get("confidence"))
                                if conf is None or conf < 50.0:
                                    primary = "CONFIDENCE_TOO_LOW"
                                else:
                                    primary = "OTHER"

                    bottleneck_counts[primary] = bottleneck_counts.get(primary, 0) + 1

                    # prepare candidate for closests
                    gap = None
                    if final_score is not None:
                        gap = max(0.0, 50.0 - final_score)
                    candidate = {
                        "symbol": symbol,
                        "timeframe": tf,
                        "direction": direction,
                        "decision": dec,
                        "decision_score": _safe_float(detail.get("decision_score")),
                        "confidence": _safe_float(detail.get("confidence")),
                        "breakout_score": final_score,
                        "breakout_score_gap": gap,
                        "cross_score": cross,
                        "distance_score": dist_score,
                        "body_score": body_score,
                        "wick_score": wick_score,
                        "structure_score": _safe_float((dir_quality.get("structure_quality") or {}).get("structure_score")),
                        "primary_blocker": primary,
                        "breakout_crossed": crossed,
                    }
                    rejected_candidates.append(candidate)

    # compute summaries
    comp_stats = {
        "cross_score": _summary_stats(cross_scores),
        "distance_score": _summary_stats(distance_scores),
        "body_score": _summary_stats(body_scores),
        "wick_score": _summary_stats(wick_scores),
        "final_score": _summary_stats(final_scores),
    }

    # sort rejected candidates as requested
    def _cand_sort_key(c):
        # breakout_crossed True first -> sort by not c['breakout_crossed']
        return (not bool(c.get("breakout_crossed")),
                (c.get("breakout_score_gap") if c.get("breakout_score_gap") is not None else float("inf")),
                -(c.get("decision_score") or 0.0))

    rejected_candidates.sort(key=_cand_sort_key)
    top10 = rejected_candidates[:10]

    report = {
        "totals": totals,
        "component_stats": comp_stats,
        "buckets": buckets,
        "bottleneck_counts": bottleneck_counts,
        "top10_closest_rejected": top10,
        "cross_state_counts": cross_counts,
        "cross_state_by_timeframe": cross_by_tf,
        "cross_state_by_direction": cross_by_dir,
        "examples_already_beyond": examples_already,
        "examples_crossed_on_latest": examples_crossed_latest,
    }
    return report
