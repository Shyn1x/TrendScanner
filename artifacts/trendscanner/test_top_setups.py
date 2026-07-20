"""
test_top_setups.py
~~~~~~~~~~~~~~~~~~
Unit tests for top_setups.build_top_setups.

НЕ импортирует Streamlit.
НЕ изменяет существующие тесты.

Запуск: python test_top_setups.py
"""

import sys
import math
import copy
import traceback

from top_setups import build_top_setups
from settings import MAX_RESULT_PAGE_SIZE

# ─────────────────────────────────────────────────────────────────────────────
#  Infrastructure
# ─────────────────────────────────────────────────────────────────────────────

_passed: list[str] = []
_failed: list[str] = []


def ok(name: str, detail: str = "") -> None:
    _passed.append(name)
    print(f"  ✓  {name}" + (f"  ({detail})" if detail else ""))


def fail(name: str, reason: str) -> None:
    _failed.append(name)
    print(f"  ✗  {name}  ←  {reason}")


def check(cond: bool, name: str, reason: str = "", detail: str = "") -> bool:
    if cond:
        ok(name, detail)
    else:
        fail(name, reason or "assertion failed")
    return cond


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─────────────────────────────────────────────────────────────────────────────
#  Data factories
# ─────────────────────────────────────────────────────────────────────────────

def _make_final(
    decision: str = "SKIP",
    direction: str = "NONE",
    decision_score: float = 0.0,
    confidence: float = 50.0,
    confidence_label: str = "MEDIUM",
    signal: str = "WAIT",
    directional_score: float = 0.0,
    decision_reason: str = "test reason",
    available_timeframes: int = 3,
    take_long: int = 0,
    take_short: int = 0,
    watch_long: int = 0,
    watch_short: int = 0,
) -> dict:
    return {
        "signal":               signal,
        "confidence":           confidence,
        "confidence_label":     confidence_label,
        "directional_score":    directional_score,
        "decision":             decision,
        "decision_direction":   direction,
        "decision_score":       decision_score,
        "decision_directional_score": decision_score,
        "decision_reason":      decision_reason,
        "available_timeframes": available_timeframes,
        "decision_counts": {
            "take_long":   take_long,
            "take_short":  take_short,
            "watch_long":  watch_long,
            "watch_short": watch_short,
        },
    }


def _make_result(final: dict) -> dict:
    return {"FINAL": final, "4h": {"signal": "WAIT", "confidence": 50.0}}


def _take_long(score: float = 80.0, conf: float = 75.0) -> dict:
    return _make_result(_make_final(
        decision="TAKE", direction="LONG",
        decision_score=score, confidence=conf,
        signal="LONG", take_long=1,
    ))


def _take_short(score: float = 75.0, conf: float = 70.0) -> dict:
    return _make_result(_make_final(
        decision="TAKE", direction="SHORT",
        decision_score=score, confidence=conf,
        signal="SHORT", take_short=1,
    ))


def _watch_long(score: float = 55.0, conf: float = 60.0) -> dict:
    return _make_result(_make_final(
        decision="WATCH", direction="LONG",
        decision_score=score, confidence=conf,
        signal="LONG", watch_long=1,
    ))


def _watch_short(score: float = 45.0, conf: float = 55.0) -> dict:
    return _make_result(_make_final(
        decision="WATCH", direction="SHORT",
        decision_score=score, confidence=conf,
        signal="SHORT", watch_short=1,
    ))


def _skip(score: float = 30.0) -> dict:
    return _make_result(_make_final(
        decision="SKIP", direction="NONE",
        decision_score=score, confidence=40.0,
    ))


# ─────────────────────────────────────────────────────────────────────────────
#  TS1 — Priority: TAKE > WATCH > SKIP
# ─────────────────────────────────────────────────────────────────────────────

def test_priority_take_above_watch():
    section("TS1 — TAKE > WATCH priority")
    symbol_results = {
        "WATCH/USDT": _watch_long(score=90.0),   # higher score, but WATCH
        "TAKE/USDT":  _take_long(score=60.0),    # lower score, but TAKE
    }
    result = build_top_setups(symbol_results, include_watch=True, include_skip=True)
    check(len(result) >= 2, "TS1.min_two_results")
    take_idx  = next((i for i, r in enumerate(result) if r["symbol"] == "TAKE/USDT"), None)
    watch_idx = next((i for i, r in enumerate(result) if r["symbol"] == "WATCH/USDT"), None)
    check(take_idx is not None and watch_idx is not None, "TS1.both_present")
    if take_idx is not None and watch_idx is not None:
        check(take_idx < watch_idx, "TS1.TAKE_before_WATCH",
              f"TAKE at {take_idx}, WATCH at {watch_idx}")


