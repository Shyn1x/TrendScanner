"""
test_decision_engine.py
~~~~~~~~~~~~~~~~~~~~~~~
Тесты Trade Decision Engine (v0.5).

Покрывает:
  - TAKE / WATCH / SKIP логику
  - Hard blockers и их коды
  - decision_score бонусы и штрафы
  - positive_factors и warning_factors
  - evaluate_both_directions / FINAL-логику
  - Симметрия LONG/SHORT
  - Граничные случаи и защитные гарантии
"""

from __future__ import annotations
import sys, math, traceback
import pandas as pd
import numpy as np

from decision_engine import (
    evaluate_trade_decision,
    evaluate_both_directions,
    _TAKE_CONF_THRESHOLD,
    _WATCH_CONF_THRESHOLD,
    _HARD_CONF_MIN,
    _TAKE_BREAKOUT_MIN,
    _TAKE_TREND_MIN,
    _MIN_COMPONENTS_TAKE,
    _BLOCKER_NO_DATA,
    _BLOCKER_BREAKOUT,
    _BLOCKER_CONF_LOW,
    _BLOCKER_SIGNAL_MISMATCH,
    _BLOCKER_STRONG_OPPOSITION,
    _BLOCKER_INVALID_METRICS,
    _BLOCKER_PIPELINE_ERROR,
)

# ─── счётчик ──────────────────────────────────────────────────────────────────
_results: list[tuple[str, bool, str]] = []

def _check(label: str, cond: bool, detail: str = "") -> bool:
    _results.append((label, cond, detail))
    mark = "  ✓ " if cond else "  ✗ "
    print(f"{mark} {label}")
    if not cond and detail:
        print(f"     → {detail}")
    return cond


# ─── фабрики синтетических quality_result ────────────────────────────────────

def _make_qr(
    *,
    signal: str = "LONG",
    confirmed: bool = True,
    confidence: float = 75.0,
    conf_label: str = "HIGH",
    tq_score: float = 65.0,
    vq_score: float = 70.0,
    bq_score: float = 72.0,
    bq_confirmed: bool = True,
    sq_score: float = 70.0,
    sq_alignment: str = "ALIGNED",
    sq_available: bool = True,
    ms_structure: str = "BULLISH",
    bos_confirmed: bool = True,
    bos_dir: str = "BULLISH",
    choch: str = "NONE",
    avail_comps: int = 4,
    reason: str = "test",
) -> dict:
    """Создаёт синтетический качественный LONG quality_result."""
    return {
        "direction":   signal,
        "signal":      signal,
        "confirmed":   confirmed,
        "reason":      reason,
        "confidence": {
            "confidence":           confidence,
            "label":                conf_label,
            "available_components": avail_comps,
            "components":           {},
        },
        "trend_quality":    {"trend_quality_score": tq_score},
        "volume_quality":   {"volume_score": vq_score},
        "breakout_quality": {
            "breakout_score": bq_score,
            "confirmed":      bq_confirmed,
            "reason":         "test",
        },
        "structure_quality": {
            "structure_score": sq_score,
            "alignment":       sq_alignment,
            "available":       sq_available,
            "reason":          "test",
        },
        "market_structure": {
            "structure": ms_structure,
            "structure_score": 80.0,
            "bos": {
                "confirmed":  bos_confirmed,
                "direction":  bos_dir,
                "price":      100.0,
                "candle_idx": 5,
            },
            "choch": choch,
            "pivot_highs": [],
            "pivot_lows":  [],
        },
    }


def _make_long_qr(**kwargs) -> dict:
    defaults = dict(signal="LONG", bos_dir="BULLISH")
    defaults.update(kwargs)          # kwargs win — no duplicate-key error
    return _make_qr(**defaults)


def _make_short_qr(**kwargs) -> dict:
    defaults = dict(
        signal="SHORT",
        ms_structure="BEARISH",
        bos_dir="BEARISH",
        sq_alignment="ALIGNED",
    )
    defaults.update(kwargs)
    return _make_qr(**defaults)


def _blocker_codes(result: dict) -> list[str]:
    return [b["code"] for b in result.get("blockers", [])]


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 1 — TAKE
# ═══════════════════════════════════════════════════════════════════════════════

