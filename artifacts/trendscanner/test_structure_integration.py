"""
test_structure_integration.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Тесты интеграции Market Structure в Confidence Engine.

Покрывает:
  - structure_quality.py  : directional scoring, BOS, CHoCH, edge cases
  - confidence.py         : четыре компонента, перенормировка, available
  - quality_pipeline.py   : структура вычисляется один раз, opposition filter,
                            сигнальный инвариант
"""

from __future__ import annotations
import sys, math, traceback
import numpy as np
import pandas as pd

# ─── импорты ──────────────────────────────────────────────────────────────────
from structure_quality import score_structure_for_direction
from confidence import calculate_confidence, WEIGHTS
from quality_pipeline import (
    analyze_quality,
    analyze_both_directions,
    SIGNAL_THRESHOLD,
    STRONG_OPPOSITION_THRESHOLD,
)
from market_structure import analyze_market_structure


# ─── счётчик результатов ──────────────────────────────────────────────────────
_results: list[tuple[str, bool, str]] = []

def _check(label: str, cond: bool, detail: str = "") -> bool:
    _results.append((label, cond, detail))
    mark = "  ✓ " if cond else "  ✗ "
    print(f"{mark} {label}")
    if not cond and detail:
        print(f"     → {detail}")
    return cond


# ─── вспомогательные фабрики ──────────────────────────────────────────────────

def _make_ms_result(
    structure: str,
    score: float = 70.0,
    bos_confirmed: bool = False,
    bos_direction: str = "NONE",
    choch: str = "NONE",
) -> dict:
    """Синтетический результат analyze_market_structure для unit-тестов."""
    return {
        "structure":       structure,
        "structure_score": score,
        "bos": {
            "confirmed":  bos_confirmed,
            "direction":  bos_direction,
            "price":      None,
            "candle_idx": None,
        },
        "choch":          choch,
        "pivot_highs":    [],
        "pivot_lows":     [],
        "classified_highs": [],
        "classified_lows":  [],
        "reason":         "synthetic",
    }


def _make_sq(
    structure: str,
    direction: str,
    score: float = 70.0,
    bos_confirmed: bool = False,
    bos_direction: str = "NONE",
    choch: str = "NONE",
) -> dict:
    return score_structure_for_direction(
        _make_ms_result(structure, score, bos_confirmed, bos_direction, choch),
        direction,
    )


def _full_conf_inputs(structure_score: float = 60.0, sq_available: bool = True) -> dict:
    """Четыре синтетических компонента для calculate_confidence."""
    tq = {"trend_quality_score": 70.0}
    vq = {"volume_score": 60.0}
    bq = {"breakout_score": 65.0, "confirmed": True}
    sq = {"structure_score": structure_score, "available": sq_available, "reason": "test"}
    return dict(trend_quality=tq, volume_quality=vq, breakout_quality=bq, structure_quality=sq)