def test_priority_watch_above_skip():
    section("TS2 — WATCH > SKIP priority")
    symbol_results = {
        "SKIP/USDT":  _skip(score=99.0),          # highest score, but SKIP
        "WATCH/USDT": _watch_long(score=30.0),    # lower score, but WATCH
    }
    result = build_top_setups(symbol_results, include_watch=True, include_skip=True,
                               min_decision_score=0.0)
    watch_idx = next((i for i, r in enumerate(result) if r["symbol"] == "WATCH/USDT"), None)
    skip_idx  = next((i for i, r in enumerate(result) if r["symbol"] == "SKIP/USDT"), None)
    check(watch_idx is not None and skip_idx is not None, "TS2.both_present")
    if watch_idx is not None and skip_idx is not None:
        check(watch_idx < skip_idx, "TS2.WATCH_before_SKIP",
              f"WATCH at {watch_idx}, SKIP at {skip_idx}")


# ─────────────────────────────────────────────────────────────────────────────
#  TS3 — Sorting within category
# ─────────────────────────────────────────────────────────────────────────────

def test_sort_by_decision_score():
    section("TS3 — Sort by decision_score within TAKE")
    symbol_results = {
        "A/USDT": _take_long(score=60.0, conf=80.0),
        "B/USDT": _take_long(score=90.0, conf=50.0),
        "C/USDT": _take_long(score=75.0, conf=65.0),
    }
    result = build_top_setups(symbol_results)
    scores = [r["decision_score"] for r in result]
    check(scores == sorted(scores, reverse=True), "TS3.scores_descending",
          f"scores={scores}")


def test_sort_by_confidence_on_equal_score():
    section("TS4 — Sort by confidence when decision_score equal")
    symbol_results = {
        "LOW/USDT":  _take_long(score=80.0, conf=50.0),
        "HIGH/USDT": _take_long(score=80.0, conf=90.0),
        "MID/USDT":  _take_long(score=80.0, conf=70.0),
    }
    result = build_top_setups(symbol_results)
    syms = [r["symbol"] for r in result]
    check(syms[0] == "HIGH/USDT", "TS4.highest_conf_first", f"order={syms}")
    check(syms[1] == "MID/USDT",  "TS4.mid_conf_second",   f"order={syms}")
    check(syms[2] == "LOW/USDT",  "TS4.low_conf_third",    f"order={syms}")


def test_sort_stable_by_symbol():
    section("TS5 — Stable sort by symbol when score and conf equal")
    symbol_results = {
        "ZZZ/USDT": _take_long(score=80.0, conf=70.0),
        "AAA/USDT": _take_long(score=80.0, conf=70.0),
        "MMM/USDT": _take_long(score=80.0, conf=70.0),
    }
    result = build_top_setups(symbol_results)
    syms = [r["symbol"] for r in result]
    check(syms == sorted(syms), "TS5.alphabetical_stable", f"order={syms}")


# ─────────────────────────────────────────────────────────────────────────────
#  TS6 — include_watch=False
# ─────────────────────────────────────────────────────────────────────────────

def test_include_watch_false():
    section("TS6 — include_watch=False excludes WATCH")
    symbol_results = {
        "TAKE/USDT":  _take_long(),
        "WATCH/USDT": _watch_long(score=90.0),
    }
    result = build_top_setups(symbol_results, include_watch=False)
    symbols = {r["symbol"] for r in result}
    check("WATCH/USDT" not in symbols, "TS6.watch_excluded")
    check("TAKE/USDT"  in symbols,     "TS6.take_included")


# ─────────────────────────────────────────────────────────────────────────────
#  TS7 — include_skip=False
# ─────────────────────────────────────────────────────────────────────────────

def test_include_skip_false():
    section("TS7 — include_skip=False excludes SKIP")
    symbol_results = {
        "TAKE/USDT": _take_long(),
        "SKIP/USDT": _skip(score=99.0),
    }
    result = build_top_setups(symbol_results, include_skip=False)
    symbols = {r["symbol"] for r in result}
    check("SKIP/USDT" not in symbols, "TS7.skip_excluded")
    check("TAKE/USDT" in symbols,     "TS7.take_included")


