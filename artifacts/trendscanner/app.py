import streamlit as st
import pandas as pd
import time
from datetime import datetime

from config    import SYMBOLS, TIMEFRAMES
from scanner   import get_data
from trendlines import find_pivots, create_trendline, line_value
from multi_tf  import multi_analysis
from ui_helpers import (
    normalize_timeframe_result,
    format_directional_score,
    extract_quality_summary,
)

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
    .qual-card    { background:#1a1d26; border-radius:8px; padding:10px 14px;
                    margin-bottom:6px; }
    .qual-na      { color:#546e7a; font-style:italic; }
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

# ── Cache wrappers ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def cached_multi_analysis(symbol: str) -> dict:
    return multi_analysis(symbol)


@st.cache_data(ttl=60, show_spinner=False)
def cached_get_data(symbol: str, timeframe: str) -> pd.DataFrame:
    return get_data(symbol, timeframe)


# ── Header ─────────────────────────────────────────────────────────────────────
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

# ── Fetch all results (one cache entry per symbol) ─────────────────────────────
progress = st.progress(0, text="Scanning markets…")
all_results: dict[str, dict] = {}
for i, symbol in enumerate(selected_symbols):
    progress.progress((i + 1) / len(selected_symbols), text=f"Scanning {symbol}…")
    try:
        all_results[symbol] = cached_multi_analysis(symbol)
    except Exception as e:
        all_results[symbol] = {"_error": str(e)}
    time.sleep(0.05)
progress.empty()

# ── Signal grid rows (extract clean signal string per TF) ──────────────────────
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

rows = []
for symbol in selected_symbols:
    ma  = all_results.get(symbol, {})
    row = {"Symbol": symbol}
    for tf in selected_tfs:
        tf_raw         = ma.get(tf, {})
        row[tf]        = normalize_timeframe_result(tf_raw)["signal"]
    rows.append(row)

# ── Summary metrics ────────────────────────────────────────────────────────────
flat = [row.get(tf, "WAIT") for row in rows for tf in selected_tfs]
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

# ── Signal Grid ────────────────────────────────────────────────────────────────
st.subheader("📊 Signal Grid")

header_cols = st.columns([2] + [1] * len(selected_tfs))
header_cols[0].markdown("**Symbol**")
for j, tf in enumerate(selected_tfs):
    header_cols[j + 1].markdown(f"**{tf}**")

st.markdown("<hr style='margin:4px 0 8px 0'>", unsafe_allow_html=True)

for row in rows:
    cols = st.columns([2] + [1] * len(selected_tfs))
    cols[0].markdown(f"**{row['Symbol']}**")
    for j, tf in enumerate(selected_tfs):
        sig   = row.get(tf, "WAIT")
        css   = SIGNAL_CLASS.get(sig, "cell-wait")
        label = SIGNAL_ICON.get(sig, sig)
        cols[j + 1].markdown(
            f"<div class='{css}'>{label}</div>",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── Multi Timeframe Analysis ───────────────────────────────────────────────────
st.subheader("🧠 Multi Timeframe Analysis")

_ALL_TFS = ["1M", "1w", "1d", "4h", "1h"]

for symbol in selected_symbols:
    sym_key  = symbol.replace("/", "_")
    analysis = all_results.get(symbol, {})

    with st.expander(f"📌 {symbol}", expanded=True):

        # ── TF cards ──────────────────────────────────────────────────────────
        cols = st.columns(5)
        for i, tf in enumerate(_ALL_TFS):
            tf_raw = analysis.get(tf, {})
            norm   = normalize_timeframe_result(tf_raw)
            sig    = norm["signal"]
            trend  = norm["trend"]
            conf   = norm["confidence"]
            clabel = norm["confidence_label"]
            reason = norm["reason"]

            with cols[i]:
                st.markdown(f"### {tf}")

                if sig == "LONG":
                    st.success("🟢 LONG")
                elif sig == "SHORT":
                    st.error("🔴 SHORT")
                elif sig == "ERROR":
                    st.warning("⚠️ ERROR")
                else:
                    st.info("⚪ WAIT")

                if sig == "ERROR":
                    st.caption(trend)
                    if reason:
                        st.caption(f"_{reason[:80]}_")
                else:
                    st.caption(trend)
                    st.caption(f"Confidence: **{conf:.0f}%**")
                    st.caption(clabel)

        st.markdown("---")

        # ── FINAL block ───────────────────────────────────────────────────────
        final       = analysis.get("FINAL", {})
        if not isinstance(final, dict):
            final = {}

        f_signal = final.get("signal", "WAIT")
        f_conf   = final.get("confidence", 0.0)
        try:
            f_conf = float(f_conf)
        except Exception:
            f_conf = 0.0
        f_label   = final.get("confidence_label", "LOW") or "LOW"
        f_ds      = final.get("directional_score", 0.0)
        f_long    = final.get("long_timeframes",   0)
        f_short   = final.get("short_timeframes",  0)
        f_wait    = final.get("wait_timeframes",   0)
        f_avail   = final.get("available_timeframes", 0)
        f_reason  = final.get("reason", "")
        ds_str    = format_directional_score(f_ds)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Final Signal",      f_signal)
        m2.metric("Confidence",        f"{f_conf:.0f}%")
        m3.metric("Directional Score", ds_str)
        m4.metric("🟢 / 🔴 / ⚪",     f"{f_long} / {f_short} / {f_wait}")

        st.caption(
            f"Quality: **{f_label}** &nbsp;|&nbsp; "
            f"Available TFs: {f_avail} &nbsp;|&nbsp; "
            f"{f_reason}"
        )

        # ── Quality Breakdown ─────────────────────────────────────────────────
        avail_tfs = [
            tf for tf in _ALL_TFS
            if isinstance(analysis.get(tf), dict)
            and analysis[tf].get("trend", "ERROR") != "ERROR"
        ]

        if avail_tfs:
            with st.expander("🔍 Quality Breakdown", expanded=False):
                qb_tf = st.selectbox(
                    "Timeframe",
                    avail_tfs,
                    key=f"qb_tf_{sym_key}",
                    label_visibility="visible",
                )
                tf_result = analysis.get(qb_tf, {})

                ql, qr = st.columns(2)

                for col, direction, hdr_color in [
                    (ql, "LONG",  "🟢"),
                    (qr, "SHORT", "🔴"),
                ]:
                    qs = extract_quality_summary(tf_result, direction)
                    with col:
                        confirmed_badge = "✅ Confirmed" if qs["confirmed"] else "❌ Not confirmed"
                        st.markdown(
                            f"**{hdr_color} {direction}** &nbsp; "
                            f"<small>{confirmed_badge}</small>",
                            unsafe_allow_html=True,
                        )
                        st.caption(
                            f"Signal: **{qs['signal']}** &nbsp;|&nbsp; "
                            f"Confidence: **{qs['confidence']}** &nbsp;|&nbsp; "
                            f"{qs['confidence_label']}"
                        )

                        _comp_labels = {
                            "trend_quality":    ("Trend Quality",    qs["trend_quality_score"]),
                            "volume_quality":   ("Volume Quality",   qs["volume_quality_score"]),
                            "breakout_quality": ("Breakout Quality", qs["breakout_quality_score"]),
                        }
                        for comp_key, (comp_name, top_score) in _comp_labels.items():
                            c = qs["components"][comp_key]
                            score_disp  = c["score"] if c["available"] else top_score
                            weight_disp = c["effective_weight"]
                            contrib_disp = c["contribution"]
                            if score_disp == "N/A":
                                score_disp = top_score

                            st.markdown(
                                f"<div class='qual-card'>"
                                f"<b>{comp_name}</b><br>"
                                f"Score: <b>{score_disp}</b> &nbsp;"
                                f"Weight: {weight_disp} &nbsp;"
                                f"Contrib: {contrib_disp}"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                        if qs["reason"]:
                            st.caption(f"_{qs['reason'][:120]}_")

        # ── Debug data ────────────────────────────────────────────────────────
        with st.expander("🔧 Debug data", expanded=False):
            debug_tfs = [tf for tf in _ALL_TFS if tf in analysis]
            if debug_tfs:
                dbg_tf = st.selectbox(
                    "TF",
                    debug_tfs,
                    key=f"debug_tf_{sym_key}",
                    label_visibility="visible",
                )
                # Show only non-quality fields to keep it readable
                raw = analysis.get(dbg_tf, {})
                if isinstance(raw, dict):
                    compact = {
                        k: v for k, v in raw.items()
                        if k != "quality"
                    }
                    st.json(compact)
                    st.json({"FINAL": analysis.get("FINAL", {})})

st.markdown("---")

# ── Price Chart ────────────────────────────────────────────────────────────────
st.subheader("📈 Price Chart")

try:
    import plotly.graph_objects as go

    chart_symbol = st.selectbox("Symbol", selected_symbols, key="chart_symbol")
    chart_tf     = st.select_slider("Timeframe", options=selected_tfs, key="chart_tf")

    with st.spinner(f"Loading {chart_symbol} {chart_tf}…"):
        df = cached_get_data(chart_symbol, chart_tf)

    # ── Trendlines ────────────────────────────────────────────────────────────
    highs, lows = find_pivots(df)
    down_line   = create_trendline(highs)   # нисходящая (сопротивление)
    up_line     = create_trendline(lows)    # восходящая (поддержка)

    # ── Analysis from cache (no extra API call) ───────────────────────────────
    sym_analysis = all_results.get(chart_symbol, {})
    tf_analysis  = sym_analysis.get(chart_tf, {})
    if not isinstance(tf_analysis, dict):
        tf_analysis = {}

    chart_norm  = normalize_timeframe_result(tf_analysis)
    chart_sig   = chart_norm["signal"]
    chart_conf  = chart_norm["confidence"]
    chart_label = chart_norm["confidence_label"]
    chart_reason = chart_norm["reason"]

    # ── Signal badge ──────────────────────────────────────────────────────────
    sig_css   = SIGNAL_CLASS.get(chart_sig, "cell-wait")
    sig_label = SIGNAL_ICON.get(chart_sig, chart_sig)
    st.markdown(
        f"<div style='display:inline-block' class='{sig_css}'>{sig_label}</div>"
        f"&nbsp;&nbsp;<small>Confidence: <b>{chart_conf:.0f}%</b>"
        f"&nbsp;|&nbsp;<b>{chart_label}</b></small>",
        unsafe_allow_html=True,
    )
    if chart_reason:
        st.caption(chart_reason)
    st.markdown("")

    # ── Figure ────────────────────────────────────────────────────────────────
    fig = go.Figure()

    # ── Convert timestamps ────────────────────────────────────────────────────
    has_time = "time" in df.columns
    if has_time:
        x_vals = pd.to_datetime(df["time"], unit="ms")
    else:
        x_vals = df.index

    fig.add_trace(go.Candlestick(
        x=x_vals,
        open=df["open"], high=df["high"],
        low=df["low"],   close=df["close"],
        increasing_line_color="#00e676",
        decreasing_line_color="#ff5252",
        name="Price",
    ))

    # ── Trendline: нисходящая (сопротивление) ─────────────────────────────────
    if down_line and has_time:
        fig.add_trace(go.Scatter(
            x=[
                pd.to_datetime(df["time"].iloc[down_line["x1"]], unit="ms"),
                pd.to_datetime(df["time"].iloc[down_line["x2"]], unit="ms"),
            ],
            y=[down_line["y1"], down_line["y2"]],
            mode="lines",
            line=dict(color="#ef9a9a", width=1.5, dash="dot"),
            name="Resistance",
        ))

    # ── Trendline: восходящая (поддержка) ─────────────────────────────────────
    if up_line and has_time:
        fig.add_trace(go.Scatter(
            x=[
                pd.to_datetime(df["time"].iloc[up_line["x1"]], unit="ms"),
                pd.to_datetime(df["time"].iloc[up_line["x2"]], unit="ms"),
            ],
            y=[up_line["y1"], up_line["y2"]],
            mode="lines",
            line=dict(color="#80cbc4", width=1.5, dash="dot"),
            name="Support",
        ))

    # ── Breakout marker на сигнальной свече (n-2) ─────────────────────────────
    quality = tf_analysis.get("quality", {})
    if isinstance(quality, dict) and chart_sig in ("LONG", "SHORT"):
        dir_data  = quality.get(chart_sig, {})
        confirmed = isinstance(dir_data, dict) and bool(dir_data.get("confirmed", False))

        if confirmed and len(df) >= 2 and has_time:
            signal_candle = df.iloc[-2]
            x_marker = pd.to_datetime(signal_candle["time"], unit="ms")

            if chart_sig == "LONG":
                y_marker = signal_candle["low"] * 0.999
                marker_sym = "triangle-up"
                marker_col = "#00e676"
                marker_name = "▲ Breakout LONG"
            else:
                y_marker = signal_candle["high"] * 1.001
                marker_sym = "triangle-down"
                marker_col = "#ff5252"
                marker_name = "▼ Breakout SHORT"

            fig.add_trace(go.Scatter(
                x=[x_marker],
                y=[y_marker],
                mode="markers",
                marker=dict(
                    symbol=marker_sym,
                    size=14,
                    color=marker_col,
                    line=dict(color="#ffffff", width=1),
                ),
                name=marker_name,
            ))

    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#141720",
        font=dict(color="#e0e0e0", family="monospace"),
        xaxis_rangeslider_visible=False,
        xaxis=dict(gridcolor="#1f2329"),
        yaxis=dict(gridcolor="#1f2329"),
        margin=dict(l=0, r=0, t=30, b=0),
        height=480,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=11),
        ),
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