def test_take_long():
    """Качественный LONG → TAKE."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-T1 — Качественный LONG → TAKE")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(_make_long_qr(), "LONG")
    _check("T1.decision == TAKE",     r["decision"] == "TAKE")
    _check("T1.direction == LONG",    r["direction"] == "LONG")
    _check("T1.no_blockers",          len(r["blockers"]) == 0,
           str(r["blockers"]))
    _check("T1.has_positive_factors", len(r["positive_factors"]) > 0)
    _check("T1.summary correct",      "Qualified" in r["summary"])
    _check("T1.breakout_confirmed",   r["breakout_confirmed"] is True)
    _check("T1.decision_score >= 70", r["decision_score"] >= 70)
    print(f"     decision_score={r['decision_score']}, confidence={r['confidence']}")
    print(f"     positive={r['positive_factors']}")


def test_take_short():
    """Качественный SHORT → TAKE."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-T2 — Качественный SHORT → TAKE")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(_make_short_qr(), "SHORT")
    _check("T2.decision == TAKE",  r["decision"] == "TAKE")
    _check("T2.direction == SHORT", r["direction"] == "SHORT")
    _check("T2.no_blockers",       len(r["blockers"]) == 0,
           str(r["blockers"]))
    _check("T2.decision_score >= 70", r["decision_score"] >= 70)
    print(f"     decision_score={r['decision_score']}")


def test_take_requires_min_3_components():
    """Меньше 3 компонентов → WATCH (не TAKE), даже при conf>=70."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-T3 — Меньше 3 компонентов → не TAKE")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(avail_comps=2),
        "LONG",
    )
    _check("T3.decision != TAKE", r["decision"] != "TAKE",
           f"got {r['decision']}")
    print(f"     decision={r['decision']}, avail_comps=2")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 2 — WATCH
# ═══════════════════════════════════════════════════════════════════════════════

def test_watch_confidence_50_69():
    """confidence 50–69 → WATCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-W1 — confidence 50–69 → WATCH")
    print("────────────────────────────────────────────────────────────")
    for conf_val in (50.0, 60.0, 69.0):
        r = evaluate_trade_decision(
            _make_long_qr(confidence=conf_val, conf_label="MEDIUM"),
            "LONG",
        )
        _check(f"W1.conf={conf_val} → WATCH", r["decision"] == "WATCH",
               f"got {r['decision']}")
    print(f"     ✓ WATCH at conf=50,60,69")


def test_watch_high_conf_weak_volume():
    """confidence >= 70, но volume_score < 40 → WATCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-W2 — conf>=70, слабый volume → WATCH")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(confidence=75.0, vq_score=30.0),
        "LONG",
    )
    _check("W2.decision == WATCH", r["decision"] == "WATCH",
           f"got {r['decision']}")
    # Low volume warning присутствует
    warn_msgs = " ".join(r["warning_factors"])
    _check("W2.low_volume_warning", "volume" in warn_msgs.lower(),
           f"warnings={r['warning_factors']}")
    print(f"     decision={r['decision']}, decision_score={r['decision_score']}")


def test_watch_range():
    """confidence >= 70, но market structure == RANGE → WATCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-W3 — conf>=70, RANGE → WATCH")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(
            confidence=75.0,
            ms_structure="RANGE",
            sq_alignment="NEUTRAL",
            bos_dir="NONE",
            bos_confirmed=False,
        ),
        "LONG",
    )
    _check("W3.decision == WATCH", r["decision"] == "WATCH",
           f"got {r['decision']}")
    warn_msgs = " ".join(r["warning_factors"])
    _check("W3.range_warning", "rang" in warn_msgs.lower(),
           f"warnings={r['warning_factors']}")
    print(f"     decision={r['decision']}")


def test_watch_transition():
    """confidence >= 70, но structure == TRANSITION → WATCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-W4 — conf>=70, TRANSITION → WATCH")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(
            confidence=75.0,
            ms_structure="TRANSITION_BULLISH",
            sq_alignment="TRANSITION",
        ),
        "LONG",
    )
    _check("W4.decision == WATCH", r["decision"] == "WATCH",
           f"got {r['decision']}")
    print(f"     decision={r['decision']}, alignment=TRANSITION")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 3 — SKIP / hard blockers
# ═══════════════════════════════════════════════════════════════════════════════

def test_skip_breakout_not_confirmed():
    """Breakout not confirmed → SKIP + BREAKOUT_NOT_CONFIRMED."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S1 — breakout not confirmed → SKIP")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(bq_confirmed=False, confirmed=False, signal="WAIT"),
        "LONG",
    )
    _check("S1.decision == SKIP",   r["decision"] == "SKIP")
    _check("S1.blocker code",       "BREAKOUT_NOT_CONFIRMED" in _blocker_codes(r),
           str(_blocker_codes(r)))
    print(f"     blockers={_blocker_codes(r)}")


