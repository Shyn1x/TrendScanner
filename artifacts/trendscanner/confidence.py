"""
confidence.py
~~~~~~~~~~~~~
Объединяет оценки независимых модулей качества и рассчитывает
итоговую уверенность сигнала (Confidence Score, 0–100).

Текущие компоненты:
    • trend_quality  — качество трендовой линии  (вес 70%)
    • volume_quality — подтверждение объёмом      (вес 30%)

Планируемые компоненты (добавляются без изменения существующего кода):
    • atr_quality       — качество пробоя в единицах ATR
    • breakout_quality  — сила и чёткость пробоя
    • market_structure  — структура рынка (HH/HL, LH/LL)
    • multi_tf_quality  — согласованность таймфреймов
    • ml_score          — оценка ML-модели

Нет зависимостей от scanner.py, Streamlit или любых других модулей проекта.
Принимает только готовые словари с оценками.
"""


# ─── веса компонентов (сумма должна равняться 1.0) ───────────────────────────

WEIGHTS = {
    "trend_quality":  0.70,
    "volume_quality": 0.30,

    # Зарезервированные веса для будущих компонентов.
    # Когда добавляется новый компонент — перераспределить веса здесь,
    # не меняя сигнатуры calculate_confidence.
    # "atr_quality":       0.00,
    # "breakout_quality":  0.00,
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
    trend_quality: dict,
    volume_quality: dict,
) -> dict:
    """
    Объединяет оценки всех модулей качества и рассчитывает
    итоговый Confidence Score.

    Формула:
        confidence = trend_quality_score * 0.70
                   + volume_score        * 0.30

    Результат ограничен диапазоном 0–100.

    Параметры:
        trend_quality  — dict от trend_quality.calc_trend_quality()
                         Ожидаемый ключ: "trend_quality_score"
        volume_quality — dict от volume_quality.score_volume()
                         Ожидаемый ключ: "volume_score"

    Возвращает:
        dict:
            confidence  — int,  итоговая оценка 0–100
            label       — str,  текстовая метка ("LOW" / "MEDIUM" / "HIGH" / "VERY HIGH")
            components  — dict, вклад каждого компонента (оценка и вес)

    Пример вывода:
        {
            "confidence": 79,
            "label": "HIGH",
            "components": {
                "trend_quality": {
                    "score":  85,
                    "weight": 0.70,
                    "contribution": 59.5
                },
                "volume_quality": {
                    "score":  65,
                    "weight": 0.30,
                    "contribution": 19.5
                }
            }
        }

    Добавление нового компонента в будущем:
        1. Добавить новый параметр в сигнатуру функции с дефолтом None:
               def calculate_confidence(..., atr_quality: dict | None = None):
                   atr_quality = atr_quality or {}
        2. Добавить строку в словарь scores ниже
        3. Обновить WEIGHTS в начале файла (сумма = 1.0)
    """

    # Извлекаем оценки из словарей компонентов
    scores = {
        "trend_quality":  _extract_score(trend_quality,  "trend_quality_score"),
        "volume_quality": _extract_score(volume_quality, "volume_score"),

        # Будущие компоненты добавляются здесь:
        # "atr_quality":      _extract_score(atr_quality,      "atr_score"),
        # "breakout_quality": _extract_score(breakout_quality, "breakout_score"),
        # "market_structure": _extract_score(market_structure, "structure_score"),
        # "multi_tf_quality": _extract_score(multi_tf_quality, "multi_tf_score"),
        # "ml_score":         _extract_score(ml_score,         "ml_score"),
    }

    # Взвешенная сумма
    raw = sum(
        scores[component] * WEIGHTS[component]
        for component in scores
    )

    # Ограничиваем диапазон 0–100
    confidence = int(max(0.0, min(100.0, raw)))

    # Формируем детализацию компонентов
    components = {
        component: {
            "score":        int(scores[component]),
            "weight":       WEIGHTS[component],
            "contribution": round(scores[component] * WEIGHTS[component], 1),
        }
        for component in scores
    }

    return {
        "confidence": confidence,
        "label":      confidence_label(confidence),
        "components": components,
    }