def _make_zigzag_df(levels: list[float], candles_between: int = 12) -> pd.DataFrame:
    """
    Строит OHLCV DataFrame с пилообразным движением цены через уровни.
    Цена движется от levels[i] к levels[i+1] за candles_between свечей,
    затем добавляется хвост от последнего уровня в обратном направлении.
    """
    rows = []
    for i in range(len(levels) - 1):
        start, end = levels[i], levels[i + 1]
        prices = np.linspace(start, end, candles_between + 1)
        for j in range(candles_between):
            o = prices[j]
            c = prices[j + 1]
            h = max(o, c) * 1.001
            lo = min(o, c) * 0.999
            rows.append({"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0})

    # хвост — уходит от последнего уровня
    last_price = levels[-1]
    direction = -1.0 if levels[-1] > levels[-2] else 1.0
    tail_end = last_price * (1 + direction * 0.005)
    tail_prices = np.linspace(last_price, tail_end, 6)
    for j in range(5):
        o = tail_prices[j]
        c = tail_prices[j + 1]
        h = max(o, c) * 1.001
        lo = min(o, c) * 0.999
        rows.append({"open": o, "high": h, "low": lo, "close": c, "volume": 800.0})

    df = pd.DataFrame(rows)
    df.index = pd.RangeIndex(len(df))
    return df


def _bullish_df() -> pd.DataFrame:
    """HH + HL zigzag → BULLISH structure."""
    return _make_zigzag_df([100, 80, 110, 90, 120, 95, 130])


def _bearish_df() -> pd.DataFrame:
    """LH + LL zigzag → BEARISH structure."""
    return _make_zigzag_df([100, 120, 90, 110, 80, 105, 70])


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 1 — structure_quality.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_sq_bullish_long():
    """BULLISH поддерживает LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-1 — BULLISH поддерживает LONG")
    print("────────────────────────────────────────────────────────────")
    r = _make_sq("BULLISH", "LONG", score=80.0)
    _check("SQ1.alignment == ALIGNED",   r["alignment"] == "ALIGNED")
    _check("SQ1.available == True",      r["available"] is True)
    _check("SQ1.structure_score >= 70",  r["structure_score"] >= 70)
    _check("SQ1.raw == 80",              r["raw_structure_score"] == 80.0)
    print(f"     {r['reason']}")


def test_sq_bullish_short():
    """BULLISH против SHORT."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-2 — BULLISH против SHORT")
    print("────────────────────────────────────────────────────────────")
    r = _make_sq("BULLISH", "SHORT", score=80.0)
    _check("SQ2.alignment == OPPOSED",  r["alignment"] == "OPPOSED")
    _check("SQ2.score < 50",            r["structure_score"] < 50)
    _check("SQ2.score == 100-80 == 20", r["structure_score"] == 20.0)
    print(f"     {r['reason']}")


def test_sq_bearish_short():
    """BEARISH поддерживает SHORT."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-3 — BEARISH поддерживает SHORT")
    print("────────────────────────────────────────────────────────────")
    r = _make_sq("BEARISH", "SHORT", score=75.0)
    _check("SQ3.alignment == ALIGNED",  r["alignment"] == "ALIGNED")
    _check("SQ3.score >= 70",           r["structure_score"] >= 70)
    print(f"     {r['reason']}")


def test_sq_bearish_long():
    """BEARISH против LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-4 — BEARISH против LONG")
    print("────────────────────────────────────────────────────────────")
    r = _make_sq("BEARISH", "LONG", score=75.0)
    _check("SQ4.alignment == OPPOSED",  r["alignment"] == "OPPOSED")
    _check("SQ4.score < 50",            r["structure_score"] < 50)
    _check("SQ4.score == 25",           r["structure_score"] == 25.0)
    print(f"     {r['reason']}")


def test_sq_range_both():
    """RANGE нейтрален для обоих направлений."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-5 — RANGE нейтрален для LONG и SHORT")
    print("────────────────────────────────────────────────────────────")
    for d in ("LONG", "SHORT"):
        r = _make_sq("RANGE", d, score=30.0)
        _check(f"SQ5.{d}.alignment == NEUTRAL", r["alignment"] == "NEUTRAL")
        _check(f"SQ5.{d}.score == 40",          r["structure_score"] == 40.0)
        _check(f"SQ5.{d}.available == True",    r["available"] is True)
        print(f"     {d}: {r['reason']}")


def test_sq_transition_bullish():
    """TRANSITION_BULLISH: поддерживает LONG слабо, ослабляет SHORT."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-6 — TRANSITION_BULLISH")
    print("────────────────────────────────────────────────────────────")
    r_long  = _make_sq("TRANSITION_BULLISH", "LONG",  score=80.0)
    r_short = _make_sq("TRANSITION_BULLISH", "SHORT", score=80.0)
    _check("SQ6.LONG.alignment == TRANSITION",  r_long["alignment"]  == "TRANSITION")
    _check("SQ6.SHORT.alignment == TRANSITION", r_short["alignment"] == "TRANSITION")
    _check("SQ6.LONG.score <= 65",              r_long["structure_score"] <= 65.0)
    _check("SQ6.SHORT.score <= 35",             r_short["structure_score"] <= 35.0)
    print(f"     LONG={r_long['structure_score']}, SHORT={r_short['structure_score']}")