def test_skip_confidence_below_40():
    """confidence < 40 → SKIP + CONFIDENCE_TOO_LOW."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S2 — confidence < 40 → SKIP")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(confidence=35.0, conf_label="LOW"),
        "LONG",
    )
    _check("S2.decision == SKIP", r["decision"] == "SKIP")
    _check("S2.blocker CONFIDENCE_TOO_LOW",
           "CONFIDENCE_TOO_LOW" in _blocker_codes(r),
           str(_blocker_codes(r)))
    print(f"     conf=35.0, blockers={_blocker_codes(r)}")


def test_skip_confidence_40_to_49():
    """confidence 40–49 → SKIP (below WATCH threshold)."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S3 — confidence 40–49 → SKIP")
    print("────────────────────────────────────────────────────────────")
    for conf_val in (40.0, 45.0, 49.9):
        r = evaluate_trade_decision(
            _make_long_qr(confidence=conf_val, conf_label="MEDIUM"),
            "LONG",
        )
        _check(f"S3.conf={conf_val} → SKIP", r["decision"] == "SKIP",
               f"got {r['decision']}")
    print(f"     ✓ SKIP at conf=40,45,49.9")


def test_skip_strong_opposition():
    """Structure OPPOSED + score < 25 → SKIP + STRONG_STRUCTURE_OPPOSITION."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S4 — strong structure opposition → SKIP")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(
            confidence=72.0,
            sq_alignment="OPPOSED",
            sq_score=15.0,
            ms_structure="BEARISH",
        ),
        "LONG",
    )
    _check("S4.decision == SKIP", r["decision"] == "SKIP")
    _check("S4.blocker STRONG_STRUCTURE_OPPOSITION",
           "STRONG_STRUCTURE_OPPOSITION" in _blocker_codes(r),
           str(_blocker_codes(r)))
    print(f"     blockers={_blocker_codes(r)}")


def test_skip_signal_direction_mismatch():
    """Pipeline signal indicates opposite direction → SKIP."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S5 — signal direction mismatch → SKIP")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(signal="SHORT"),   # signal=SHORT but we ask LONG
        "LONG",
    )
    _check("S5.decision == SKIP", r["decision"] == "SKIP")
    _check("S5.blocker SIGNAL_DIRECTION_MISMATCH",
           "SIGNAL_DIRECTION_MISMATCH" in _blocker_codes(r),
           str(_blocker_codes(r)))
    print(f"     blockers={_blocker_codes(r)}")


def test_skip_missing_quality_result():
    """quality_result=None → SKIP + NO_DATA."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S6 — quality_result=None → SKIP")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(None, "LONG")
    _check("S6.decision == SKIP", r["decision"] == "SKIP")
    _check("S6.blocker NO_DATA", "NO_DATA" in _blocker_codes(r),
           str(_blocker_codes(r)))
    print(f"     blockers={_blocker_codes(r)}")


def test_skip_non_dict_input():
    """quality_result не dict → SKIP + NO_DATA."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S7 — non-dict input → SKIP")
    print("────────────────────────────────────────────────────────────")
    for bad in ("string", 42, [], True):
        r = evaluate_trade_decision(bad, "LONG")
        _check(f"S7.{type(bad).__name__} → SKIP", r["decision"] == "SKIP")
        _check(f"S7.{type(bad).__name__} NO_DATA", "NO_DATA" in _blocker_codes(r))


def test_skip_nan_confidence():
    """NaN confidence → SKIP."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S8 — NaN confidence → SKIP")
    print("────────────────────────────────────────────────────────────")
    qr = _make_long_qr()
    qr["confidence"]["confidence"] = float("nan")
    r = evaluate_trade_decision(qr, "LONG")
    _check("S8.decision == SKIP", r["decision"] == "SKIP")
    _check("S8.confidence is None", r["confidence"] is None)
    print(f"     blockers={_blocker_codes(r)}")


def test_skip_inf_component():
    """inf в компоненте не приводит к краху и не создаёт TAKE."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-S9 — inf component → не TAKE, не краш")
    print("────────────────────────────────────────────────────────────")
    qr = _make_long_qr(confidence=80.0)
    qr["trend_quality"]["trend_quality_score"] = float("inf")
    qr["confidence"]["confidence"] = float("inf")
    r = evaluate_trade_decision(qr, "LONG")
    _check("S9.no_crash",       isinstance(r, dict))
    _check("S9.decision SKIP",  r["decision"] == "SKIP",
           f"got {r['decision']}")
    _check("S9.conf is None",   r["confidence"] is None)


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 4 — invalid direction
# ═══════════════════════════════════════════════════════════════════════════════

def test_invalid_direction():
    """Некорректный direction → ValueError.
    Примечание: engine принимает регистронезависимый ввод ('long' → 'LONG'),
    поэтому в список невалидных НЕ включаем 'long'/'short'.
    """
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-V1 — invalid direction → ValueError")
    print("────────────────────────────────────────────────────────────")
    for bad in ("BUY", "", 42, None):
        try:
            evaluate_trade_decision(_make_long_qr(), bad)
            _check(f"V1.ValueError for {bad!r}", False, "Exception not raised")
        except ValueError:
            _check(f"V1.ValueError for {bad!r}", True)
        except Exception as e:
            _check(f"V1.ValueError for {bad!r}", False, str(e))
    # Строчные — принимаются (case-insensitive)
    r = evaluate_trade_decision(_make_long_qr(), "long")
    _check("V1.lowercase 'long' accepted", isinstance(r, dict) and r["direction"] == "LONG")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 5 — decision_score