# ─────────────────────────────────────────────────────────────────────────────
#  TS8 — min_decision_score filter on WATCH
# ─────────────────────────────────────────────────────────────────────────────

def test_min_score_filter_watch():
    section("TS8 — min_decision_score filters WATCH")
    symbol_results = {
        "ABOVE/USDT": _watch_long(score=60.0),
        "BELOW/USDT": _watch_short(score=20.0),
    }
    result = build_top_setups(
        symbol_results, include_watch=True, include_skip=False,
        min_decision_score=25.0,
    )
    symbols = {r["symbol"] for r in result}
    check("ABOVE/USDT" in symbols,     "TS8.above_threshold_included")
    check("BELOW/USDT" not in symbols, "TS8.below_threshold_excluded")


# ─────────────────────────────────────────────────────────────────────────────
#  TS9 — limit
# ─────────────────────────────────────────────────────────────────────────────

def test_limit():
    section("TS9 — limit respected")
    symbol_results = {f"SYM{i}/USDT": _take_long(score=float(i)) for i in range(20)}
    result = build_top_setups(symbol_results, limit=5)
    check(len(result) == 5, "TS9.limit_5", f"got {len(result)}")


def test_limit_below_one():
    section("TS10 — limit < 1 → use 1")
    symbol_results = {
        "A/USDT": _take_long(score=80.0),
        "B/USDT": _take_long(score=70.0),
    }
    result = build_top_setups(symbol_results, limit=0)
    check(len(result) == 1, "TS10.limit_clamped_to_1", f"got {len(result)}")

    result2 = build_top_setups(symbol_results, limit=-5)
    check(len(result2) == 1, "TS10.limit_negative_clamped", f"got {len(result2)}")


def test_limit_above_max():
    section("TS11 — limit > MAX_RESULT_PAGE_SIZE → clamped")
    symbol_results = {f"SYM{i}/USDT": _take_long(score=float(i)) for i in range(MAX_RESULT_PAGE_SIZE + 10)}
    result = build_top_setups(symbol_results, limit=MAX_RESULT_PAGE_SIZE + 100)
    check(len(result) <= MAX_RESULT_PAGE_SIZE, "TS11.max_clamped",
          f"got {len(result)}, max={MAX_RESULT_PAGE_SIZE}")


# ─────────────────────────────────────────────────────────────────────────────
#  TS12 — Incomplete FINAL
# ─────────────────────────────────────────────────────────────────────────────

def test_incomplete_final():
    section("TS12 — Incomplete FINAL — only decision key")
    symbol_results = {
        "PARTIAL/USDT": {"FINAL": {"decision": "TAKE", "decision_direction": "LONG"}},
    }
    # Should not raise; TAKE with LONG direction should be included
    try:
        result = build_top_setups(symbol_results)
        check(True, "TS12.no_exception")
        take_present = any(r["symbol"] == "PARTIAL/USDT" for r in result)
        check(take_present, "TS12.partial_take_included")
    except Exception as e:
        fail("TS12.no_exception", str(e))


