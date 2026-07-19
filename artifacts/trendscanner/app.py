import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone
import time

st.set_page_config(
    page_title="Trendscanner",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .stMetric { background: #1a1d23; border-radius: 8px; padding: 12px; }
    .signal-bullish  { color: #00e676; font-weight: 700; }
    .signal-bearish  { color: #ff5252; font-weight: 700; }
    .signal-neutral  { color: #90a4ae; font-weight: 700; }
    div[data-testid="stExpander"] { border: 1px solid #2a2d35; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ── Indicator helpers ──────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def bollinger_bands(series: pd.Series, period=20, num_std=2):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    return upper, sma, lower

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()

def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["close"]
    df["ema9"]   = ema(c, 9)
    df["ema21"]  = ema(c, 21)
    df["ema50"]  = ema(c, 50)
    df["ema200"] = ema(c, 200)
    df["rsi"]    = rsi(c)
    df["macd_line"], df["macd_signal"], df["macd_hist"] = macd(c)
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger_bands(c)
    df["atr"]    = atr(df["high"], df["low"], c)
    vol_sma      = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / vol_sma
    return df

# ── Trend scoring ──────────────────────────────────────────────────────────────

def score_trend(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    score = 0
    reasons = []

    # EMA stack
    if last["ema9"] > last["ema21"] > last["ema50"]:
        score += 2; reasons.append("✅ EMA9 > EMA21 > EMA50 (bullish stack)")
    elif last["ema9"] < last["ema21"] < last["ema50"]:
        score -= 2; reasons.append("❌ EMA9 < EMA21 < EMA50 (bearish stack)")

    # Price vs EMA200
    if last["close"] > last["ema200"]:
        score += 1; reasons.append("✅ Price above EMA200")
    else:
        score -= 1; reasons.append("❌ Price below EMA200")

    # RSI
    r = last["rsi"]
    if pd.isna(r):
        pass
    elif r > 60:
        score += 1; reasons.append(f"✅ RSI {r:.1f} — bullish momentum")
    elif r < 40:
        score -= 1; reasons.append(f"❌ RSI {r:.1f} — bearish momentum")
    elif r > 70:
        score -= 1; reasons.append(f"⚠️ RSI {r:.1f} — overbought")
    elif r < 30:
        score += 1; reasons.append(f"⚠️ RSI {r:.1f} — oversold")
    else:
        reasons.append(f"➖ RSI {r:.1f} — neutral zone")

    # MACD
    if last["macd_hist"] > 0 and prev["macd_hist"] < last["macd_hist"]:
        score += 2; reasons.append("✅ MACD histogram expanding above zero")
    elif last["macd_hist"] < 0 and prev["macd_hist"] > last["macd_hist"]:
        score -= 2; reasons.append("❌ MACD histogram expanding below zero")
    elif last["macd_line"] > last["macd_signal"]:
        score += 1; reasons.append("✅ MACD line above signal")
    elif last["macd_line"] < last["macd_signal"]:
        score -= 1; reasons.append("❌ MACD line below signal")

    # Bollinger Band position
    bb_pct = (last["close"] - last["bb_lower"]) / (last["bb_upper"] - last["bb_lower"] + 1e-10)
    if bb_pct > 0.8:
        score += 1; reasons.append(f"✅ Price near upper BB ({bb_pct:.0%})")
    elif bb_pct < 0.2:
        score -= 1; reasons.append(f"❌ Price near lower BB ({bb_pct:.0%})")

    # Volume confirmation
    if last["vol_ratio"] > 1.5:
        direction = "bullish" if last["close"] > prev["close"] else "bearish"
        modifier = 1 if direction == "bullish" else -1
        score += modifier
        reasons.append(f"{'✅' if modifier > 0 else '❌'} High volume ({last['vol_ratio']:.1f}x avg) — {direction} confirmation")

    # Signal label
    if score >= 4:
        label, color = "STRONG BULL 🐂", "bullish"
    elif score >= 2:
        label, color = "BULLISH ↑", "bullish"
    elif score <= -4:
        label, color = "STRONG BEAR 🐻", "bearish"
    elif score <= -2:
        label, color = "BEARISH ↓", "bearish"
    else:
        label, color = "NEUTRAL ↔", "neutral"

    return {
        "score": score,
        "label": label,
        "color": color,
        "reasons": reasons,
        "rsi": last["rsi"],
        "macd_hist": last["macd_hist"],
        "close": last["close"],
        "ema9": last["ema9"],
        "ema50": last["ema50"],
        "atr": last["atr"],
        "vol_ratio": last["vol_ratio"],
    }

# ── CCXT helpers ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, limit: int = 300):
    try:
        exchange_cls = getattr(ccxt, exchange_id)
        exchange = exchange_cls({"enableRateLimit": True})
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df, None
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=300, show_spinner=False)
def get_markets(exchange_id: str):
    try:
        exchange_cls = getattr(ccxt, exchange_id)
        exchange = exchange_cls({"enableRateLimit": True})
        markets = exchange.load_markets()
        symbols = sorted([s for s in markets.keys() if "/" in s and ":USDT" not in s])
        return symbols, None
    except Exception as e:
        return [], str(e)

# ── Charting ───────────────────────────────────────────────────────────────────

def build_chart(df: pd.DataFrame, symbol: str, timeframe: str) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.55, 0.25, 0.20],
        subplot_titles=[f"{symbol} — {timeframe}", "MACD", "RSI"],
    )

    # ── Price + BBands + EMAs
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        increasing_line_color="#00e676", decreasing_line_color="#ff5252",
        name="Price",
    ), row=1, col=1)

    for col, color, dash, name in [
        ("bb_upper", "rgba(100,149,237,0.4)", "dot", "BB Upper"),
        ("bb_mid",   "rgba(100,149,237,0.4)", "dash", "BB Mid"),
        ("bb_lower", "rgba(100,149,237,0.4)", "dot", "BB Lower"),
        ("ema9",   "#ffd740", "solid", "EMA 9"),
        ("ema21",  "#ff6d00", "solid", "EMA 21"),
        ("ema50",  "#40c4ff", "dash", "EMA 50"),
        ("ema200", "#ea80fc", "dash", "EMA 200"),
    ]:
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col], mode="lines",
            line=dict(color=color, width=1, dash=dash), name=name,
        ), row=1, col=1)

    # BB fill
    fig.add_trace(go.Scatter(
        x=pd.concat([df.index.to_series(), df.index.to_series()[::-1]]),
        y=pd.concat([df["bb_upper"], df["bb_lower"][::-1]]),
        fill="toself", fillcolor="rgba(100,149,237,0.05)",
        line=dict(color="rgba(0,0,0,0)"), showlegend=False,
    ), row=1, col=1)

    # Volume bars
    colors = ["#00e676" if c >= o else "#ff5252"
              for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"], marker_color=colors,
        opacity=0.4, name="Volume", showlegend=False,
    ), row=1, col=1)

    # ── MACD
    fig.add_trace(go.Scatter(
        x=df.index, y=df["macd_line"], mode="lines",
        line=dict(color="#ffd740", width=1.5), name="MACD",
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["macd_signal"], mode="lines",
        line=dict(color="#ff6d00", width=1.5), name="Signal",
    ), row=2, col=1)
    hist_colors = ["#00e676" if v >= 0 else "#ff5252" for v in df["macd_hist"]]
    fig.add_trace(go.Bar(
        x=df.index, y=df["macd_hist"], marker_color=hist_colors,
        opacity=0.7, name="Histogram", showlegend=False,
    ), row=2, col=1)

    # ── RSI
    fig.add_trace(go.Scatter(
        x=df.index, y=df["rsi"], mode="lines",
        line=dict(color="#40c4ff", width=1.5), name="RSI",
    ), row=3, col=1)
    for level, color in [(70, "rgba(255,82,82,0.3)"), (30, "rgba(0,230,118,0.3)"), (50, "rgba(144,164,174,0.2)")]:
        fig.add_hline(y=level, line_color=color, line_dash="dash", row=3, col=1)

    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#141720",
        font=dict(color="#e0e0e0", family="monospace"),
        legend=dict(orientation="h", y=1.05, bgcolor="rgba(0,0,0,0)"),
        xaxis_rangeslider_visible=False,
        margin=dict(l=0, r=0, t=40, b=0),
        height=700,
    )
    for i in range(1, 4):
        fig.update_xaxes(gridcolor="#1f2329", row=i, col=1)
        fig.update_yaxes(gridcolor="#1f2329", row=i, col=1)

    return fig

