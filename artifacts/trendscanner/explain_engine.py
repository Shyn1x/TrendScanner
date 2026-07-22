import math

from decision_engine import (
    _TAKE_CONF_THRESHOLD,
    _WATCH_CONF_THRESHOLD,
    _TAKE_BREAKOUT_MIN,
    _TAKE_TREND_MIN,
    _WATCH_VOLUME_WEAK,
)


_VALID_DIRECTIONS = {"LONG", "SHORT"}


def _safe_float(value):
    try:
        f = float(value)
        return None if not math.isfinite(f) else f
    except (TypeError, ValueError):
        return None


def _safe_str(value):
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _blocker_codes(detail: dict) -> list[str]:
    if not isinstance(detail, dict):
        return []
    blk = detail.get("blockers")
    if not isinstance(blk, list):
        return []
    codes = []
    for b in blk:
        if isinstance(b, dict):
            code = b.get("code")
            if isinstance(code, str) and code:
                codes.append(code)
    return codes


def _blocker_messages(detail: dict) -> list[str]:
    if not isinstance(detail, dict):
        return []
    blk = detail.get("blockers")
    if not isinstance(blk, list):
        return []
    messages = []
    for b in blk:
        if isinstance(b, dict):
            msg = b.get("message") or b.get("code")
            if isinstance(msg, str) and msg:
                messages.append(msg)
        elif isinstance(b, str) and b:
            messages.append(b)
    return messages


def _select_stage(tf_result: dict) -> str:
    if not isinstance(tf_result, dict):
        return "invalid data"

    if "_error" in tf_result:
        return "error"

    quality = tf_result.get("quality")
    if not isinstance(quality, dict):
        return "data loaded"

    # Stage 1: load data
    if tf_result.get("trend") == "ERROR":
        return "data loaded"

    # Stage 2: trendline found for at least one direction
    def _has_line(direction: str) -> bool:
        d = quality.get(direction)
        return isinstance(d, dict) and d.get("line") is not None

    if not (_has_line("LONG") or _has_line("SHORT")):
        return "trendline"

    # Stage 3: breakout detected for at least one direction
    def _has_breakout(direction: str) -> bool:
        d = quality.get(direction)
        if not isinstance(d, dict):
            return False
        bq = d.get("breakout_quality")
        return isinstance(bq, dict) and _safe_float(bq.get("breakout_score")) is not None and _safe_float(bq.get("breakout_score")) > 0

    if not (_has_breakout("LONG") or _has_breakout("SHORT")):
        return "breakout detected"

    # Stage 4: breakout confirmation for at least one direction
    def _has_confirmed(direction: str) -> bool:
        d = quality.get(direction)
        if not isinstance(d, dict):
            return False
        if bool(d.get("confirmed", False)):
            return True
        bq = d.get("breakout_quality")
        return isinstance(bq, dict) and bool(bq.get("confirmed", False))

    if not (_has_confirmed("LONG") or _has_confirmed("SHORT")):
        return "breakout_confirmation"

    # Stage 5: trend quality
    def _has_tq(direction: str) -> bool:
        d = quality.get(direction)
        if not isinstance(d, dict):
            return False
        tq = d.get("trend_quality")
        return isinstance(tq, dict) and _safe_float(tq.get("trend_quality_score")) is not None and _safe_float(tq.get("trend_quality_score")) >= _TAKE_TREND_MIN

    if not (_has_tq("LONG") or _has_tq("SHORT")):
        return "trend quality"

    # Stage 6: volume
    def _has_vq(direction: str) -> bool:
        d = quality.get(direction)
        if not isinstance(d, dict):
            return False
        vq = d.get("volume_quality")
        return isinstance(vq, dict) and _safe_float(vq.get("volume_score")) is not None and _safe_float(vq.get("volume_score")) >= _WATCH_VOLUME_WEAK

    if not (_has_vq("LONG") or _has_vq("SHORT")):
        return "volume"

    # Stage 7: market structure
    def _has_structure(direction: str) -> bool:
        d = quality.get(direction)
        if not isinstance(d, dict):
            return False
        ms = d.get("market_structure")
        if not isinstance(ms, dict):
            return False
        structure = _safe_str(ms.get("structure")).upper()
        return structure not in ("TRANSITION_BULLISH", "TRANSITION_BEARISH", "UNKNOWN", "")

    if not (_has_structure("LONG") or _has_structure("SHORT")):
        return "market structure"

    # Stage 8: confidence
    def _has_confidence(direction: str) -> bool:
        d = quality.get(direction)
        if not isinstance(d, dict):
            return False
        conf = d.get("confidence")
        if not isinstance(conf, dict):
            return False
        return _safe_float(conf.get("confidence")) is not None and _safe_float(conf.get("confidence")) >= _WATCH_CONF_THRESHOLD

    if not (_has_confidence("LONG") or _has_confidence("SHORT")):
        return "confidence"

    return "decision"


