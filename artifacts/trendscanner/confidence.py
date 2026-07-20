"""
confidence.py
~~~~~~~~~~~~~
Объединяет оценки независимых модулей качества и рассчитывает
итоговую уверенность сигнала (Confidence Score, 0–100).

Текущие компоненты:
    • trend_quality   — качество трендовой линии  (базовый вес 70%)
    • volume_quality  — подтверждение объёмом      (базовый вес 30%)
    • breakout_quality — сила и чёткость пробоя   (пока None; зарезервирован)

Планируемые компоненты (добавляются без изменения существующего кода):
    • atr_quality       — качество пробоя в единицах ATR
    • market_structure  — структура рынка (HH/HL, LH/LL)
    • multi_tf_quality  — согласованность таймфреймов
    • ml_score          — оценка ML-модели

ВАЖНО — перенормировка весов:
    Если компонент отсутствует (None или {}), его вес перераспределяется
    пропорционально между доступными компонентами.
    Это не допускает искусственного занижения confidence при неполных данных.

Нет зависимостей от scanner.py, Streamlit или любых других модулей проекта.
Принимает только готовые словари с оценками.
"""

from __future__ import annotations
import math


# ─── базовые веса компонентов ─────────────────────────────────────────────────
# Сумма активных (не None) весов при полном наборе = 1.0.
# При добавлении нового компонента перераспределить веса здесь,
# не меняя сигнатуры calculate_confidence.

WEIGHTS: dict[str, float] = {
    "trend_quality":   0.70,
    "volume_quality":  0.30,
    "breakout_quality": 0.00,   # зарезервирован; при включении: перераспределить веса
    # "atr_quality":       0.00,
    # "market_structure":  0.00,
    # "multi_tf_quality":  0.00,
    # "ml_score":          0.00,
}


# ─── метки уверенности ───────────────────────────────────────────────────────

def confidence_label(confidence: float) -> str:
    """
    Переводит числовую оценку Confidence в текстовую метку.

    Параметры:
        confidence — float или int, значение в диапазоне 0–100

    Возвращает:
        str:
            "LOW"       — 0–39
            "MEDIUM"    — 40–69
            "HIGH"      — 70–84
            "VERY HIGH" — 85–100

    Пример:
        >>> confidence_label(82)
        'HIGH'
        >>> confidence_label(91)
        'VERY HIGH'
    """
    if confidence >= 85:
        return "VERY HIGH"
    elif confidence >= 70:
        return "HIGH"
    elif confidence >= 40:
        return "MEDIUM"
    else:
        return "LOW"


# ─── вспомогательная функция извлечения очков ────────────────────────────────

def _extract_score(quality_dict: dict, key: str) -> float:
    """
    Безопасно извлекает числовую оценку из словаря качества.
    Возвращает 0.0 если ключ отсутствует или значение некорректно.

    Параметры:
        quality_dict — словарь с оценками от одного из модулей качества
        key          — имя ключа с числовой оценкой

    Возвращает:
        float — значение оценки (0.0 при ошибке)
    """
    try:
        return float(quality_dict.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


# ─── основная функция ─────────────────────────────────────────────────────────

def calculate_confidence(
    trend_quality:    dict,
    volume_quality:   dict,
    breakout_quality: dict | None = None,   # зарезервирован для будущей интеграции
) -> dict:
    """
    Объединяет оценки всех модулей качества и рассчитывает
    итоговый Confidence Score с перенормировкой весов.

    Перенормировка:
        Компонент считается «отсутствующим», если его dict пустой ({}) или None.
        В этом случае его вес перераспределяется пропорционально между
        остальными доступными компонентами — confidence не занижается.

    Пример:
        trend_quality={}   → вес 0.70 убирается, volume_quality получает вес 1.0
        breakout_quality={} → не учитывается, пока WEIGHTS["breakout_quality"]=0.00

    Параметры:
        trend_quality    — dict от trend_quality.calc_trend_quality()
                           Ожидаемый ключ: "trend_quality_score"
        volume_quality   — dict от volume_quality.score_volume()
                           Ожидаемый ключ: "volume_score"
        breakout_quality — dict от breakout_quality.calculate_breakout_quality()
                           Ожидаемый ключ: "breakout_score"  (по умолчанию None)

    Возвращает:
        dict:
            confidence       — int,  итоговая оценка 0–100
            label            — str,  текстовая метка ("LOW" / "MEDIUM" / "HIGH" / "VERY HIGH")
            components       — dict, вклад каждого компонента (оценка, базовый вес, эффективный вес, contribution)
            renormalized     — bool, True если хотя бы один компонент отсутствовал

    Добавление нового компонента в будущем:
        1. Добавить параметр с дефолтом None: `atr_quality: dict | None = None`
        2. Добавить его в _all_components ниже
        3. Обновить WEIGHTS (перераспределить сумму = 1.0 при полном наборе)
    """

    # ── регистр всех компонентов ──────────────────────────────────────────────
    # Порядок: (имя, dict_или_None, score_key)
    _all: list[tuple[str, dict | None, str]] = [
        ("trend_quality",    trend_quality,    "trend_quality_score"),
        ("volume_quality",   volume_quality,   "volume_score"),
        ("breakout_quality", breakout_quality, "breakout_score"),
    ]

    # ── фильтруем доступные компоненты (non-None и non-empty) ─────────────────
    available: list[tuple[str, dict, str]] = [
        (name, d, key)
        for name, d, key in _all
        if d and WEIGHTS.get(name, 0.0) > 0.0
    ]

    if not available:
        return {
            "confidence":   0,
            "label":        "LOW",
            "components":   {},
            "renormalized": False,
        }

    # ── перенормировка весов ──────────────────────────────────────────────────
    total_base_weight = sum(WEIGHTS.get(name, 0.0) for name, _, _ in available)
    renormalized = total_base_weight < (sum(WEIGHTS.values()) - 1e-9)

    # ── взвешенная сумма (перенормированная) ──────────────────────────────────
    raw = 0.0
    components: dict[str, dict] = {}

    for name, d, key in available:
        score = _extract_score(d, key)
        base_w = WEIGHTS.get(name, 0.0)
        eff_w  = base_w / total_base_weight if total_base_weight > 0 else 0.0
        contribution = score * eff_w

        if not math.isfinite(score):
            score = 0.0
        if not math.isfinite(contribution):
            contribution = 0.0

        raw += contribution
        components[name] = {
            "score":          int(score),
            "weight":         round(base_w, 4),
            "effective_weight": round(eff_w, 4),
            "contribution":   round(contribution, 1),
        }

    # Ограничиваем диапазон 0–100
    if not math.isfinite(raw):
        raw = 0.0
    confidence = int(max(0.0, min(100.0, raw)))

    return {
        "confidence":   confidence,
        "label":        confidence_label(confidence),
        "components":   components,
        "renormalized": renormalized,
    }