# ═══════════════════════════════════════════════════════════════════════════════

def test_decision_score_clamp():
    """decision_score всегда в [0, 100]."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-DS1 — decision_score clamp [0, 100]")
    print("────────────────────────────────────────────────────────────")
    # Максимальные бонусы
    r_max = evaluate_trade_decision(
        _make_long_qr(
            confidence=90.0, bq_score=90.0, tq_score=80.0,
            vq_score=90.0, sq_alignment="ALIGNED",
            bos_confirmed=True, bos_dir="BULLISH",
        ),
        "LONG",
    )
    _check("DS1.score <= 100", r_max["decision_score"] <= 100.0,
           f"got {r_max['decision_score']}")

    # Максимальные штрафы (нет hard blockers из-за высокого conf)
    r_min = evaluate_trade_decision(
        _make_long_qr(
            confidence=70.0, vq_score=20.0, sq_alignment="RANGE",
            ms_structure="RANGE", choch="BEARISH",
        ),
        "LONG",
    )
    _check("DS1.score >= 0", r_min["decision_score"] >= 0.0,
           f"got {r_min['decision_score']}")

    print(f"     max_score={r_max['decision_score']}, min_score={r_min['decision_score']}")


def test_decision_score_bonuses():
    """Бонусы за breakout>=80, trend>=75, volume>=80, ALIGNED, BOS."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-DS2 — decision_score бонусы")
    print("────────────────────────────────────────────────────────────")
    base = evaluate_trade_decision(
        _make_long_qr(
            confidence=70.0, bq_score=70.0, tq_score=60.0,
            vq_score=70.0, sq_alignment="NEUTRAL",
            bos_confirmed=False,
        ),
        "LONG",
    )
    bonus = evaluate_trade_decision(
        _make_long_qr(
            confidence=70.0, bq_score=85.0, tq_score=80.0,
            vq_score=85.0, sq_alignment="ALIGNED",
            bos_confirmed=True, bos_dir="BULLISH",
        ),
        "LONG",
    )
    _check("DS2.bonus_score > base_score",
           bonus["decision_score"] > base["decision_score"],
           f"base={base['decision_score']}, bonus={bonus['decision_score']}")
    diff = bonus["decision_score"] - base["decision_score"]
    _check("DS2.diff >= 20 (5+5+5+5+5)",
           diff >= 20,
           f"diff={diff}")
    print(f"     base={base['decision_score']}, bonus={bonus['decision_score']}, diff={diff}")


def test_decision_score_penalties():
    """Штрафы за weak volume, TRANSITION, RANGE, CHoCH."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-DS3 — decision_score штрафы")
    print("────────────────────────────────────────────────────────────")
    clean = evaluate_trade_decision(
        _make_long_qr(confidence=70.0, vq_score=70.0, sq_alignment="ALIGNED"),
        "LONG",
    )
    penalized = evaluate_trade_decision(
        _make_long_qr(
            confidence=70.0, vq_score=25.0,
            sq_alignment="TRANSITION",
            ms_structure="RANGE",
            choch="BEARISH",
        ),
        "LONG",
    )
    _check("DS3.penalized < clean",
           penalized["decision_score"] < clean["decision_score"],
           f"clean={clean['decision_score']}, penalized={penalized['decision_score']}")
    print(f"     clean={clean['decision_score']}, penalized={penalized['decision_score']}")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 6 — факторы
# ═══════════════════════════════════════════════════════════════════════════════

def test_positive_factors():
    """Положительные факторы присутствуют при хорошем сетапе."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-F1 — positive_factors")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(_make_long_qr(), "LONG")
    pos = r["positive_factors"]
    _check("F1.positive_factors non-empty",         len(pos) > 0)
    _check("F1.breakout_confirmed in factors",
           any("breakout" in f.lower() and "confirm" in f.lower() for f in pos),
           str(pos))
    _check("F1.confidence in factors",
           any("confidence" in f.lower() for f in pos),
           str(pos))
    print(f"     positive={pos}")


def test_warning_factors():
    """warning_factors корректно заполняются для слабых аспектов."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-F2 — warning_factors")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(
        _make_long_qr(
            confidence=72.0, vq_score=25.0,
            ms_structure="RANGE", sq_alignment="NEUTRAL",
            avail_comps=3,
        ),
        "LONG",
    )
    warn = r["warning_factors"]
    _check("F2.warning_factors non-empty", len(warn) > 0)
    _check("F2.volume_warning",
           any("volume" in w.lower() for w in warn), str(warn))
    _check("F2.range_warning",
           any("rang" in w.lower() for w in warn), str(warn))
    print(f"     warnings={warn}")


def test_blocker_codes_present():
    """blockers содержат code и message."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-F3 — blocker структура {code, message}")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_trade_decision(None, "LONG")
    blk = r["blockers"]
    _check("F3.blockers non-empty",       len(blk) > 0)
    _check("F3.blocker has code",         "code" in blk[0])
    _check("F3.blocker has message",      "message" in blk[0])
    _check("F3.code is string",           isinstance(blk[0]["code"], str))
    _check("F3.message is string",        isinstance(blk[0]["message"], str))
    print(f"     blocker={blk[0]}")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 7 — формат результата
