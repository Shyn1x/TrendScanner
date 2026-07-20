import ccxt
import pandas as pd

from trendlines import find_pivots, create_trendline, check_break

exchange = ccxt.kucoinfutures()


def get_futures_symbols():

    markets = exchange.load_markets()

    symbols = []

    for symbol, data in markets.items():

        if (
            symbol.endswith("/USDT")
            and data.get("active")
        ):
            symbols.append(symbol)

    return symbols


def get_data(symbol: str, timeframe: str) -> pd.DataFrame:
    candles = exchange.fetch_ohlcv(symbol, timeframe, limit=300)
    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    return df


def trend_signal(df: pd.DataFrame) -> str:
    highs, lows = find_pivots(df)

    down_line = create_trendline(highs)
    up_line   = create_trendline(lows)

    long_signal  = check_break(df, down_line, "LONG")
    short_signal = check_break(df, up_line,   "SHORT")

    if long_signal == "LONG":
        return "LONG"
    if short_signal == "SHORT":
        return "SHORT"
    return "WAIT"


def analyze(symbol: str) -> dict:
    from config import TIMEFRAMES
    result = {}
    for tf in TIMEFRAMES:
        try:
            df = get_data(symbol, tf)
            result[tf] = trend_signal(df)
        except Exception:
            result[tf] = "ERROR"
    return result
