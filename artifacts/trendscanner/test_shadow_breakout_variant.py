from shadow_breakout_variant import (
    build_shadow_breakout_report,
    evaluate_confirmation_variant,
)


def _tf_payload(
    breakout_score: float,
    cross: float,
    distance: float,
    body: float,
    confirmed: bool,
) -> dict:
    return {
        "quality": {
            "LONG": {
                "line": {"exists": True},
                "breakout_quality": {
                    "breakout_score": breakout_score,
                    "confirmed": confirmed,
                    "components": {
                        "cross": cross,
                        "close_distance": distance,
                        "candle_body": body,
                        "rejection_wick": 0.0,
                    },
                },
            }
        }
    }


def test_variant_matches_production_when_crossed_and_score_ge_50() -> None:
    result = evaluate_confirmation_variant(
        crossed=True,
        breakout_score=55.0,
        distance_score=0.0,
        body_score=0.0,
    )
    assert result["production_confirmed"] is True
    assert result["shadow_confirmed"] is True


def test_variant_adds_candidate_without_cross_when_strong_combo_present() -> None:
    result = evaluate_confirmation_variant(
        crossed=False,
        breakout_score=70.0,
        distance_score=20.0,
        body_score=12.0,
    )
    assert result["production_confirmed"] is False
    assert result["shadow_confirmed"] is True


def test_variant_rejects_without_cross_and_without_strong_combo() -> None:
    result = evaluate_confirmation_variant(
        crossed=False,
        breakout_score=69.9,
        distance_score=25.0,
        body_score=12.0,
    )
    assert result["production_confirmed"] is False
    assert result["shadow_confirmed"] is False


def test_build_report_counts_added_and_affected_symbols() -> None:
    all_results = {
        "BTC/USDT": {
            "1h": _tf_payload(
                breakout_score=72.0,
                cross=0.0,
                distance=21.0,
                body=12.0,
                confirmed=False,
            ),
            "FINAL": {},
        },
        "ETH/USDT": {
            "1h": _tf_payload(
                breakout_score=52.0,
                cross=30.0,
                distance=10.0,
                body=8.0,
                confirmed=True,
            ),
        },
    }

    report = build_shadow_breakout_report(all_results)

    assert report["production_confirmed"] == 1
    assert report["shadow_confirmed"] == 2
    assert report["added_candidates"] == 1
    assert report["removed_candidates"] == 0
    assert report["symbols_affected"] == ["BTC/USDT"]
    assert report["total_rows_evaluated"] == 2

    added_rows = [row for row in report["side_by_side"] if row["delta"] == "added"]
    assert len(added_rows) == 1
    assert added_rows[0]["symbol"] == "BTC/USDT"


def test_build_report_skips_malformed_rows() -> None:
    all_results = {
        "SOL/USDT": {
            "1h": {"quality": "invalid"},
            "4h": {
                "quality": {
                    "SHORT": {
                        "breakout_quality": {
                            "breakout_score": 80.0,
                            "components": {"cross": 0.0, "close_distance": 30.0, "candle_body": 20.0},
                        }
                    }
                }
            },
        }
    }

    report = build_shadow_breakout_report(all_results)
    assert report["total_rows_evaluated"] == 1
    assert report["added_candidates"] == 1
    assert report["symbols_affected"] == ["SOL/USDT"]