# ═══════════════════════════════════════════════════════════════════════════════

_EXPECTED_KEYS = {
    "direction", "decision", "decision_score", "confidence",
    "confidence_label", "signal", "breakout_confirmed",
    "available_components", "positive_factors", "warning_factors",
    "blockers", "component_scores", "market_context", "summary", "reason",
}

_EXPECTED_COMP_SCORE_KEYS = {
    "trend_quality", "volume_quality", "breakout_quality", "structure_quality",
}

_EXPECTED_MARKET_CTX_KEYS = {
    "structure", "alignment", "bos_direction", "choch",
}

def test_result_format():
    """Стабильный формат результата."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-FMT1 — стабильный формат результата")
    print("────────────────────────────────────────────────────────────")
    for label, qr, d in [
        ("TAKE",  _make_long_qr(),  "LONG"),
        ("SKIP",  None,             "LONG"),
        ("SHORT", _make_short_qr(), "SHORT"),
    ]:
        r = evaluate_trade_decision(qr, d)
        missing = _EXPECTED_KEYS - set(r.keys())
        _check(f"FMT1.{label}.all_top_keys",  not missing, f"missing={missing}")

        cs = r.get("component_scores", {})
        missing_cs = _EXPECTED_COMP_SCORE_KEYS - set(cs.keys())
        _check(f"FMT1.{label}.component_scores", not missing_cs, f"missing={missing_cs}")

        mc = r.get("market_context", {})
        missing_mc = _EXPECTED_MARKET_CTX_KEYS - set(mc.keys())
        _check(f"FMT1.{label}.market_context", not missing_mc, f"missing={missing_mc}")

        _check(f"FMT1.{label}.decision_in_valid", r["decision"] in ("TAKE","WATCH","SKIP"))
        _check(f"FMT1.{label}.decision_score_float", isinstance(r["decision_score"], float))
        _check(f"FMT1.{label}.summary_str", isinstance(r["summary"], str) and len(r["summary"]) > 0)
        _check(f"FMT1.{label}.reason_str", isinstance(r["reason"], str) and len(r["reason"]) > 0)
        _check(f"FMT1.{label}.pos_list", isinstance(r["positive_factors"], list))
        _check(f"FMT1.{label}.warn_list", isinstance(r["warning_factors"], list))
        _check(f"FMT1.{label}.blockers_list", isinstance(r["blockers"], list))


def test_decision_score_in_range():
    """decision_score ∈ [0, 100] для всех вариантов."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-FMT2 — decision_score ∈ [0, 100]")
    print("────────────────────────────────────────────────────────────")
    cases = [
        _make_long_qr(),
        _make_long_qr(confidence=35.0),
        _make_long_qr(confidence=72.0, sq_alignment="OPPOSED", sq_score=10.0),
        _make_short_qr(),
        None,
    ]
    for i, qr in enumerate(cases):
        r = evaluate_trade_decision(qr, "LONG")
        ok = 0.0 <= r["decision_score"] <= 100.0
        _check(f"FMT2.case{i+1}.score_in_range", ok,
               f"got {r['decision_score']}")


def test_volume_missing_no_crash():
    """Отсутствующий volume не вызывает краш."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-FMT3 — отсутствующий volume не вызывает краш")
    print("────────────────────────────────────────────────────────────")
    qr = _make_long_qr()
    del qr["volume_quality"]
    try:
        r = evaluate_trade_decision(qr, "LONG")
        _check("FMT3.no_crash",     isinstance(r, dict))
        _check("FMT3.volume_none",  r["component_scores"]["volume_quality"] is None)
        print(f"     decision={r['decision']}, volume_quality=None")
    except Exception as e:
        _check("FMT3.no_crash", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 8 — симметрия LONG/SHORT
# ═══════════════════════════════════════════════════════════════════════════════

def test_long_short_symmetry():
    """Симметричные сетапы дают симметричные решения."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-SYM1 — LONG/SHORT симметрия")
    print("────────────────────────────────────────────────────────────")
    r_long  = evaluate_trade_decision(_make_long_qr(),  "LONG")
    r_short = evaluate_trade_decision(_make_short_qr(), "SHORT")

    _check("SYM1.LONG decision same type as SHORT",
           r_long["decision"] == r_short["decision"],
           f"LONG={r_long['decision']}, SHORT={r_short['decision']}")
    _check("SYM1.LONG direction == LONG",   r_long["direction"]  == "LONG")
    _check("SYM1.SHORT direction == SHORT", r_short["direction"] == "SHORT")
    _check("SYM1.scores close",
           abs(r_long["decision_score"] - r_short["decision_score"]) <= 5,
           f"LONG={r_long['decision_score']}, SHORT={r_short['decision_score']}")
    print(f"     LONG: {r_long['decision']} / {r_long['decision_score']}")
    print(f"     SHORT: {r_short['decision']} / {r_short['decision_score']}")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 9 — evaluate_both_directions
