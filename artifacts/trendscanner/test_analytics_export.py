from __future__ import annotations

import copy

from analytics_export import build_strategy_rows


def _sample_timeframe_result() -> dict:
    return {
        "quality": {
            "LONG": {
                "confidence": {
                    "confidence": 61.5,
                },
                "trend_quality": {
                    "score": 72.0,
                },
                "volume_quality": {
                    "score": 40.0,
                },
                "breakout_quality": {
                    "score": 83.3,
                    "confirmed": True,
                    "signal_close": 123.45,
                },
                "structure_quality": {
                    "score": 30.0,
                    "alignment": "OPPOSED",
                },
            },
            "SHORT": {
                "confidence": {
                    "confidence": 35.0,
                },
                "trend_quality": {
                    "score": 50.0,
                },
                "volume_quality": {
                    "score": 20.0,
                },
                "breakout_quality": {
                    "score": 20.0,
                    "confirmed": False,
                    "signal_close": 123.45,
                },
                "structure_quality": {
                    "score": 15.0,
                    "alignment": "ALIGNED",
                },
            },
            "FINAL": {},
        },
        "decision_details": {
            "LONG": {
                "decision": "WATCH",
                "decision_score": 31.5,
                "confidence": 61.5,
                "breakout_confirmed": True,
                "blockers": [],
            },
            "SHORT": {
                "decision": "SKIP",
                "decision_score": 10.0,
                "confidence": 35.0,
                "breakout_confirmed": False,
                "blockers": [
                    {
                        "code": "BREAKOUT_NOT_CONFIRMED",
                        "message": "Breakout is not confirmed",
                    }
                ],
            },
            "FINAL": {},
        },
    }


def test_empty_input() -> None:
    rows = build_strategy_rows(
        {},
        scan_id="scan-1",
        pipeline_version="0.5",
    )

    assert rows == []


def test_one_symbol_one_timeframe_exports_long_and_short() -> None:
    all_results = {
        "BTC/USDT": {
            "1h": _sample_timeframe_result(),
            "FINAL": {},
        }
    }

    rows = build_strategy_rows(
        all_results,
        scan_id="scan-1",
        pipeline_version="0.5",
        scanner_version="0.6",
        decision_version="1.0",
        timestamp_utc="2026-07-22T12:00:00+00:00",
    )

    assert len(rows) == 2

    long_row = next(
        row for row in rows
        if row["direction"] == "LONG"
    )
    short_row = next(
        row for row in rows
        if row["direction"] == "SHORT"
    )

    assert long_row["symbol"] == "BTC/USDT"
    assert long_row["timeframe"] == "1h"
    assert long_row["decision"] == "WATCH"
    assert long_row["decision_score"] == 31.5
    assert long_row["confidence"] == 61.5
    assert long_row["breakout_score"] == 83.3
    assert long_row["trend_quality_score"] == 72.0
    assert long_row["volume_score"] == 40.0
    assert long_row["structure_score"] == 30.0
    assert long_row["breakout_confirmed"] == 1
    assert long_row["structure_alignment"] == "OPPOSED"
    assert long_row["primary_blocker"] is None
    assert long_row["entry_price"] == 123.45
    assert long_row["pipeline_version"] == "0.5"
    assert long_row["scanner_version"] == "0.6"
    assert long_row["decision_version"] == "1.0"

    assert short_row["decision"] == "SKIP"
    assert short_row["confidence"] == 35.0
    assert short_row["breakout_confirmed"] == 0
    assert (
        short_row["primary_blocker"]
        == "BREAKOUT_NOT_CONFIRMED"
    )


def test_missing_quality_still_uses_decision_details() -> None:
    all_results = {
        "ETH/USDT": {
            "4h": {
                "quality": {},
                "decision_details": {
                    "LONG": {
                        "decision": "SKIP",
                        "decision_score": 0.0,
                        "confidence": 25.0,
                        "breakout_confirmed": False,
                        "blockers": [],
                    },
                    "SHORT": {},
                    "FINAL": {},
                },
            },
            "FINAL": {},
        }
    }

    rows = build_strategy_rows(
        all_results,
        scan_id="scan-2",
    )

    assert len(rows) == 1
    assert rows[0]["direction"] == "LONG"
    assert rows[0]["decision"] == "SKIP"
    assert rows[0]["confidence"] == 25.0
    assert rows[0]["trend_quality_score"] is None


def test_missing_decision_details_still_uses_quality() -> None:
    all_results = {
        "SOL/USDT": {
            "1d": {
                "quality": {
                    "LONG": {
                        "confidence": {
                            "confidence": 48.0,
                        },
                        "breakout_quality": {
                            "score": 55.0,
                            "confirmed": True,
                            "signal_close": 150.0,
                        },
                    },
                    "SHORT": {},
                    "FINAL": {},
                },
                "decision_details": {},
            },
            "FINAL": {},
        }
    }

    rows = build_strategy_rows(
        all_results,
        scan_id="scan-3",
    )

    assert len(rows) == 1
    assert rows[0]["direction"] == "LONG"
    assert rows[0]["decision"] is None
    assert rows[0]["confidence"] == 48.0
    assert rows[0]["breakout_score"] == 55.0
    assert rows[0]["breakout_confirmed"] == 1
    assert rows[0]["entry_price"] == 150.0


def test_error_symbol_is_skipped() -> None:
    all_results = {
        "BROKEN/USDT": {
            "_error": "network error",
            "FINAL": {},
        }
    }

    rows = build_strategy_rows(
        all_results,
        scan_id="scan-4",
    )

    assert rows == []


def test_source_input_is_not_mutated() -> None:
    all_results = {
        "AVAX/USDT": {
            "1w": _sample_timeframe_result(),
            "FINAL": {},
        }
    }
    original = copy.deepcopy(all_results)

    build_strategy_rows(
        all_results,
        scan_id="scan-5",
    )

    assert all_results == original


def test_output_is_deterministic_except_generated_ids() -> None:
    all_results = {
        "BTC/USDT": {
            "1h": _sample_timeframe_result(),
            "FINAL": {},
        }
    }

    first = build_strategy_rows(
        all_results,
        scan_id="scan-6",
        timestamp_utc="2026-07-22T12:00:00+00:00",
    )
    second = build_strategy_rows(
        all_results,
        scan_id="scan-6",
        timestamp_utc="2026-07-22T12:00:00+00:00",
    )

    assert len(first) == len(second)

    for left, right in zip(first, second):
        left_copy = dict(left)
        right_copy = dict(right)

        left_copy.pop("analysis_id")
        right_copy.pop("analysis_id")

        assert left_copy == right_copy


def run_tests() -> None:
    tests = [
        test_empty_input,
        test_one_symbol_one_timeframe_exports_long_and_short,
        test_missing_quality_still_uses_decision_details,
        test_missing_decision_details_still_uses_quality,
        test_error_symbol_is_skipped,
        test_source_input_is_not_mutated,
        test_output_is_deterministic_except_generated_ids,
    ]

    passed = 0

    print("=" * 60)
    print("ANALYTICS EXPORT TESTS")
    print("=" * 60)

    for test in tests:
        test()
        passed += 1
        print(f"✓ {test.__name__}")

    print("=" * 60)
    print(f"Passed {passed} / {len(tests)} tests")


if __name__ == "__main__":
    run_tests()