# ── Sidebar ────────────────────────────────────────────────────────────────────

SUPPORTED_EXCHANGES = [
    "binance", "kraken", "coinbasepro", "bybit", "okx", "kucoin",
    "bitfinex", "gateio", "huobi",
]

TIMEFRAMES = {
    "1m": "1 Minute", "5m": "5 Minutes", "15m": "15 Minutes",
    "30m": "30 Min", "1h": "1 Hour", "4h": "4 Hours",
    "1d": "1 Day", "1w": "1 Week",
}

POPULAR_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
    "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "TRX/USDT",
]

with st.sidebar:
    st.markdown("## 📡 Trendscanner")
    st.markdown("---")

    exchange_id = st.selectbox(
        "Exchange",
        SUPPORTED_EXCHANGES,
        index=0,
        help="Public data — no API key required",
    )

    timeframe = st.selectbox(
        "Timeframe",
        list(TIMEFRAMES.keys()),
        index=5,
        format_func=lambda x: TIMEFRAMES[x],
    )

    num_candles = st.slider("Candles to fetch", 100, 500, 300, step=50)

    st.markdown("---")
    st.markdown("**Watchlist**")

    # Multi-symbol scanner
    scan_symbols = st.multiselect(
        "Symbols to scan",
        POPULAR_SYMBOLS,
        default=["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
    )

    custom_symbol = st.text_input("Add custom symbol", placeholder="e.g. PEPE/USDT")
    if custom_symbol:
        custom_symbol = custom_symbol.upper().strip()
        if custom_symbol not in scan_symbols:
            scan_symbols.append(custom_symbol)

    st.markdown("---")
    auto_refresh = st.checkbox("Auto-refresh (60s)", value=False)
    if st.button("🔄 Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Data: public exchange APIs via ccxt  \nNo API key required")

# ── Main content ───────────────────────────────────────────────────────────────

st.markdown("# 📡 Trendscanner")
st.markdown(f"**Exchange:** `{exchange_id}` &nbsp;|&nbsp; **Timeframe:** `{timeframe}` &nbsp;|&nbsp; **Updated:** {datetime.now().strftime('%H:%M:%S')}")

if not scan_symbols:
    st.info("Add at least one symbol in the sidebar to get started.")
    st.stop()

# ── Scanner table ──────────────────────────────────────────────────────────────

st.markdown("---")
st.subheader("🔬 Trend Scanner")

results = []
errors = []

progress = st.progress(0, text="Fetching market data…")

for i, symbol in enumerate(scan_symbols):
    progress.progress((i + 1) / len(scan_symbols), text=f"Fetching {symbol}…")
    df, err = fetch_ohlcv(exchange_id, symbol, timeframe, num_candles)
    if err or df is None or len(df) < 50:
        errors.append(f"**{symbol}**: {err or 'insufficient data'}")
        continue
    df = compute_all(df)
    s = score_trend(df)
    change_pct = (df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100
    results.append({
        "Symbol": symbol,
        "Price": s["close"],
        "Change %": change_pct,
        "Signal": s["label"],
        "Score": s["score"],
        "RSI": s["rsi"],
        "MACD Hist": s["macd_hist"],
        "Vol Ratio": s["vol_ratio"],
        "ATR": s["atr"],
        "_color": s["color"],
        "_df": df,
        "_reasons": s["reasons"],
    })
    time.sleep(0.15)  # polite rate limiting

progress.empty()

if errors:
    with st.expander(f"⚠️ {len(errors)} error(s)", expanded=False):
        for e in errors:
            st.markdown(e)

if not results:
    st.error("Could not fetch data for any symbol. Try a different exchange.")
    st.stop()

# Summary metrics
col1, col2, col3, col4 = st.columns(4)
bulls = sum(1 for r in results if r["_color"] == "bullish")
bears = sum(1 for r in results if r["_color"] == "bearish")
neuts = sum(1 for r in results if r["_color"] == "neutral")
with col1:
    st.metric("🐂 Bullish", bulls)
with col2:
    st.metric("🐻 Bearish", bears)
with col3:
    st.metric("↔ Neutral", neuts)
with col4:
    sentiment = "Bullish" if bulls > bears else ("Bearish" if bears > bulls else "Mixed")
    st.metric("Market Mood", sentiment)

st.markdown("")

# Sort by score descending
results.sort(key=lambda r: r["Score"], reverse=True)

# Render table
for r in results:
    color_class = f"signal-{r['_color']}"
    change_arrow = "▲" if r["Change %"] >= 0 else "▼"
    change_color = "#00e676" if r["Change %"] >= 0 else "#ff5252"

    with st.container():
        cols = st.columns([2, 2, 2, 2, 1.5, 1.5, 1.5, 1.5])
        cols[0].markdown(f"**{r['Symbol']}**")
        cols[1].markdown(f"`{r['Price']:,.4f}`")
        cols[2].markdown(
            f"<span style='color:{change_color}'>{change_arrow} {abs(r['Change %']):.2f}%</span>",
            unsafe_allow_html=True,
        )
        signal_html = f"<span class='{color_class}'>{r['Signal']}</span>"
        cols[3].markdown(signal_html, unsafe_allow_html=True)
        cols[4].markdown(f"RSI `{r['RSI']:.1f}`" if not pd.isna(r["RSI"]) else "RSI —")
        cols[5].markdown(f"MACD `{r['MACD Hist']:+.4f}`")
        cols[6].markdown(f"Vol `{r['Vol Ratio']:.1f}x`")
        cols[7].markdown(f"Score **{r['Score']:+d}**")

    st.markdown("---")

# ── Detail view ────────────────────────────────────────────────────────────────

st.subheader("📈 Chart + Signal Detail")

selected = st.selectbox(
    "Select symbol to inspect",
    [r["Symbol"] for r in results],
)

sel_result = next(r for r in results if r["Symbol"] == selected)

c1, c2 = st.columns([3, 1])

with c1:
    fig = build_chart(sel_result["_df"], selected, timeframe)
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.markdown(f"### {selected}")
    signal_class = f"signal-{sel_result['_color']}"
    st.markdown(
        f"<div style='font-size:1.4rem' class='{signal_class}'>{sel_result['Signal']}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(f"**Score:** {sel_result['Score']:+d} / ±9")
    st.markdown(f"**Price:** `{sel_result['Price']:,.4f}`")
    st.markdown(f"**RSI:** `{sel_result['RSI']:.1f}`" if not pd.isna(sel_result["RSI"]) else "**RSI:** —")
    st.markdown(f"**ATR:** `{sel_result['ATR']:.4f}`")
    st.markdown(f"**Vol Ratio:** `{sel_result['Vol Ratio']:.1f}x`")
    st.markdown("---")
    st.markdown("**Signal Reasons:**")
    for reason in sel_result["_reasons"]:
        st.markdown(f"- {reason}")

# ── Auto-refresh ───────────────────────────────────────────────────────────────

if auto_refresh:
    time.sleep(60)
    st.cache_data.clear()
    st.rerun()