def _extract_decision_detail(tf_result: dict, direction: str) -> dict:
    if not isinstance(tf_result, dict):
        return {}
    details = tf_result.get("decision_details")
    if not isinstance(details, dict):
        return {}
    detail = details.get(direction)
    return detail if isinstance(detail, dict) else {}


def _extract_factors(detail: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    if not isinstance(detail, dict):
        return [], [], [], []

    positive = [str(x) for x in detail.get("positive_factors", []) if isinstance(x, str) and x]
    warnings = [str(x) for x in detail.get("warning_factors", []) if isinstance(x, str) and x]
    blockers = _blocker_messages(detail)
    raw_codes = _blocker_codes(detail)
    return positive, warnings, blockers, raw_codes


def _distance_to_threshold(score, threshold):
    if score is None:
        return None
    current = _safe_float(score)
    if current is None:
        return None
    if threshold is None:
        return None
    return max(0.0, float(threshold) - current)


def _required_atr_for_close_distance_score(score: float) -> float:
    """Инвертирует score_close_distance в требуемый distance_atr."""
    if score <= 0.0:
        return 0.0
    if score <= 10.0:
        return score * 0.015
    if score <= 20.0:
        return 0.15 + (score - 10.0) * 0.02
    if score <= 30.0:
        return 0.35 + (score - 20.0) * 0.035
    return 0.70


def build_timeframe_explanation(timeframe_result: dict, direction: str) -> dict:
    direction = _safe_str(direction).upper()
    if direction not in _VALID_DIRECTIONS:
        raise ValueError("direction must be LONG or SHORT")

    detail = _extract_decision_detail(timeframe_result, direction)

    decision = _safe_str(detail.get("decision"))
    if decision not in ("TAKE", "WATCH", "SKIP"):
        decision = _safe_str(timeframe_result.get("decision"))
        if decision not in ("TAKE", "WATCH", "SKIP"):
            decision = "SKIP"

    decision_score = _safe_float(detail.get("decision_score"))
    confidence = _safe_float(detail.get("confidence"))
    breakout_confirmed = bool(detail.get("breakout_confirmed", False))

    quality = timeframe_result.get("quality") if isinstance(timeframe_result, dict) else {}
    tf_quality = quality.get(direction) if isinstance(quality, dict) else {}
    bq = tf_quality.get("breakout_quality") if isinstance(tf_quality, dict) else {}

    line_price = _safe_float(bq.get("line_price"))
    signal_close = _safe_float(bq.get("signal_close"))
    atr = _safe_float(bq.get("atr"))
    distance_atr = _safe_float(bq.get("distance_atr"))
    breakout_score = _safe_float(bq.get("breakout_score"))

    bq_components = bq.get("components") if isinstance(bq, dict) else {}
    if not isinstance(bq_components, dict):
        bq_components = {}
    cross_score = _safe_float(bq_components.get("cross"))
    body_score = _safe_float(bq_components.get("candle_body"))
    wick_score = _safe_float(bq_components.get("rejection_wick"))
    close_distance_score = _safe_float(bq_components.get("close_distance"))
    breakout_crossed = cross_score is not None and cross_score > 0.0

    breakout_distance = (
        signal_close - line_price
        if line_price is not None and signal_close is not None
        else None
    )
    breakout_distance_direction = direction
    if line_price is not None and signal_close is not None and direction == "SHORT":
        breakout_distance = line_price - signal_close
    confirmation_threshold = 50.0
    comparison_operator = "breakout_score >= 50.0"
    final_expression = (
        "crossed and (breakout_score >= 50.0)"
        if breakout_score is not None
        else "breakout_quality not available"
    )

    breakout_cross_score = cross_score
    breakout_distance_score = close_distance_score
    breakout_body_score = body_score
    breakout_wick_score = wick_score
    distance_atr_ratio = (
        (breakout_distance / atr) if atr is not None and breakout_distance is not None and atr != 0 else None
    )
    candle_body_ratio = _safe_float(bq.get("body_ratio"))
    rejection_wick_ratio = _safe_float(bq.get("rejection_wick_ratio"))

    scores = {
        "trend_quality": _safe_float(detail.get("component_scores", {}).get("trend_quality"))
        if isinstance(detail.get("component_scores"), dict) else None,
        "volume": _safe_float(detail.get("component_scores", {}).get("volume_quality"))
        if isinstance(detail.get("component_scores"), dict) else None,
        "breakout": _safe_float(detail.get("component_scores", {}).get("breakout_quality"))
        if isinstance(detail.get("component_scores"), dict) else None,
        "structure": _safe_float(detail.get("component_scores", {}).get("structure_quality"))
        if isinstance(detail.get("component_scores"), dict) else None,
    }

    raw_blockers = _blocker_messages(detail)
    raw_codes = _blocker_codes(detail)
    primary_blocker = raw_blockers[0] if raw_blockers else ""
    if len(raw_blockers) >= 2:
        secondary_blocker = raw_blockers[1]
    elif raw_blockers:
        warnings = [str(x) for x in detail.get("warning_factors", []) if isinstance(x, str) and x]
        secondary_blocker = warnings[0] if warnings else ""
    else:
        secondary_blocker = ""

    positive, warnings, blockers, raw_codes = _extract_factors(detail)

    pipeline_stage = _select_stage(timeframe_result)

    distance_to_watch = _distance_to_threshold(decision_score, _WATCH_CONF_THRESHOLD)
    distance_to_take = _distance_to_threshold(decision_score, _TAKE_CONF_THRESHOLD)

    return {
        "direction": direction,
        "decision": decision,
        "decision_score": decision_score,
        "confidence": confidence,
        "breakout_confirmed": breakout_confirmed,
        "scores": scores,
        "pipeline_stage": pipeline_stage,
        "breakout_line_price": line_price,
        "breakout_close": signal_close,
        "breakout_distance_direction": breakout_distance_direction,
        "breakout_distance": breakout_distance,
        "breakout_atr": atr,
        "distance_atr_ratio": distance_atr_ratio,
        "candle_body_ratio": candle_body_ratio,
        "rejection_wick_ratio": rejection_wick_ratio,
        "breakout_cross_score": breakout_cross_score,
        "breakout_distance_score": breakout_distance_score,
        "breakout_body_score": breakout_body_score,
        "breakout_wick_score": breakout_wick_score,
        "breakout_confirmation_threshold": confirmation_threshold,
        "breakout_confirmation_comparison": comparison_operator,
        "breakout_confirmation_expression": final_expression,
        "breakout_crossed": breakout_crossed,
        "primary_blocker": primary_blocker,
        "secondary_blocker": secondary_blocker,
        "distance_to_watch": distance_to_watch,
        "distance_to_take": distance_to_take,
        "positive_factors": positive,
        "warnings": warnings,
        "blockers": blockers,
        "raw_blocker_codes": raw_codes,
    }