def test_sq_transition_bearish():
    """TRANSITION_BEARISH: поддерживает SHORT слабо, ослабляет LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-7 — TRANSITION_BEARISH")
    print("────────────────────────────────────────────────────────────")
    r_long  = _make_sq("TRANSITION_BEARISH", "LONG",  score=80.0)
    r_short = _make_sq("TRANSITION_BEARISH", "SHORT", score=80.0)
    _check("SQ7.LONG.alignment == TRANSITION",  r_long["alignment"]  == "TRANSITION")
    _check("SQ7.SHORT.alignment == TRANSITION", r_short["alignment"] == "TRANSITION")
    _check("SQ7.LONG.score <= 35",              r_long["structure_score"] <= 35.0)
    _check("SQ7.SHORT.score <= 65",             r_short["structure_score"] <= 65.0)
    print(f"     LONG={r_long['structure_score']}, SHORT={r_short['structure_score']}")


def test_sq_bos_long():
    """BULLISH BOS добавляет бонус к LONG и штрафует SHORT."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-8 — BOS: BULLISH для LONG, штраф для SHORT")
    print("────────────────────────────────────────────────────────────")
    base_long  = _make_sq("BULLISH", "LONG",  score=70.0)
    bos_long   = _make_sq("BULLISH", "LONG",  score=70.0, bos_confirmed=True, bos_direction="BULLISH")
    base_short = _make_sq("BULLISH", "SHORT", score=70.0)
    bos_short  = _make_sq("BULLISH", "SHORT", score=70.0, bos_confirmed=True, bos_direction="BULLISH")

    _check("SQ8.LONG BOS adds bonus",    bos_long["structure_score"] > base_long["structure_score"])
    _check("SQ8.SHORT BOS adds penalty", bos_short["structure_score"] < base_short["structure_score"])
    print(f"     LONG base={base_long['structure_score']}, +BOS={bos_long['structure_score']}")
    print(f"     SHORT base={base_short['structure_score']}, +BOS(opp)={bos_short['structure_score']}")


def test_sq_bos_short():
    """BEARISH BOS добавляет бонус к SHORT и штрафует LONG."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-9 — BOS: BEARISH для SHORT, штраф для LONG")
    print("────────────────────────────────────────────────────────────")
    base_short = _make_sq("BEARISH", "SHORT", score=70.0)
    bos_short  = _make_sq("BEARISH", "SHORT", score=70.0, bos_confirmed=True, bos_direction="BEARISH")
    base_long  = _make_sq("BEARISH", "LONG",  score=70.0)
    bos_long   = _make_sq("BEARISH", "LONG",  score=70.0, bos_confirmed=True, bos_direction="BEARISH")

    _check("SQ9.SHORT BOS adds bonus",   bos_short["structure_score"] > base_short["structure_score"])
    _check("SQ9.LONG BOS adds penalty",  bos_long["structure_score"]  < base_long["structure_score"])
    print(f"     SHORT base={base_short['structure_score']}, +BOS={bos_short['structure_score']}")
    print(f"     LONG base={base_long['structure_score']}, +BOS(opp)={bos_long['structure_score']}")


def test_sq_choch_aligned_caps():
    """CHoCH совпадает с направлением — ограничивает score до 70."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-10 — CHoCH совпадает → cap 70")
    print("────────────────────────────────────────────────────────────")
    # BULLISH структура + BULLISH BOS → score без CHoCH должен быть > 70
    r_no_choch = _make_sq("BULLISH", "LONG", score=80.0,
                           bos_confirmed=True, bos_direction="BULLISH")
    r_choch    = _make_sq("BULLISH", "LONG", score=80.0,
                           bos_confirmed=True, bos_direction="BULLISH", choch="BULLISH")
    _check("SQ10.no_choch score > 70",   r_no_choch["structure_score"] > 70.0)
    _check("SQ10.choch score <= 70",     r_choch["structure_score"]    <= 70.0)
    print(f"     no_choch={r_no_choch['structure_score']}, with_choch={r_choch['structure_score']}")


def test_sq_choch_opposed_penalty():
    """CHoCH против направления — штраф."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-11 — CHoCH против → штраф 20")
    print("────────────────────────────────────────────────────────────")
    r_no   = _make_sq("BULLISH", "LONG", score=70.0)
    r_opp  = _make_sq("BULLISH", "LONG", score=70.0, choch="BEARISH")
    _check("SQ11.no_choch > opp_choch", r_no["structure_score"] > r_opp["structure_score"])
    diff = round(r_no["structure_score"] - r_opp["structure_score"], 2)
    _check("SQ11.penalty == 20",        diff == 20.0, f"diff={diff}")
    print(f"     no_choch={r_no['structure_score']}, opp_choch={r_opp['structure_score']}")


def test_sq_unknown_unavailable():
    """UNKNOWN — компонент недоступен."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-12 — UNKNOWN → недоступен")
    print("────────────────────────────────────────────────────────────")
    r_long  = _make_sq("UNKNOWN", "LONG")
    r_short = _make_sq("UNKNOWN", "SHORT")
    _check("SQ12.LONG.available  == False", r_long["available"]  is False)
    _check("SQ12.SHORT.available == False", r_short["available"] is False)
    _check("SQ12.LONG.score == 0",          r_long["structure_score"]  == 0.0)
    _check("SQ12.SHORT.score == 0",         r_short["structure_score"] == 0.0)


