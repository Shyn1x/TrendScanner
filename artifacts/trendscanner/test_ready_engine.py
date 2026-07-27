from copy import deepcopy

from ready_engine import (
    build_ready_candidates,
    build_ready_report,
    evaluate_ready_candidate,
)


def make_tf(
    *,
    direction="LONG",
    confirmed=True,
    signal="LONG",
    confidence=68.0,
    decision_score=48.0,
    trend=62.0,
    volume=100.0,
    breakout=80.0,
    structure=30.0,
    alignment="OPPOSED",
    blockers=None,
):
    blockers = blockers or []

    return {
        "quality": {
            direction: {
                "confirmed": confirmed,
                "signal": signal,
                "confidence": {
                    "confidence": confidence,
                },
                "trend_quality": {
                    "trend_quality_score": trend,
                },
                "volume_quality": {
                    "volume_score": volume,
                },
                "breakout_quality": {
                    "confirmed": confirmed,
                    "breakout_score": breakout,
                },
            }
        },
        "decision_details": {
            direction: {
                "decision": "WATCH",
                "direction": direction,
                "decision_score": decision_score,
                "confidence": confidence,
                "signal": signal,
                "breakout_confirmed": confirmed,
                "blockers": blockers,
                "component_scores": {
                    "trend_quality": trend,
                    "volume_quality": volume,
                    "breakout_quality": breakout,
                    "structure_quality": structure,
                },
                "market_context": {
                    "alignment": alignment,
                },
            }
        },
    }


def test_strong_candidate_is_ready():
    result = evaluate_ready_candidate(
        "APT/USDT",
        "4h",
        "LONG",
        make_tf(),
    )

    assert result["ready"] is True
    assert result["warnings"] == ["STRUCTURE_OPPOSED"]


def test_unconfirmed_candidate_is_not_ready():
    result = evaluate_ready_candidate(
        "DOGE/USDT",
        "1h",
        "LONG",
        make_tf(confirmed=False),
    )

    assert result["ready"] is False
    assert "BREAKOUT_NOT_CONFIRMED" in result["reasons"]


def test_low_volume_candidate_is_not_ready():
    result = evaluate_ready_candidate(
        "XRP/USDT",
        "4h",
        "LONG",
        make_tf(volume=20.0),
    )

    assert result["ready"] is False
    assert "VOLUME_BELOW_READY" in result["reasons"]


def test_hard_blocker_rejects_candidate():
    result = evaluate_ready_candidate(
        "BTC/USDT",
        "1h",
        "LONG",
        make_tf(
            blockers=[{
                "code": "STRONG_STRUCTURE_OPPOSITION",
                "message": "blocked",
            }]
        ),
    )

    assert result["ready"] is False
    assert "HARD_BLOCKERS_PRESENT" in result["reasons"]


def test_only_1h_and_4h_are_supported():
    result = evaluate_ready_candidate(
        "ATOM/USDT",
        "1w",
        "SHORT",
        make_tf(direction="SHORT", signal="SHORT"),
    )

    assert result["ready"] is False
    assert result["reasons"] == ["UNSUPPORTED_READY_TIMEFRAME"]


def test_candidates_are_ranked_and_input_is_not_mutated():
    all_results = {
        "APT/USDT": {
            "4h": make_tf(
                confidence=68.0,
                decision_score=48.0,
            )
        },
        "SOL/USDT": {
            "1h": make_tf(
                confidence=72.0,
                decision_score=61.0,
                alignment="ALIGNED",
            )
        },
    }

    original = deepcopy(all_results)
    candidates = build_ready_candidates(all_results)

    assert all_results == original
    assert [item["symbol"] for item in candidates] == [
        "SOL/USDT",
        "APT/USDT",
    ]


def test_report_counts():
    all_results = {
        "APT/USDT": {"4h": make_tf()},
        "SOL/USDT": {
            "1h": make_tf(
                direction="SHORT",
                signal="SHORT",
                alignment="ALIGNED",
            )
        },
    }

    report = build_ready_report(all_results)

    assert report["ready_count"] == 2
    assert report["by_timeframe"] == {"4h": 1, "1h": 1}
    assert report["by_direction"] == {"LONG": 1, "SHORT": 1}


if __name__ == "__main__":
    tests = [
        test_strong_candidate_is_ready,
        test_unconfirmed_candidate_is_not_ready,
        test_low_volume_candidate_is_not_ready,
        test_hard_blocker_rejects_candidate,
        test_only_1h_and_4h_are_supported,
        test_candidates_are_ranked_and_input_is_not_mutated,
        test_report_counts,
    ]

    for test in tests:
        test()

    print(f"{len(tests)} ready engine tests passed.")