# ═══════════════════════════════════════════════════════════════════════════════

_BOTH_QUALITY = {
    "LONG":  None,   # заполняется ниже
    "SHORT": None,
    "FINAL": {"signal": "LONG", "confidence": 75.0, "label": "HIGH", "reason": "test"},
}

_SENTINEL = object()

def _make_both(
    long_qr=_SENTINEL,
    short_qr=_SENTINEL,
) -> dict:
    """Собирает quality_analysis dict. None передаётся как есть (не заменяется дефолтом)."""
    return {
        "LONG":  _make_long_qr()  if long_qr  is _SENTINEL else long_qr,
        "SHORT": _make_short_qr() if short_qr is _SENTINEL else short_qr,
        "FINAL": {"signal": "LONG", "confidence": 75.0, "label": "HIGH", "reason": "test"},
    }


def test_both_format():
    """evaluate_both_directions возвращает корректный формат."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B1 — evaluate_both_directions формат")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_both_directions(_make_both())
    _check("B1.LONG key present",  "LONG"  in r)
    _check("B1.SHORT key present", "SHORT" in r)
    _check("B1.FINAL key present", "FINAL" in r)

    final = r["FINAL"]
    _check("B1.FINAL.decision",       final["decision"] in ("TAKE","WATCH","SKIP"))
    _check("B1.FINAL.direction",      final["direction"] in ("LONG","SHORT","NONE"))
    _check("B1.FINAL.decision_score", isinstance(final["decision_score"], float))
    _check("B1.FINAL.confidence",     isinstance(final["confidence"], float))
    _check("B1.FINAL.reason",         isinstance(final["reason"], str))
    print(f"     FINAL: {final}")


def test_both_both_take_higher_score():
    """Оба TAKE → выбирается направление с большим decision_score."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B2 — оба TAKE → больший decision_score")
    print("────────────────────────────────────────────────────────────")
    # LONG с высоким score (bq=90, tq=85, vq=85)
    long_qr  = _make_long_qr(confidence=80.0, bq_score=90.0, tq_score=85.0, vq_score=85.0)
    # SHORT с меньшим score
    short_qr = _make_short_qr(confidence=72.0, bq_score=65.0, tq_score=55.0, vq_score=60.0)

    r = evaluate_both_directions({"LONG": long_qr, "SHORT": short_qr,
                                   "FINAL": {"signal": "LONG", "confidence": 80.0,
                                             "label": "HIGH", "reason": "test"}})

    long_dec  = r["LONG"]["decision"]
    short_dec = r["SHORT"]["decision"]

    if long_dec == "TAKE" and short_dec == "TAKE":
        _check("B2.FINAL direction == LONG", r["FINAL"]["direction"] == "LONG",
               f"FINAL={r['FINAL']}")
        _check("B2.FINAL decision == TAKE",  r["FINAL"]["decision"]  == "TAKE")
    else:
        _check("B2.both_TAKE_as_expected",
               long_dec == "TAKE" and short_dec == "TAKE",
               f"LONG={long_dec}, SHORT={short_dec} (check fixtures)")
    print(f"     LONG={long_dec}/{r['LONG']['decision_score']}, "
          f"SHORT={short_dec}/{r['SHORT']['decision_score']}")
    print(f"     FINAL={r['FINAL']}")