def test_sq_invalid_direction():
    """Некорректный direction → ValueError."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-13 — invalid direction → ValueError")
    print("────────────────────────────────────────────────────────────")
    for bad in ("BUY", "", 42, None):
        try:
            score_structure_for_direction(_make_ms_result("BULLISH"), bad)
            _check(f"SQ13.ValueError raised for {bad!r}", False, "Exception не поднято")
        except ValueError:
            _check(f"SQ13.ValueError raised for {bad!r}", True)
        except Exception as e:
            _check(f"SQ13.ValueError raised for {bad!r}", False, str(e))


def test_sq_score_bounds():
    """score всегда в диапазоне 0–100."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-14 — score ∈ [0, 100] для всех структур и направлений")
    print("────────────────────────────────────────────────────────────")
    structures = ["BULLISH", "BEARISH", "RANGE", "TRANSITION_BULLISH", "TRANSITION_BEARISH"]
    for s in structures:
        for d in ("LONG", "SHORT"):
            for raw in (0.0, 50.0, 100.0):
                r = _make_sq(s, d, score=raw)
                ok = 0.0 <= r["structure_score"] <= 100.0
                _check(f"SQ14.{s}.{d}.raw={raw} → in[0,100]", ok,
                       f"got {r['structure_score']}")


def test_sq_format():
    """Стабильность формата возвращаемого dict."""
    print("\n────────────────────────────────────────────────────────────")
    print("  SQ-15 — стабильный формат результата")
    print("────────────────────────────────────────────────────────────")
    expected_keys = {
        "structure_score", "raw_structure_score", "structure",
        "direction", "alignment", "bos_direction", "choch",
        "available", "reason",
    }
    for structure in ("BULLISH", "UNKNOWN"):
        r = _make_sq(structure, "LONG")
        missing = expected_keys - set(r.keys())
        _check(f"SQ15.{structure}.all_keys_present", not missing,
               f"missing: {missing}")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 2 — confidence.py (четыре компонента)
# ═══════════════════════════════════════════════════════════════════════════════

def test_conf_four_components():
    """Четыре доступных компонента — все учтены."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-1 — четыре компонента доступны")
    print("────────────────────────────────────────────────────────────")
    kwargs = _full_conf_inputs(60.0)
    r = calculate_confidence(**kwargs)
    _check("CONF1.available_components == 4", r["available_components"] == 4)
    _check("CONF1.confidence > 0",            r["confidence"] > 0)
    _check("CONF1.structure_quality available",
           r["components"]["structure_quality"]["available"] is True)

    # сумма effective_weight == 1
    total_eff = sum(v["effective_weight"] for v in r["components"].values())
    _check("CONF1.sum_effective_weight ≈ 1", abs(total_eff - 1.0) < 1e-9,
           f"got {total_eff:.6f}")

    # сумма contribution ≈ confidence
    total_contrib = sum(v["contribution"] for v in r["components"].values())
    _check("CONF1.sum_contribution ≈ confidence",
           abs(total_contrib - r["confidence"]) < 0.2,
           f"contrib={total_contrib:.2f}, conf={r['confidence']:.2f}")

    print(f"     confidence={r['confidence']}, label={r['label']}")
    print(f"     weights: ", end="")
    for k, v in r["components"].items():
        print(f"{k}={v['effective_weight']:.3f}", end=" ")
    print()


def test_conf_no_structure():
    """structure_quality отсутствует → перенормировка остальных трёх."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-2 — structure отсутствует → перенормировка")
    print("────────────────────────────────────────────────────────────")
    r = calculate_confidence(
        trend_quality={"trend_quality_score": 70.0},
        volume_quality={"volume_score": 60.0},
        breakout_quality={"breakout_score": 65.0},
        structure_quality=None,
    )
    _check("CONF2.available_components == 3", r["available_components"] == 3)
    _check("CONF2.structure not available",
           r["components"]["structure_quality"]["available"] is False)

    total_eff = sum(v["effective_weight"] for v in r["components"].values())
    _check("CONF2.sum_effective_weight ≈ 1", abs(total_eff - 1.0) < 1e-9,
           f"got {total_eff:.6f}")
    _check("CONF2.confidence > 0", r["confidence"] > 0)
    print(f"     confidence={r['confidence']}, 3-component normalised weights:")
    for k, v in r["components"].items():
        print(f"       {k}: eff_w={v['effective_weight']:.4f}")


