from trendlines import (
    find_pivots,
    create_trendline,
    check_break
)


def analyze_timeframe(df):

    highs, lows = find_pivots(df)

    down_line = create_trendline(
        highs
    )

    up_line = create_trendline(
        lows
    )

    long_signal = check_break(
        df,
        down_line,
        "LONG"
    )

    short_signal = check_break(
        df,
        up_line,
        "SHORT"
    )

    if long_signal == "LONG":
        return {
            "trend": "BULLISH",
            "signal": "LONG",
            "score": 1
        }

    if short_signal == "SHORT":
        return {
            "trend": "BEARISH",
            "signal": "SHORT",
            "score": 1
        }

    return {
        "trend": "NEUTRAL",
        "signal": "WAIT",
        "score": 0
    }