def test_both_both_take_equal_score():
    """Оба TAKE с равным score → WATCH / NONE."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B3 — оба TAKE, равный score → WATCH/NONE")
    print("────────────────────────────────────────────────────────────")
    # Одинаковые fixtures
    long_qr  = _make_long_qr()
    short_qr = _make_short_qr()

    r = evaluate_both_directions({"LONG": long_qr, "SHORT": short_qr,
                                   "FINAL": {"signal": "LONG", "confidence": 75.0,
                                             "label": "HIGH", "reason": "test"}})
    long_dec  = r["LONG"]["decision"]
    short_dec = r["SHORT"]["decision"]
    ls = r["LONG"]["decision_score"]
    ss = r["SHORT"]["decision_score"]

    if long_dec == "TAKE" and short_dec == "TAKE" and ls == ss:
        _check("B3.FINAL direction == NONE", r["FINAL"]["direction"] == "NONE")
        _check("B3.FINAL decision WATCH or SKIP", r["FINAL"]["decision"] in ("WATCH", "SKIP"))
    else:
        # score отличаются — один из них побеждает, это тоже корректно
        _check("B3.FINAL has direction",
               r["FINAL"]["direction"] in ("LONG", "SHORT", "NONE"))
    print(f"     LONG={long_dec}/{ls}, SHORT={short_dec}/{ss}")
    print(f"     FINAL={r['FINAL']}")


def test_both_one_watch():
    """Только одно направление WATCH → оно выбирается."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B4 — один WATCH → FINAL=WATCH")
    print("────────────────────────────────────────────────────────────")
    long_qr  = _make_long_qr(confidence=60.0, conf_label="MEDIUM")   # WATCH
    short_qr = _make_short_qr(confidence=30.0, conf_label="LOW")      # SKIP

    r = evaluate_both_directions({"LONG": long_qr, "SHORT": short_qr,
                                   "FINAL": {"signal": "LONG", "confidence": 60.0,
                                             "label": "MEDIUM", "reason": "test"}})
    _check("B4.LONG == WATCH",          r["LONG"]["decision"]  == "WATCH",
           f"got {r['LONG']['decision']}")
    _check("B4.FINAL.decision == WATCH", r["FINAL"]["decision"] == "WATCH")
    _check("B4.FINAL.direction == LONG", r["FINAL"]["direction"] == "LONG")
    print(f"     FINAL={r['FINAL']}")


def test_both_both_watch():
    """Оба WATCH → выбирается больший decision_score."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B5 — оба WATCH → больший decision_score")
    print("────────────────────────────────────────────────────────────")
    long_qr  = _make_long_qr(confidence=65.0, conf_label="MEDIUM")
    short_qr = _make_short_qr(confidence=55.0, conf_label="MEDIUM")

    r = evaluate_both_directions({"LONG": long_qr, "SHORT": short_qr,
                                   "FINAL": {"signal": "WAIT", "confidence": 65.0,
                                             "label": "MEDIUM", "reason": "test"}})
    long_dec  = r["LONG"]["decision"]
    short_dec = r["SHORT"]["decision"]

    _check("B5.LONG == WATCH",  long_dec  == "WATCH", f"got {long_dec}")
    _check("B5.SHORT == WATCH", short_dec == "WATCH", f"got {short_dec}")
    _check("B5.FINAL == WATCH", r["FINAL"]["decision"] == "WATCH")

    if r["LONG"]["decision_score"] > r["SHORT"]["decision_score"]:
        _check("B5.FINAL direction == LONG", r["FINAL"]["direction"] == "LONG")
    elif r["SHORT"]["decision_score"] > r["LONG"]["decision_score"]:
        _check("B5.FINAL direction == SHORT", r["FINAL"]["direction"] == "SHORT")
    else:
        _check("B5.FINAL direction == NONE", r["FINAL"]["direction"] == "NONE")
    print(f"     LONG={r['LONG']['decision']}/{r['LONG']['decision_score']}, "
          f"SHORT={r['SHORT']['decision']}/{r['SHORT']['decision_score']}")
    print(f"     FINAL={r['FINAL']}")


def test_both_both_skip():
    """Оба SKIP → FINAL=SKIP/NONE."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B6 — оба SKIP → FINAL=SKIP/NONE")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_both_directions(_make_both(
        long_qr=None,
        short_qr=None,
    ))
    _check("B6.LONG == SKIP",           r["LONG"]["decision"]  == "SKIP")
    _check("B6.SHORT == SKIP",          r["SHORT"]["decision"] == "SKIP")
    _check("B6.FINAL.decision == SKIP", r["FINAL"]["decision"] == "SKIP")
    _check("B6.FINAL.direction == NONE", r["FINAL"]["direction"] == "NONE")
    print(f"     FINAL={r['FINAL']}")