def test_conf_only_structure():
    """Только structure_quality доступен."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-3 — только structure доступен")
    print("────────────────────────────────────────────────────────────")
    sq = {"structure_score": 80.0, "available": True}
    r = calculate_confidence(structure_quality=sq)
    _check("CONF3.available_components == 1", r["available_components"] == 1)
    _check("CONF3.structure eff_w == 1.0",
           r["components"]["structure_quality"]["effective_weight"] == 1.0)
    _check("CONF3.confidence == 80",  r["confidence"] == 80.0)
    print(f"     confidence={r['confidence']}")


def test_conf_structure_score_zero_but_available():
    """structure_score=0, available=True → компонент учтён."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-4 — structure_score=0 но available=True")
    print("────────────────────────────────────────────────────────────")
    sq = {"structure_score": 0.0, "available": True}
    r = calculate_confidence(
        trend_quality={"trend_quality_score": 80.0},
        structure_quality=sq,
    )
    _check("CONF4.available_components == 2", r["available_components"] == 2)
    _check("CONF4.structure available",
           r["components"]["structure_quality"]["available"] is True)
    _check("CONF4.structure score == 0",
           r["components"]["structure_quality"]["score"] == 0.0)
    print(f"     confidence={r['confidence']}")


def test_conf_structure_none():
    """structure_quality=None → available=False, не снижает confidence."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-5 — structure=None → не снижает confidence")
    print("────────────────────────────────────────────────────────────")
    r3 = calculate_confidence(
        trend_quality={"trend_quality_score": 80.0},
        volume_quality={"volume_score": 70.0},
        breakout_quality={"breakout_score": 75.0},
        structure_quality=None,
    )
    r4 = calculate_confidence(
        trend_quality={"trend_quality_score": 80.0},
        volume_quality={"volume_score": 70.0},
        breakout_quality={"breakout_score": 75.0},
        structure_quality={"structure_score": 75.0, "available": True},
    )
    _check("CONF5.3comp confidence <= 4comp confidence or within 10pts",
           abs(r3["confidence"] - r4["confidence"]) <= 10 or r3["confidence"] <= r4["confidence"])
    print(f"     3comp={r3['confidence']:.2f}, 4comp={r4['confidence']:.2f}")


def test_conf_nan_inf():
    """NaN / inf в structure_score → компонент недоступен."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-6 — structure NaN / inf → unavailable")
    print("────────────────────────────────────────────────────────────")
    for bad_val in (float("nan"), float("inf"), float("-inf")):
        sq = {"structure_score": bad_val, "available": True}
        r = calculate_confidence(structure_quality=sq)
        _check(f"CONF6.{bad_val} → not available",
               r["components"]["structure_quality"]["available"] is False,
               f"score={bad_val}")


