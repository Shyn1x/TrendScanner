from scanner import get_data
from analysis import analyze_timeframe


TIMEFRAMES = [
    "1M",
    "1w",
    "1d",
    "4h",
    "1h"
]


def multi_analysis(symbol):

    result = {}

    total_score = 0

    long_count = 0
    short_count = 0


    for tf in TIMEFRAMES:

        try:

            df = get_data(
                symbol,
                tf
            )

            analysis = analyze_timeframe(
                df
            )

            result[tf] = analysis

            # считаем общий баланс

            if analysis["signal"] == "LONG":

                total_score += 20
                long_count += 1

            elif analysis["signal"] == "SHORT":

                total_score -= 20
                short_count += 1

        except Exception as e:

            result[tf] = {
                "trend": "ERROR",
                "signal": str(e),
                "score": 0
            }


    # Финальное решение

    if total_score >= 40:

        final_signal = "LONG"

    elif total_score <= -40:

        final_signal = "SHORT"

    else:

        final_signal = "WAIT"


    result["FINAL"] = {

        "signal": final_signal,

        "score": abs(total_score),

        "long_timeframes": long_count,

        "short_timeframes": short_count

    }

    return result