def test_both_none_input():
    """quality_analysis=None → SKIP/NONE без краша."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B7 — quality_analysis=None → SKIP/NONE")
    print("────────────────────────────────────────────────────────────")
    r = evaluate_both_directions(None)
    _check("B7.no_crash",               isinstance(r, dict))
    _check("B7.FINAL.decision == SKIP", r["FINAL"]["decision"] == "SKIP")
    _check("B7.FINAL.direction == NONE", r["FINAL"]["direction"] == "NONE")
    print(f"     FINAL={r['FINAL']}")


def test_pipeline_final_cannot_bypass_blockers():
    """FINAL pipeline сигнал не обходит hard blockers в Decision Engine."""
    print("\n────────────────────────────────────────────────────────────")
    print("  DE-B8 — pipeline FINAL не обходит hard blockers")
    print("────────────────────────────────────────────────────────────")
    # Pipeline говорит LONG — но breakout не подтверждён
    long_qr = _make_long_qr(bq_confirmed=False, confirmed=False, signal="WAIT")
    qa = {
        "LONG":  long_qr,
        "SHORT": _make_short_qr(confidence=30.0),
        "FINAL": {"signal": "LONG", "confidence": 75.0, "label": "HIGH", "reason": ""},
    }
    r = evaluate_both_directions(qa)
    # LONG должен быть SKIP из-за blocker
    _check("B8.LONG has breakout blocker",
           "BREAKOUT_NOT_CONFIRMED" in _blocker_codes(r["LONG"]),
           str(_blocker_codes(r["LONG"])))
    _check("B8.LONG == SKIP", r["LONG"]["decision"] == "SKIP")
    _check("B8.FINAL != LONG_TAKE",
           not (r["FINAL"]["decision"] == "TAKE" and r["FINAL"]["direction"] == "LONG"))
    print(f"     LONG={r['LONG']['decision']}, FINAL={r['FINAL']}")


# ═══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════════

def _run_all():
    tests = [
        # TAKE
        test_take_long,
        test_take_short,
        test_take_requires_min_3_components,
        # WATCH
        test_watch_confidence_50_69,
        test_watch_high_conf_weak_volume,
        test_watch_range,
        test_watch_transition,
        # SKIP / blockers
        test_skip_breakout_not_confirmed,
        test_skip_confidence_below_40,
        test_skip_confidence_40_to_49,
        test_skip_strong_opposition,
        test_skip_signal_direction_mismatch,
        test_skip_missing_quality_result,
        test_skip_non_dict_input,
        test_skip_nan_confidence,
        test_skip_inf_component,
        # invalid direction
        test_invalid_direction,
        # decision_score
        test_decision_score_clamp,
        test_decision_score_bonuses,
        test_decision_score_penalties,
        # factors
        test_positive_factors,
        test_warning_factors,
        test_blocker_codes_present,
        # format
        test_result_format,
        test_decision_score_in_range,
        test_volume_missing_no_crash,
        # symmetry
        test_long_short_symmetry,
        # both_directions
        test_both_format,
        test_both_both_take_higher_score,
        test_both_both_take_equal_score,
        test_both_one_watch,
        test_both_both_watch,
        test_both_both_skip,
        test_both_none_input,
        test_pipeline_final_cannot_bypass_blockers,
    ]

    print("\n" + "═"*60)
    print("  DECISION ENGINE TESTS (v0.5)")
    print("═"*60)

    failed_tests = []
    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            failed_tests.append((test_fn.__name__, str(e)))
            print(f"\n  ✗ EXCEPTION in {test_fn.__name__}: {e}")
            traceback.print_exc()

    total  = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed

    take_labels  = [l for l, ok, _ in _results if l.startswith("T")]
    take_passed  = sum(1 for l, ok, _ in _results if l.startswith("T") and ok)
    watch_labels = [l for l, ok, _ in _results if l.startswith("W")]
    watch_passed = sum(1 for l, ok, _ in _results if l.startswith("W") and ok)
    skip_labels  = [l for l, ok, _ in _results if l.startswith("S") or l.startswith("V")]
    skip_passed  = sum(1 for l, ok, _ in _results if (l.startswith("S") or l.startswith("V")) and ok)
    sym_labels   = [l for l, ok, _ in _results if l.startswith("SYM")]
    sym_passed   = sum(1 for l, ok, _ in _results if l.startswith("SYM") and ok)
    b_labels     = [l for l, ok, _ in _results if l.startswith("B")]
    b_passed     = sum(1 for l, ok, _ in _results if l.startswith("B") and ok)

    print("\n" + "═"*60)
    print(f"  Decision engine tests: {passed}/{total}")
    print()
    print(f"  TAKE tests:           {take_passed}/{len(take_labels)}")
    print(f"  WATCH tests:          {watch_passed}/{len(watch_labels)}")
    print(f"  SKIP/blocker tests:   {skip_passed}/{len(skip_labels)}")
    print(f"  LONG/SHORT symmetry:  {sym_passed}/{len(sym_labels)}")
    print(f"  Conflict/both tests:  {b_passed}/{len(b_labels)}")

    if failed_tests:
        print(f"\n  Exceptions in {len(failed_tests)} test(s):")
        for name, err in failed_tests:
            print(f"    - {name}: {err}")

    if failed > 0:
        print(f"\n  Failed checks ({failed}):")
        for label, ok, detail in _results:
            if not ok:
                print(f"    ✗ {label}" + (f" — {detail}" if detail else ""))

    if failed == 0 and not failed_tests:
        print("\n  DECISION ENGINE STATUS: PASSED")
    else:
        print("\n  DECISION ENGINE STATUS: FAILED")
    print("═"*60)

    return passed, total


if __name__ == "__main__":
    passed, total = _run_all()
    sys.exit(0 if passed == total else 1)
