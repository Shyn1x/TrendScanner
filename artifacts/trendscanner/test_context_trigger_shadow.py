from __future__ import annotations

import copy

from context_trigger_shadow import (
    build_shadow_context_trigger,
    build_shadow_context_trigger_report,
)


def _make_context_block(
    *,
    direction: str,
    signal: str = "LONG",
    confirmed: bool = True,
    confidence: float = 72.0,
    confidence_label: str = "HIGH",
    breakout_score: float = 80.0,
    volume_score: float = 60.0,
    trend_score: float = 65.0,
    structure_score: float = 80.0,
    alignment: str = "ALIGNED",
    decision: str = "WATCH",
    blockers: list[dict] | None = None,
    warnings: list[str] | None = None,
    reason: str = "synthetic",
) -> tuple[dict, dict]:
    quality = {
        "signal": signal,
        "confirmed": confirmed,
        "trend_quality": {"trend_quality_score": trend_score},
        "volume_quality": {"volume_score": volume_score},
        "breakout_quality": {
            "breakout_score": breakout_score,
            "confirmed": confirmed,
            "components": {
                "cross": 30.0 if confirmed else 0.0,
                "close_distance": 20.0,
                "candle_body": 15.0,
                "rejection_wick": 15.0,
            },
        },
        "structure_quality": {
            "structure_score": structure_score,
            "alignment": alignment,
            "available": True,
        },
        "confidence": {
            "confidence": confidence,
            "label": confidence_label,
            "available_components": 4,
        },
        "reason": reason,
    }
    decision_detail = {
        "decision": decision,
        "decision_score": confidence,
        "confidence": confidence,
        "warning_factors": warnings or [],
        "blockers": blockers or [],
        "reason": reason,
    }
    return quality, decision_detail


def _make_no_trigger_block(direction: str) -> tuple[dict, dict]:
    return _make_context_block(
        direction=direction,
        signal="WAIT",
        confirmed=False,
        confidence=20.0,
        confidence_label="LOW",
        breakout_score=0.0,
        structure_score=40.0,
        alignment="NEUTRAL",
        decision="SKIP",
        reason="no trigger",
    )


def _make_tf(
    *,
    long_block: tuple[dict, dict] | None = None,
    short_block: tuple[dict, dict] | None = None,
) -> dict:
    long_q, long_d = long_block if long_block is not None else _make_context_block(direction="LONG")
    short_q, short_d = short_block if short_block is not None else _make_context_block(
        direction="SHORT",
        signal="WAIT",
        confirmed=False,
        confidence=35.0,
        confidence_label="LOW",
        structure_score=40.0,
        alignment="NEUTRAL",
        decision="SKIP",
    )
    return {
        "quality": {
            "LONG": long_q,
            "SHORT": short_q,
            "FINAL": {},
        },
        "decision_details": {
            "LONG": long_d,
            "SHORT": short_d,
            "FINAL": {},
        },
    }


def test_no_trigger() -> None:
    all_results = {
        "NO/TRIGGER": {
            "4h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="WAIT",
                    confirmed=False,
                    confidence=20.0,
                    confidence_label="LOW",
                    decision="SKIP",
                    reason="too weak",
                ),
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="WAIT",
                    confirmed=False,
                    confidence=18.0,
                    confidence_label="LOW",
                    decision="SKIP",
                    reason="too weak",
                ),
            ),
            "1h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=40.0,
                    confidence_label="MEDIUM",
                    decision="WATCH",
                ),
            ),
            "1w": _make_tf(),
            "1d": _make_tf(),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["NO/TRIGGER"])

    assert shadow["candidate"] is False
    assert shadow["direction"] == "NONE"
    assert shadow["trigger_timeframe"] is None
    assert shadow["reason"].startswith("No 4h/1h trigger")