def test_empty_final():
    section("TS13 — Empty FINAL dict")
    symbol_results = {
        "EMPTY/USDT": {"FINAL": {}},
    }
    try:
        result = build_top_setups(symbol_results, include_skip=True)
        check(True, "TS13.no_exception")
        # Empty FINAL → decision defaults to SKIP, direction=NONE
        # include_skip=True but direction=NONE → excluded (WATCH/NONE rule applies)
        # Actually SKIP with direction NONE → included if include_skip=True
        # Let's just verify no exception
    except Exception as e:
        fail("TS13.no_exception", str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  TS14 — None inputs
# ─────────────────────────────────────────────────────────────────────────────

def test_none_result():
    section("TS14 — None result for symbol")
    symbol_results = {
        "NONE/USDT":  None,
        "VALID/USDT": _take_long(),
    }
    try:
        result = build_top_setups(symbol_results)
        check(True, "TS14.no_exception")
        symbols = {r["symbol"] for r in result}
        check("NONE/USDT"  not in symbols, "TS14.none_excluded")
        check("VALID/USDT" in symbols,     "TS14.valid_included")
    except Exception as e:
        fail("TS14.no_exception", str(e))


def test_error_result():
    section("TS15 — _error result")
    symbol_results = {
        "ERR/USDT":   {"_error": "connection timeout"},
        "VALID/USDT": _take_long(),
    }
    result = build_top_setups(symbol_results)
    symbols = {r["symbol"] for r in result}
    check("ERR/USDT"   not in symbols, "TS15.error_excluded")
    check("VALID/USDT" in symbols,     "TS15.valid_included")


# ─────────────────────────────────────────────────────────────────────────────
#  TS16 — NaN / inf in fields
# ─────────────────────────────────────────────────────────────────────────────

def test_nan_inf_fields():
    section("TS16 — NaN and inf in numeric fields")
    nan_result = _make_result(_make_final(
        decision="TAKE", direction="LONG",
        decision_score=float("nan"), confidence=float("inf"),
    ))
    symbol_results = {
        "NAN/USDT": nan_result,
        "VALID/USDT": _take_long(score=50.0),
    }
    try:
        result = build_top_setups(symbol_results)
        check(True, "TS16.no_exception")
        # NAN/USDT: TAKE LONG — should be included (NaN score → 0.0)
        for r in result:
            check(
                math.isfinite(r["decision_score"]),
                f"TS16.finite_score_{r['symbol']}",
                f"score={r['decision_score']}",
            )
            check(
                math.isfinite(r["confidence"]),
                f"TS16.finite_confidence_{r['symbol']}",
                f"conf={r['confidence']}",
            )
    except Exception as e:
        fail("TS16.no_exception", str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  TS17 — Input dict not mutated
# ─────────────────────────────────────────────────────────────────────────────

def test_input_not_mutated():
    section("TS17 — Input dict not mutated")
    symbol_results = {
        "A/USDT": _take_long(score=80.0),
        "B/USDT": _watch_long(score=50.0),
        "C/USDT": _skip(score=30.0),
    }
    original = copy.deepcopy(symbol_results)
    build_top_setups(symbol_results, include_watch=True, include_skip=True)
    check(symbol_results == original, "TS17.input_not_mutated")


# ─────────────────────────────────────────────────────────────────────────────
#  TS18 — rank sequential
# ─────────────────────────────────────────────────────────────────────────────

def test_rank_sequential():
    section("TS18 — rank is sequential starting from 1")
    symbol_results = {
        f"SYM{i}/USDT": _take_long(score=float(100 - i))
        for i in range(5)
    }
    result = build_top_setups(symbol_results, limit=5)
    ranks = [r["rank"] for r in result]
    check(ranks == list(range(1, len(result) + 1)), "TS18.rank_sequential",
          f"ranks={ranks}")


# ─────────────────────────────────────────────────────────────────────────────
#  TS19 — raw_result preserved
# ─────────────────────────────────────────────────────────────────────────────

def test_raw_result_preserved():
    section("TS19 — raw_result is the original dict")
    orig = _take_long(score=80.0)
    orig["_sentinel"] = "unique_marker_12345"
    symbol_results = {"SENT/USDT": orig}
    result = build_top_setups(symbol_results)
    check(len(result) == 1, "TS19.one_result")
    if result:
        raw = result[0].get("raw_result")
        check(isinstance(raw, dict), "TS19.raw_result_is_dict")
        check(raw.get("_sentinel") == "unique_marker_12345", "TS19.sentinel_preserved")


# ─────────────────────────────────────────────────────────────────────────────
#  TS20 — Record format stable
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_RECORD_KEYS = {
    "rank", "symbol", "decision", "direction", "decision_score",
    "confidence", "confidence_label", "signal", "directional_score",
    "decision_reason", "available_timeframes", "take_count",
    "watch_count", "raw_result",
}


def test_record_format_stable():
    section("TS20 — Record format stable")
    symbol_results = {
        "FMT/USDT": _take_long(score=80.0),
    }
    result = build_top_setups(symbol_results)
    check(len(result) >= 1, "TS20.has_result")
    if result:
        actual_keys = set(result[0].keys())
        missing = EXPECTED_RECORD_KEYS - actual_keys
        extra   = actual_keys - EXPECTED_RECORD_KEYS
        check(not missing, "TS20.no_missing_keys", f"missing={missing}")
        check(not extra,   "TS20.no_extra_keys",   f"extra={extra}")


# ─────────────────────────────────────────────────────────────────────────────
#  TS21 — TAKE direction=NONE excluded
# ─────────────────────────────────────────────────────────────────────────────

def test_take_direction_none_excluded():
    section("TS21 — TAKE with direction=NONE is excluded")
    symbol_results = {
        "BADDTAKE/USDT": _make_result(_make_final(
            decision="TAKE", direction="NONE", decision_score=95.0,
        )),
        "GOOD/USDT": _take_long(score=50.0),
    }
    result = build_top_setups(symbol_results)
    symbols = {r["symbol"] for r in result}
    check("BADDTAKE/USDT" not in symbols, "TS21.take_none_excluded")
    check("GOOD/USDT" in symbols,         "TS21.good_take_included")


# ─────────────────────────────────────────────────────────────────────────────
#  TS22 — Empty input
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_input():
    section("TS22 — Empty input returns empty list")
    result = build_top_setups({})
    check(isinstance(result, list), "TS22.returns_list")
    check(len(result) == 0,         "TS22.empty_result")


# ─────────────────────────────────────────────────────────────────────────────
#  TS23 — take_count and watch_count extracted
# ─────────────────────────────────────────────────────────────────────────────

def test_count_fields():
    section("TS23 — take_count / watch_count extracted correctly")
    r = _make_result(_make_final(
        decision="TAKE", direction="LONG",
        decision_score=80.0, confidence=75.0,
        take_long=2, take_short=0,
        watch_long=1, watch_short=1,
    ))
    result = build_top_setups({"COUNT/USDT": r})
    check(len(result) == 1, "TS23.has_result")
    if result:
        check(result[0]["take_count"]  == 2, "TS23.take_count=2",
              f"got {result[0]['take_count']}")
        check(result[0]["watch_count"] == 2, "TS23.watch_count=2",
              f"got {result[0]['watch_count']}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    suites = [
        ("TS1  — TAKE > WATCH priority",             test_priority_take_above_watch),
        ("TS2  — WATCH > SKIP priority",             test_priority_watch_above_skip),
        ("TS3  — Sort by decision_score",            test_sort_by_decision_score),
        ("TS4  — Sort by confidence on equal score", test_sort_by_confidence_on_equal_score),
        ("TS5  — Stable sort by symbol",             test_sort_stable_by_symbol),
        ("TS6  — include_watch=False",               test_include_watch_false),
        ("TS7  — include_skip=False",                test_include_skip_false),
        ("TS8  — min_decision_score filter",         test_min_score_filter_watch),
        ("TS9  — limit",                             test_limit),
        ("TS10 — limit < 1",                         test_limit_below_one),
        ("TS11 — limit > MAX",                       test_limit_above_max),
        ("TS12 — incomplete FINAL",                  test_incomplete_final),
        ("TS13 — empty FINAL",                       test_empty_final),
        ("TS14 — None result",                       test_none_result),
        ("TS15 — _error result",                     test_error_result),
        ("TS16 — NaN/inf fields",                    test_nan_inf_fields),
        ("TS17 — input not mutated",                 test_input_not_mutated),
        ("TS18 — rank sequential",                   test_rank_sequential),
        ("TS19 — raw_result preserved",              test_raw_result_preserved),
        ("TS20 — record format stable",              test_record_format_stable),
        ("TS21 — TAKE direction=NONE excluded",      test_take_direction_none_excluded),
        ("TS22 — empty input",                       test_empty_input),
        ("TS23 — take/watch count fields",           test_count_fields),
    ]

    suite_results: list[tuple[str, bool]] = []
    for suite_name, func in suites:
        before_fail = len(_failed)
        try:
            func()
            passed_suite = len(_failed) == before_fail
        except Exception:
            print(f"\n  !!! EXCEPTION in {suite_name} !!!")
            traceback.print_exc()
            passed_suite = False
            _failed.append(f"{suite_name} (EXCEPTION)")
        suite_results.append((suite_name, passed_suite))

    total  = len(_passed) + len(_failed)
    n_pass = len(_passed)

    print("\n" + "═" * 60)
    print("  TOP SETUPS REPORT")
    print("═" * 60)
    for suite_name, suite_ok in suite_results:
        mark = "✓" if suite_ok else "✗"
        print(f"  {mark}  {suite_name}")

    if _failed:
        print("\n  Failed checks:")
        for f in _failed:
            print(f"    ✗  {f}")

    print(f"\n  Top Setups tests: {n_pass}/{total}")
    status = "PASSED" if not _failed else "FAILED"
    print(f"\n  TOP SETUPS STATUS: {status}")
    print("═" * 60 + "\n")
    sys.exit(0 if not _failed else 1)


if __name__ == "__main__":
    main()
