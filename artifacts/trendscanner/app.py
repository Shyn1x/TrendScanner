import streamlit as st
import pandas as pd
import time
from datetime import datetime

from config import SYMBOLS, TIMEFRAMES
from scanner import get_data, trend_signal, analyze
from trendlines import find_pivots, create_trendline, check_break, line_value

st.set_page_config(
    page_title="Trend Scanner",
    page_icon="🚀",
    layout="wide",
)

# ── Styling ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .cell-long    { background:#0d3325; color:#00e676; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }
    .cell-short   { background:#3a0d0d; color:#ff5252; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }
    .cell-wait    { background:#1e2230; color:#90a4ae; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }
    .cell-error   { background:#2a1a00; color:#ffa726; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚀 Trend Scanner")
    st.markdown("---")
    st.markdown("**Symbols**")
    selected_symbols = st.multiselect(
        "Coins to scan",
        SYMBOLS,
        default=SYMBOLS,
        label_visibility="collapsed",
    )

    custom = st.text_input("Add symbol", placeholder="e.g. DOGE/USDT")
    if custom:
        sym = custom.upper().strip()
        if sym not in selected_symbols:
            selected_symbols.append(sym)

    st.markdown("---")
    st.markdown("**Timeframes**")
    selected_tfs = st.multiselect(
        "Timeframes",
        TIMEFRAMES,
        default=TIMEFRAMES,
        label_visibility="collapsed",
    )

    st.markdown("---")
    auto_refresh = st.checkbox("Auto-refresh (60s)", value=False)
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Exchange: KuCoin (public)\nNo API key required")

# ── Fetch + build results table ───────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def cached_analyze(symbol: str) -> dict:
    return analyze(symbol)

st.markdown("# 🚀 Trend Scanner")
st.markdown(
    f"**Exchange:** `KuCoin` &nbsp;|&nbsp; "
    f"**Updated:** `{datetime.now().strftime('%H:%M:%S')}`"
)

if not selected_symbols:
    st.info("Add at least one symbol in the sidebar.")
    st.stop()

if not selected_tfs:
    st.info("Select at least one timeframe in the sidebar.")
    st.stop()

# Run scanner
progress = st.progress(0, text="Scanning markets…")
rows = []
for i, symbol in enumerate(selected_symbols):
    progress.progress((i + 1) / len(selected_symbols), text=f"Scanning {symbol}…")
    result = cached_analyze(symbol)
    row = {"Symbol": symbol}
    row.update({tf: result.get(tf, "—") for tf in selected_tfs})
    rows.append(row)
    time.sleep(0.05)
progress.empty()

df_results = pd.DataFrame(rows)

# ── Summary metrics ────────────────────────────────────────────────────────────
signal_cells = [
    df_results[tf].tolist()
    for tf in selected_tfs
    if tf in df_results.columns
]
flat = [s for col in signal_cells for s in col]
longs  = flat.count("LONG")
shorts = flat.count("SHORT")
waits  = flat.count("WAIT")

c1, c2, c3, c4 = st.columns(4)
c1.metric("🟢 LONG",  longs)
c2.metric("🔴 SHORT", shorts)
c3.metric("⚪ WAIT",  waits)
dominant = max(["LONG", "SHORT", "WAIT"], key=lambda x: flat.count(x)) if flat else "—"
c4.metric("Dominant Signal", dominant)

st.markdown("---")

# ── Signal grid ────────────────────────────────────────────────────────────────
st.subheader("📊 Signal Grid")

SIGNAL_CLASS = {
    "LONG":  "cell-long",
    "SHORT": "cell-short",
    "WAIT":  "cell-wait",
    "ERROR": "cell-error",
}

SIGNAL_ICON = {
    "LONG":  "🟢 LONG",
    "SHORT": "🔴 SHORT",
    "WAIT":  "⚪ WAIT",
    "ERROR": "⚠️ ERROR",
}

# Header row
header_cols = st.columns([2] + [1] * len(selected_tfs))
header_cols[0].markdown("**Symbol**")
for j, tf in enumerate(selected_tfs):
    header_cols[j + 1].markdown(f"**{tf}**")

st.markdown("<hr style='margin:4px 0 8px 0'>", unsafe_allow_html=True)

# Data rows
for row in rows:
    cols = st.columns([2] + [1] * len(selected_tfs))
    cols[0].markdown(f"**{row['Symbol']}**")
    for j, tf in enumerate(selected_tfs):
        sig = row.get(tf, "—")
        css = SIGNAL_CLASS.get(sig, "cell-wait")
        label = SIGNAL_ICON.get(sig, sig)
        cols[j + 1].markdown(
            f"<div class='{css}'>{label}</div>",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── Detail chart for one symbol ───────────────────────────────────────────────
st.subheader("📈 Price Chart")

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    chart_symbol = st.selectbox("Symbol", selected_symbols)
    chart_tf     = st.select_slider("Timeframe", options=selected_tfs)

    with st.spinner(f"Loading {chart_symbol} {chart_tf}…"):
        df = get_data(chart_symbol, chart_tf)

    highs, lows = find_pivots(df)
    down_line   = create_trendline(highs)
    up_line     = create_trendline(lows)
    long_signal  = check_break(df, down_line, "LONG")
    short_signal = check_break(df, up_line,   "SHORT")

    if long_signal == "LONG":
        sig = "LONG"
    elif short_signal == "SHORT":
        sig = "SHORT"
    else:
        sig = "WAIT"

    sig_css   = SIGNAL_CLASS.get(sig, "cell-wait")
    sig_label = SIGNAL_ICON.get(sig, sig)
    st.markdown(
        f"<div style='display:inline-block' class='{sig_css}'>{sig_label}</div>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=pd.to_datetime(df["time"], unit="ms"),
        open=df["open"], high=df["high"],
        low=df["low"],   close=df["close"],
        increasing_line_color="#00e676",
        decreasing_line_color="#ff5252",
        name="Price",
    ))

    # Descending trendline (resistance)
    if down_line:
        fig.add_trace(
            go.Scatter(
                x=[
                    df.time.iloc[down_line["x1"]],
                    df.time.iloc[down_line["x2"]]
                ],
                y=[
                    down_line["y1"],
                    down_line["y2"]
                ],
                mode="lines",
                name="Down Trend",
            )
        )

    # Ascending trendline (support)
    if up_line:
        fig.add_trace(
            go.Scatter(
                x=[
                    df.time.iloc[up_line["x1"]],
                    df.time.iloc[up_line["x2"]]
                ],
                y=[
                    up_line["y1"],
                    up_line["y2"]
                ],
                mode="lines",
                name="Up Trend",
            )
        )

    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#141720",
        font=dict(color="#e0e0e0", family="monospace"),
        xaxis_rangeslider_visible=False,
        xaxis=dict(gridcolor="#1f2329"),
        yaxis=dict(gridcolor="#1f2329"),
        margin=dict(l=0, r=0, t=30, b=0),
        height=480,
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Raw data (last 10 candles)"):
        st.dataframe(df.tail(10), use_container_width=True)

except Exception as e:
    st.warning(f"Chart unavailable: {e}")

# ── Auto-refresh ───────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(60)
    st.cache_data.clear()
    st.rerun()