def test_valid_1h_trigger_with_supportive_context() -> None:
    all_results = {
        "ONE/H": {
            "4h": _make_tf(long_block=_make_no_trigger_block("LONG")),
            "1h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=72.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
            ),
            "1w": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=80.0,
                    confidence_label="VERY HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "1d": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=75.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["ONE/H"])

    assert shadow["candidate"] is True
    assert shadow["direction"] == "LONG"
    assert shadow["trigger_timeframe"] == "1h"
    assert shadow["weekly_context"] == "SUPPORTS"
    assert shadow["daily_context"] == "SUPPORTS"
    assert shadow["combined_context"] == "SUPPORTS"
    assert shadow["context_supports"] is True
    assert shadow["context_opposes"] is False


def test_valid_4h_trigger_with_supportive_context() -> None:
    all_results = {
        "FOUR/H": {
            "4h": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="SHORT",
                    confirmed=True,
                    confidence=74.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                long_block=_make_no_trigger_block("LONG"),
            ),
            "1h": _make_tf(
                long_block=_make_no_trigger_block("LONG"),
                short_block=_make_no_trigger_block("SHORT"),
            ),
            "1w": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="SHORT",
                    confirmed=True,
                    confidence=78.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "1d": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="SHORT",
                    confirmed=True,
                    confidence=79.0,
                    confidence_label="VERY HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["FOUR/H"])

    assert shadow["candidate"] is True
    assert shadow["direction"] == "SHORT"
    assert shadow["trigger_timeframe"] == "4h"
    assert shadow["combined_context"] == "SUPPORTS"


def test_trigger_with_opposing_context() -> None:
    all_results = {
        "OPPOSE": {
            "4h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=70.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
            ),
            "1h": _make_tf(long_block=_make_no_trigger_block("LONG")),
            "1w": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=78.0,
                    confidence_label="HIGH",
                    alignment="OPPOSED",
                    structure_score=20.0,
                    decision="SKIP",
                ),
            ),
            "1d": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=77.0,
                    confidence_label="HIGH",
                    alignment="OPPOSED",
                    structure_score=20.0,
                    decision="SKIP",
                ),
            ),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["OPPOSE"])

    assert shadow["candidate"] is True
    assert shadow["combined_context"] == "OPPOSES"
    assert shadow["context_opposes"] is True
    assert shadow["context_supports"] is False


def test_neutral_mixed_context() -> None:
    all_results = {
        "MIXED": {
            "4h": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="SHORT",
                    confirmed=True,
                    confidence=71.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                long_block=_make_no_trigger_block("LONG"),
            ),
            "1h": _make_tf(long_block=_make_no_trigger_block("LONG")),
            "1w": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="WAIT",
                    confirmed=False,
                    confidence=35.0,
                    confidence_label="LOW",
                    alignment="NEUTRAL",
                    decision="SKIP",
                ),
            ),
            "1d": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="SHORT",
                    confirmed=True,
                    confidence=60.0,
                    confidence_label="MEDIUM",
                    alignment="TRANSITION",
                    decision="WATCH",
                ),
            ),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["MIXED"])

    assert shadow["candidate"] is True
    assert shadow["weekly_context"] == "NEUTRAL"
    assert shadow["daily_context"] == "MIXED"
    assert shadow["combined_context"] == "MIXED"


def test_missing_weekly_or_daily_data() -> None:
    all_results = {
        "MISSING": {
            "1h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=71.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
            ),
            "1d": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=78.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            # 1w intentionally omitted to verify UNAVAILABLE handling.
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["MISSING"])

    assert shadow["candidate"] is True
    assert shadow["weekly_context"] == "UNAVAILABLE"
    assert shadow["daily_context"] == "SUPPORTS"
    assert shadow["combined_context"] == "SUPPORTS"


def test_aligned_4h_and_1h_triggers() -> None:
    all_results = {
        "ALIGN": {
            "4h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=73.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
            ),
            "1h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=75.0,
                    confidence_label="HIGH",
                    decision="TAKE",
                ),
            ),
            "1w": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=77.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "1d": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=79.0,
                    confidence_label="VERY HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["ALIGN"])

    assert shadow["candidate"] is True
    assert shadow["trigger_confluence"] is True
    assert shadow["trigger_conflict"] is False
    assert shadow["direction"] == "LONG"
    assert shadow["trigger_timeframe"] == "4h"