def test_conf_renormalization():
    """Перенормировка: при N компонентах сумма eff_weight == 1."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-7 — перенормировка при любом наборе компонентов")
    print("────────────────────────────────────────────────────────────")
    combos = [
        dict(trend_quality={"trend_quality_score": 70}),
        dict(volume_quality={"volume_score": 60}),
        dict(breakout_quality={"breakout_score": 65}),
        dict(structure_quality={"structure_score": 60, "available": True}),
        dict(trend_quality={"trend_quality_score": 70},
             breakout_quality={"breakout_score": 65}),
        dict(trend_quality={"trend_quality_score": 70},
             structure_quality={"structure_score": 60, "available": True}),
    ]
    for i, kwargs in enumerate(combos):
        r = calculate_confidence(**kwargs)
        total_eff = sum(v["effective_weight"] for v in r["components"].values())
        _check(f"CONF7.combo{i+1}.sum_eff_w ≈ 1", abs(total_eff - 1.0) < 1e-9,
               f"sum={total_eff:.6f}")
        total_contrib = sum(v["contribution"] for v in r["components"].values())
        _check(f"CONF7.combo{i+1}.sum_contrib ≈ confidence",
               abs(total_contrib - r["confidence"]) < 0.2,
               f"contrib={total_contrib:.2f}, conf={r['confidence']:.2f}")


def test_conf_weights_sum():
    """WEIGHTS в confidence.py суммируются до 1.0."""
    print("\n────────────────────────────────────────────────────────────")
    print("  CONF-8 — WEIGHTS суммируются до 1.0")
    print("────────────────────────────────────────────────────────────")
    w_sum = sum(WEIGHTS.values())
    _check("CONF8.WEIGHTS sum == 1.0", abs(w_sum - 1.0) < 1e-9, f"sum={w_sum:.6f}")
    _check("CONF8.trend_quality == 0.40",    abs(WEIGHTS["trend_quality"]     - 0.40) < 1e-9)
    _check("CONF8.volume_quality == 0.15",   abs(WEIGHTS["volume_quality"]    - 0.15) < 1e-9)
    _check("CONF8.breakout_quality == 0.30", abs(WEIGHTS["breakout_quality"]  - 0.30) < 1e-9)
    _check("CONF8.structure_quality == 0.15",abs(WEIGHTS["structure_quality"] - 0.15) < 1e-9)


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 3 — quality_pipeline.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_pipeline_ms_calculated_once():
    """Market Structure вычисляется один раз в analyze_both_directions."""
    print("\n────────────────────────────────────────────────────────────")
    print("  PIPE-1 — Market Structure вычисляется один раз")
    print("────────────────────────────────────────────────────────────")
    df = _bullish_df()
    result = analyze_both_directions(df)

    # Оба результата должны содержать одинаковый ms_result (идентичные данные)
    ms_long  = result["LONG"].get("market_structure")
    ms_short = result["SHORT"].get("market_structure")

    _check("PIPE1.LONG.market_structure present",  ms_long  is not None)
    _check("PIPE1.SHORT.market_structure present", ms_short is not None)

    # Если ms вычислен один раз и передан обоим, structure и score должны совпасть
    if ms_long and ms_short:
        _check("PIPE1.same structure in both",
               ms_long["structure"] == ms_short["structure"],
               f"LONG={ms_long['structure']}, SHORT={ms_short['structure']}")
        _check("PIPE1.same score in both",
               ms_long["structure_score"] == ms_short["structure_score"],
               f"LONG={ms_long['structure_score']}, SHORT={ms_short['structure_score']}")
    print(f"     structure={ms_long['structure'] if ms_long else 'None'}")


def test_pipeline_structure_passed_both_dirs():
    """structure_quality присутствует в LONG и SHORT результатах."""
    print("\n────────────────────────────────────────────────────────────")
    print("  PIPE-2 — structure_quality передан обоим направлениям")
    print("────────────────────────────────────────────────────────────")
    df = _bullish_df()
    result = analyze_both_directions(df)

    sq_long  = result["LONG"].get("structure_quality")
    sq_short = result["SHORT"].get("structure_quality")

    _check("PIPE2.LONG.structure_quality present",  sq_long  is not None)
    _check("PIPE2.SHORT.structure_quality present", sq_short is not None)

    if sq_long and sq_short:
        _check("PIPE2.LONG.direction == LONG",   sq_long["direction"]  == "LONG")
        _check("PIPE2.SHORT.direction == SHORT",  sq_short["direction"] == "SHORT")
        _check("PIPE2.structure same for both",
               sq_long["structure"] == sq_short["structure"])
        print(f"     LONG:  structure={sq_long['structure']}, "
              f"alignment={sq_long['alignment']}, score={sq_long['structure_score']}")
        print(f"     SHORT: structure={sq_short['structure']}, "
              f"alignment={sq_short['alignment']}, score={sq_short['structure_score']}")


def test_pipeline_aligned_vs_opposed():
    """Aligned structure улучшает оценку, opposed — ухудшает."""
    print("\n────────────────────────────────────────────────────────────")
    print("  PIPE-3 — aligned structure улучшает, opposed ухудшает")
    print("────────────────────────────────────────────────────────────")
    bullish_df = _bullish_df()
    bearish_df = _bearish_df()

    r_bull_long  = analyze_quality(bullish_df, "LONG")
    r_bull_short = analyze_quality(bullish_df, "SHORT")

    sq_bull_long  = r_bull_long.get("structure_quality",  {}) or {}
    sq_bull_short = r_bull_short.get("structure_quality", {}) or {}

    if sq_bull_long.get("available") and sq_bull_short.get("available"):
        _check("PIPE3.BULLISH: LONG score > SHORT score",
               sq_bull_long["structure_score"] > sq_bull_short["structure_score"],
               f"LONG={sq_bull_long['structure_score']}, SHORT={sq_bull_short['structure_score']}")
    else:
        _check("PIPE3.structure available for comparison",
               False, "один из компонентов недоступен")

    print(f"     BULLISH → LONG alignment={sq_bull_long.get('alignment')}, "
          f"score={sq_bull_long.get('structure_score')}")
    print(f"     BULLISH → SHORT alignment={sq_bull_short.get('alignment')}, "
          f"score={sq_bull_short.get('structure_score')}")


def test_pipeline_strong_opposition_filter():
    """Strong opposition (OPPOSED, score<25) → WAIT даже при confirmed breakout."""
    print("\n────────────────────────────────────────────────────────────")
    print("  PIPE-4 — фильтр сильного противодействия структуры")
    print("────────────────────────────────────────────────────────────")
    # Создаём синтетический вызов _analyze_direction с патчем structure_quality
    from quality_pipeline import _analyze_direction, STRONG_OPPOSITION_THRESHOLD
    import unittest.mock as mock

    bearish_ms = {
        "structure":       "BEARISH",
        "structure_score": 85.0,   # сильный медвежий → LONG score = 100-85 = 15 < 25
        "bos":             {"confirmed": False, "direction": "NONE", "price": None, "candle_idx": None},
        "choch":           "NONE",
        "pivot_highs":     [],
        "pivot_lows":      [],
        "classified_highs": [],
        "classified_lows":  [],
        "reason":          "synthetic",
    }

    df = _bullish_df()   # используем бычий df чтобы breakout мог сработать

    # Патчим analyze_market_structure чтобы вернуть сильную медвежью структуру
    with mock.patch("quality_pipeline.analyze_market_structure", return_value=bearish_ms):
        result = analyze_both_directions(df)

    long_sq = result["LONG"].get("structure_quality", {}) or {}
    long_signal = result["LONG"].get("signal")

    print(f"     LONG structure_quality: alignment={long_sq.get('alignment')}, "
          f"score={long_sq.get('structure_score')}")
    print(f"     LONG signal={long_signal}")

    if long_sq.get("alignment") == "OPPOSED" and \
       long_sq.get("structure_score", 100) < STRONG_OPPOSITION_THRESHOLD:
        _check("PIPE4.strong_opposition → WAIT", long_signal == "WAIT",
               f"signal={long_signal}")
        reason = result["LONG"].get("reason", "")
        _check("PIPE4.reason mentions opposition",
               "opposition" in reason.lower() or "opposed" in reason.lower(),
               f"reason={reason}")
    else:
        # Структура не дошла до порога — тест пропускается как N/A
        sq_score = long_sq.get("structure_score", "N/A")
        _check("PIPE4.structure_score < threshold or alignment==OPPOSED",
               long_sq.get("alignment") == "OPPOSED",
               f"alignment={long_sq.get('alignment')}, score={sq_score}")


def test_pipeline_no_signal_without_breakout():
    """Market Structure сама не создаёт сигнал — нужен confirmed breakout."""
    print("\n────────────────────────────────────────────────────────────")
    print("  PIPE-5 — структура без breakout не создаёт сигнал")
    print("────────────────────────────────────────────────────────────")
    # DataFrame с недостаточным количеством свечей для пивотов
    # → breakout will not be confirmed
    import unittest.mock as mock

    bullish_ms = {
        "structure":       "BULLISH",
        "structure_score": 90.0,
        "bos":             {"confirmed": True, "direction": "BULLISH",
                            "price": 100.0, "candle_idx": 5},
        "choch":           "NONE",
        "pivot_highs":     [],
        "pivot_lows":      [],
        "classified_highs": [],
        "classified_lows":  [],
        "reason":          "synthetic",
    }

    df = _bullish_df()

    # Патчим breakout_quality чтобы confirmed=False
    with mock.patch("quality_pipeline.analyze_market_structure", return_value=bullish_ms), \
         mock.patch("quality_pipeline.calculate_breakout_quality",
                    return_value={
                        "breakout_score": 0.0,
                        "confirmed":      False,
                        "reason":         "mocked no breakout",
                        "components": {"cross": 0, "close_distance": 0,
                                       "candle_body": 0, "rejection_wick": 0},
                    }):
        result_long = analyze_quality(df, "LONG")

    _check("PIPE5.no_breakout → WAIT",
           result_long["signal"] == "WAIT",
           f"signal={result_long['signal']}")
    _check("PIPE5.confirmed == False",
           result_long["confirmed"] is False)
    print(f"     signal={result_long['signal']}, reason={result_long['reason'][:80]}")


def test_pipeline_active_signal_confidence_invariant():
    """Инвариант: active signal (LONG/SHORT) всегда имеет confidence >= SIGNAL_THRESHOLD."""
    print("\n────────────────────────────────────────────────────────────")
    print("  PIPE-6 — инвариант: active signal → confidence >= 50")
    print("────────────────────────────────────────────────────────────")
    dfs = [_bullish_df(), _bearish_df()]
    violations = 0
    for df in dfs:
        result = analyze_both_directions(df)
        for dir_key in ("LONG", "SHORT"):
            r = result[dir_key]
            sig = r.get("signal")
            conf = float(r.get("confidence", {}).get("confidence", 0.0))
            if sig in ("LONG", "SHORT"):
                if conf < SIGNAL_THRESHOLD:
                    violations += 1
                    _check(f"PIPE6.{dir_key}.{sig}: conf={conf:.1f} >= {SIGNAL_THRESHOLD}",
                           False, f"VIOLATION: active signal with conf={conf:.1f}")
    _check("PIPE6.no_invariant_violations", violations == 0,
           f"{violations} нарушений найдено")
    print(f"     Проверено {len(dfs)*2} направлений, нарушений: {violations}")


def test_pipeline_result_keys():
    """Ключи market_structure и structure_quality присутствуют в возвращаемом dict."""
    print("\n────────────────────────────────────────────────────────────")
    print("  PIPE-7 — новые ключи присутствуют в результате")
    print("────────────────────────────────────────────────────────────")
    df = _bullish_df()
    for direction in ("LONG", "SHORT"):
        r = analyze_quality(df, direction)
        _check(f"PIPE7.{direction}.market_structure key present",
               "market_structure" in r)
        _check(f"PIPE7.{direction}.structure_quality key present",
               "structure_quality" in r)

    r_both = analyze_both_directions(df)
    for dir_key in ("LONG", "SHORT"):
        r = r_both[dir_key]
        _check(f"PIPE7.both.{dir_key}.market_structure present",
               "market_structure" in r)
        _check(f"PIPE7.both.{dir_key}.structure_quality present",
               "structure_quality" in r)


# ═══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════════

def _run_all():
    tests = [
        # structure_quality.py
        test_sq_bullish_long,
        test_sq_bullish_short,
        test_sq_bearish_short,
        test_sq_bearish_long,
        test_sq_range_both,
        test_sq_transition_bullish,
        test_sq_transition_bearish,
        test_sq_bos_long,
        test_sq_bos_short,
        test_sq_choch_aligned_caps,
        test_sq_choch_opposed_penalty,
        test_sq_unknown_unavailable,
        test_sq_invalid_direction,
        test_sq_score_bounds,
        test_sq_format,
        # confidence.py
        test_conf_four_components,
        test_conf_no_structure,
        test_conf_only_structure,
        test_conf_structure_score_zero_but_available,
        test_conf_structure_none,
        test_conf_nan_inf,
        test_conf_renormalization,
        test_conf_weights_sum,
        # quality_pipeline.py
        test_pipeline_ms_calculated_once,
        test_pipeline_structure_passed_both_dirs,
        test_pipeline_aligned_vs_opposed,
        test_pipeline_strong_opposition_filter,
        test_pipeline_no_signal_without_breakout,
        test_pipeline_active_signal_confidence_invariant,
        test_pipeline_result_keys,
    ]

    print("\n" + "═"*60)
    print("  STRUCTURE INTEGRATION TESTS (v0.5)")
    print("═"*60)

    failed_tests = []
    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            failed_tests.append((test_fn.__name__, str(e)))
            print(f"\n  ✗ EXCEPTION in {test_fn.__name__}: {e}")
            traceback.print_exc()

    # ── итог ──────────────────────────────────────────────────────────────────
    total  = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed

    sq_labels   = [l for l, ok, _ in _results if l.startswith("SQ")]
    sq_passed   = sum(1 for l, ok, _ in _results if l.startswith("SQ") and ok)
    conf_labels = [l for l, ok, _ in _results if l.startswith("CONF")]
    conf_passed = sum(1 for l, ok, _ in _results if l.startswith("CONF") and ok)
    pipe_labels = [l for l, ok, _ in _results if l.startswith("PIPE")]
    pipe_passed = sum(1 for l, ok, _ in _results if l.startswith("PIPE") and ok)

    print("\n" + "═"*60)
    print(f"  Structure integration tests: {passed}/{total}")
    print()
    print(f"  structure_quality tests:  {sq_passed}/{len(sq_labels)}")
    print(f"  confidence tests:         {conf_passed}/{len(conf_labels)}")
    print(f"  quality_pipeline tests:   {pipe_passed}/{len(pipe_labels)}")

    if failed_tests:
        print(f"\n  Exceptions in {len(failed_tests)} test(s):")
        for name, err in failed_tests:
            print(f"    - {name}: {err}")

    if failed > 0:
        print(f"\n  FAILED checks: {failed}")
        for label, ok, detail in _results:
            if not ok:
                print(f"    ✗ {label}" + (f" — {detail}" if detail else ""))

    if failed == 0 and not failed_tests:
        print("\n  STRUCTURE INTEGRATION STATUS: PASSED")
    else:
        print("\n  STRUCTURE INTEGRATION STATUS: FAILED")
    print("═"*60)

    return passed, total


if __name__ == "__main__":
    passed, total = _run_all()
    sys.exit(0 if passed == total else 1)