def test_conflicting_4h_and_1h_triggers() -> None:
    all_results = {
        "CONFLICT": {
            "4h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=74.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                short_block=_make_no_trigger_block("SHORT"),
            ),
            "1h": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="SHORT",
                    confirmed=True,
                    confidence=76.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                long_block=_make_no_trigger_block("LONG"),
            ),
            "1w": _make_tf(),
            "1d": _make_tf(),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    shadow = build_shadow_context_trigger(all_results["CONFLICT"])

    assert shadow["candidate"] is False
    assert shadow["trigger_conflict"] is True
    assert shadow["reason"].startswith("TRIGGER_CONFLICT")


def test_source_input_is_not_mutated() -> None:
    all_results = {
        "AAA/USDT": {
            "4h": _make_tf(),
            "1h": _make_tf(),
            "1w": _make_tf(),
            "1d": _make_tf(),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }
    original = copy.deepcopy(all_results)

    _ = build_shadow_context_trigger_report(all_results)

    assert all_results == original
    assert all_results["AAA/USDT"]["FINAL"] == original["AAA/USDT"]["FINAL"]


def test_output_is_deterministic() -> None:
    all_results = {
        "AAA/USDT": {
            "4h": _make_tf(),
            "1h": _make_tf(),
            "1w": _make_tf(),
            "1d": _make_tf(),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        }
    }

    first = build_shadow_context_trigger_report(all_results)
    second = build_shadow_context_trigger_report(all_results)

    assert first == second


def test_report_counts_and_examples() -> None:
    all_results = {
        "A/USDT": {
            "4h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=71.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                short_block=_make_no_trigger_block("SHORT"),
            ),
            "1h": _make_tf(long_block=_make_no_trigger_block("LONG"), short_block=_make_no_trigger_block("SHORT")),
            "1w": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=78.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "1d": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=76.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        },
        "C/USDT": {
            "4h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=72.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                short_block=_make_no_trigger_block("SHORT"),
            ),
            "1h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=74.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                short_block=_make_no_trigger_block("SHORT"),
            ),
            "1w": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=78.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "1d": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=76.0,
                    confidence_label="HIGH",
                    alignment="ALIGNED",
                    decision="TAKE",
                ),
            ),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        },
        "B/USDT": {
            "4h": _make_tf(
                short_block=_make_context_block(
                    direction="SHORT",
                    signal="SHORT",
                    confirmed=True,
                    confidence=73.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                long_block=_make_no_trigger_block("LONG"),
            ),
            "1h": _make_tf(
                long_block=_make_context_block(
                    direction="LONG",
                    signal="LONG",
                    confirmed=True,
                    confidence=74.0,
                    confidence_label="HIGH",
                    decision="WATCH",
                ),
                short_block=_make_no_trigger_block("SHORT"),
            ),
            "1w": _make_tf(),
            "1d": _make_tf(),
            "FINAL": {"signal": "WAIT", "decision": "SKIP"},
        },
    }

    report = build_shadow_context_trigger_report(all_results)

    assert report["summary"]["symbols_total"] == 3
    assert report["summary"]["trigger_candidates"]["count"] == 2
    assert report["summary"]["aligned_trigger_confluence"]["count"] == 1
    assert report["summary"]["trigger_conflicts"]["count"] == 1
    assert report["trigger_timeframe_counts"]["4h"]["count"] == 2
    assert report["trigger_timeframe_counts"]["1h"]["count"] == 0
    assert report["candidate_examples"]
    assert "A/USDT" in report["shadow_results"]


if __name__ == "__main__":
    test_no_trigger()
    test_valid_1h_trigger_with_supportive_context()
    test_valid_4h_trigger_with_supportive_context()
    test_trigger_with_opposing_context()
    test_neutral_mixed_context()
    test_missing_weekly_or_daily_data()
    test_aligned_4h_and_1h_triggers()
    test_conflicting_4h_and_1h_triggers()
    test_source_input_is_not_mutated()
    test_output_is_deterministic()
    test_report_counts_and_examples()
    print("context_trigger_shadow tests passed")