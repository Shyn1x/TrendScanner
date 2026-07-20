import streamlit as st
import pandas as pd
import time
from datetime import datetime

from config          import SYMBOLS, TIMEFRAMES
from scanner         import get_data
from trendlines      import find_pivots, create_trendline, line_value
from multi_tf        import multi_analysis
from quality_pipeline import PIPELINE_VERSION
from settings        import (
    CACHE_TTL_SECONDS,
    TOP_SETUPS_LIMIT,
    TOP_SETUPS_MIN_DECISION_SCORE,
    SHOW_WATCH_IN_TOP_SETUPS,
    SHOW_SKIP_IN_TOP_SETUPS,
    SHOW_WAIT_SIGNALS_BY_DEFAULT,
    SHOW_ALL_SYMBOLS_BY_DEFAULT,
    DEFAULT_RESULT_PAGE_SIZE,
    MAX_RESULT_PAGE_SIZE,
)
from top_setups import build_top_setups
from ui_helpers import (
    normalize_timeframe_result,
    format_directional_score,
    extract_quality_summary,
    normalize_decision_result,
    format_score_percent,
    decision_badge,
    summarize_decision_factors,
    paginate_items,
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

    /* Signal cells */
    .cell-long    { background:#0d3325; color:#00e676; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }
    .cell-short   { background:#3a0d0d; color:#ff5252; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }
    .cell-wait    { background:#1e2230; color:#90a4ae; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }
    .cell-error   { background:#2a1a00; color:#ffa726; font-weight:700;
                    border-radius:6px; padding:4px 10px; text-align:center; }

    /* Quality card */
    .qual-card    { background:#1a1d26; border-radius:8px; padding:10px 14px;
                    margin-bottom:6px; }
    .qual-na      { color:#546e7a; font-style:italic; }

    /* Decision badges */
    .decision-take  { display:inline-block; background:#0d3325;
                      color:#00e676; font-weight:700; font-size:0.9rem;
                      border-radius:6px; padding:3px 10px;
                      border: 1px solid #00e676; }
    .decision-watch { display:inline-block; background:#2a2000;
                      color:#ffcc02; font-weight:600; font-size:0.9rem;
                      border-radius:6px; padding:3px 10px;
                      border: 1px solid #ffcc02; }
    .decision-skip  { display:inline-block; background:#1e2230;
                      color:#546e7a; font-weight:500; font-size:0.9rem;
                      border-radius:6px; padding:3px 10px;
                      border: 1px solid #37474f; }

    /* Setup cards */
    .setup-card { background:#1a1d26; border-radius:10px;
                  padding:14px 16px; margin-bottom:10px;
                  border-left:3px solid #37474f; }
    .setup-card-take  { border-left-color:#00e676; }
    .setup-card-watch { border-left-color:#ffcc02; }
    .setup-meta { color:#78909c; font-size:0.82rem; margin-top:4px; }

    /* Decision factor reasons */
    .reason-positive { color:#69f0ae; font-size:0.85rem; }
    .reason-warning  { color:#ffd740; font-size:0.85rem; }
    .reason-blocker  { color:#ff5252; font-size:0.85rem; }

    /* Market list rows */
    .market-row { padding:6px 0; border-bottom:1px solid #1e2230; }

    /* Prevent horizontal overflow */
    .stMarkdown { overflow-wrap:break-word; word-break:break-word; }
</style>
""", unsafe_allow_html=True)

# ── Session state defaults ──────────────────────────────────────────────────────
if "selected_detail_symbol" not in st.session_state:
    st.session_state["selected_detail_symbol"] = None
if "market_results_page" not in st.session_state:
    st.session_state["market_results_page"] = 1
if "last_scan_completed" not in st.session_state:
    st.session_state["last_scan_completed"] = None

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
    st.markdown("**Display filters**")
    show_watch = st.toggle("Show WATCH setups",    value=SHOW_WATCH_IN_TOP_SETUPS)
    show_skip  = st.toggle("Show SKIP results",    value=SHOW_SKIP_IN_TOP_SETUPS)
    show_wait  = st.toggle("Show WAIT signals",    value=SHOW_WAIT_SIGNALS_BY_DEFAULT)
    show_all   = st.toggle("Show all symbols",     value=SHOW_ALL_SYMBOLS_BY_DEFAULT)
    top_n      = st.slider("Number of Top Setups", min_value=3, max_value=20,
                           value=TOP_SETUPS_LIMIT, step=1)

    st.markdown("---")
    auto_refresh = st.checkbox("Auto-refresh (60s)", value=False)
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Exchange: KuCoin (public)\nNo API key required")

# ── Cache wrappers ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_multi_analysis(symbol: str, pipeline_version: str = PIPELINE_VERSION) -> dict:
    """Cache key includes pipeline_version — изменение версии инвалидирует кэш."""
    return multi_analysis(symbol)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_get_data(symbol: str, timeframe: str) -> pd.DataFrame:
    return get_data(symbol, timeframe)


# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("# 🚀 Trend Scanner")

if not selected_symbols:
    st.info("Add at least one symbol in the sidebar.")
    st.stop()

if not selected_tfs:
    st.info("Select at least one timeframe in the sidebar.")
    st.stop()

# Compact info panel — 3 columns: Exchange | Last scan | Status
_hdr_c1, _hdr_c2, _hdr_c3 = st.columns(3)
_hdr_c1.markdown("**Exchange**  \nKuCoin")
_last_scan_str = st.session_state["last_scan_completed"] or "No completed scan yet"
_hdr_c2.markdown(f"**Last scan**  \n{_last_scan_str}")
_status_ph = _hdr_c3.empty()   # updated live during + after scan

# Scan progress area
_scan_msg_ph  = st.empty()     # "Scanning X / N" or "Scan completed successfully."
_progress_ph  = st.empty()     # progress bar

# ── Fetch all results — ONE call per symbol ────────────────────────────────────
all_results: dict[str, dict] = {}
_total = len(selected_symbols)
for _i, symbol in enumerate(selected_symbols):
    _frac = (_i + 1) / _total
    _status_ph.markdown(f"**Status**  \n🔄 Scanning {symbol}…")
    _scan_msg_ph.markdown(f"Scanning **{symbol}**  \n{_i + 1} / {_total} symbols")
    _progress_ph.progress(_frac)
    try:
        all_results[symbol] = cached_multi_analysis(symbol)
    except Exception as e:
        all_results[symbol] = {"_error": str(e), "FINAL": {}}
    time.sleep(0.05)

# ── Scan complete — update timestamp only after full cycle ─────────────────────
_completed_at = datetime.now().strftime("%H:%M:%S")
st.session_state["last_scan_completed"] = _completed_at
_hdr_c2.markdown(f"**Last scan**  \n{_completed_at}")
_status_ph.markdown("**Status**  \n✅ Ready")
_scan_msg_ph.success("Scan completed successfully.")
_progress_ph.empty()

# Ensure all_results values have FINAL key for safety
for sym, res in all_results.items():
    if isinstance(res, dict) and "FINAL" not in res:
        res["FINAL"] = {}

# ── Summary metrics ────────────────────────────────────────────────────────────
_ALL_TFS = ["1M", "1w", "1d", "4h", "1h"]

flat_signals = [
    normalize_timeframe_result(all_results.get(sym, {}).get(tf, {}))["signal"]
    for sym in selected_symbols
    for tf in selected_tfs
]
longs  = flat_signals.count("LONG")
shorts = flat_signals.count("SHORT")
waits  = flat_signals.count("WAIT")

# Decision summary
d_take  = sum(1 for r in all_results.values()
              if isinstance(r, dict) and
              r.get("FINAL", {}).get("decision") == "TAKE")
d_watch = sum(1 for r in all_results.values()
              if isinstance(r, dict) and
              r.get("FINAL", {}).get("decision") == "WATCH")

c1, c2, c3, c4 = st.columns(4)
c1.metric("🟢 LONG",   longs)
c2.metric("🔴 SHORT",  shorts)
c3.metric("🔥 TAKE",   d_take)
c4.metric("👀 WATCH",  d_watch)

st.markdown("---")

# ── Top Setups ─────────────────────────────────────────────────────────────────
st.subheader("🔥 Top Setups")

top_setups = build_top_setups(
    all_results,
    limit=top_n,
    min_decision_score=TOP_SETUPS_MIN_DECISION_SCORE,
    include_watch=show_watch,
    include_skip=show_skip,
)

# Set default selected symbol from top setups
if st.session_state["selected_detail_symbol"] is None:
    if top_setups:
        st.session_state["selected_detail_symbol"] = top_setups[0]["symbol"]
    elif selected_symbols:
        st.session_state["selected_detail_symbol"] = selected_symbols[0]

if not top_setups:
    st.info(
        "No TAKE or qualifying WATCH setups found.  \n"
        "The scanner is working, but current conditions do not meet "
        "the selected quality filters."
    )
else:
    # Render cards in 2 columns
    n_setups = len(top_setups)
    col_pairs = [top_setups[i:i+2] for i in range(0, n_setups, 2)]

    for pair in col_pairs:
        cols = st.columns(len(pair))
        for col, setup in zip(cols, pair):
            badge  = decision_badge(setup["decision"], setup["direction"])
            d_pct  = format_score_percent(setup["decision_score"])
            c_pct  = format_score_percent(setup["confidence"])
            reason = setup["decision_reason"][:90] + ("…" if len(setup["decision_reason"]) > 90 else "")
            card_cls = "setup-card-take" if setup["decision"] == "TAKE" else "setup-card-watch"

            with col:
                badge_css = badge["css_class"]
                st.markdown(
                    f"<div class='setup-card {card_cls}'>"
                    f"<b>#{setup['rank']} {setup['symbol']}</b><br>"
                    f"<span class='{badge_css}'>"
                    f"{badge['icon']} {badge['label']}</span>"
                    f"<div class='setup-meta'>"
                    f"Decision: <b>{d_pct}</b> &nbsp;·&nbsp; "
                    f"Conf: <b>{c_pct}</b> &nbsp;·&nbsp; "
                    f"{setup['available_timeframes']} TFs"
                    f"</div>"
                    f"<div class='setup-meta'>{reason}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Open details",
                    key=f"open_{setup['symbol']}_{setup['rank']}",
                    use_container_width=True,
                ):
                    st.session_state["selected_detail_symbol"] = setup["symbol"]
                    st.rerun()

st.markdown("---")

# ── Signal Grid ────────────────────────────────────────────────────────────────
st.subheader("📊 Signal Grid")

SIGNAL_CLASS = {"LONG": "cell-long", "SHORT": "cell-short",
                "WAIT": "cell-wait", "ERROR": "cell-error"}
SIGNAL_ICON  = {"LONG": "🟢 LONG", "SHORT": "🔴 SHORT",
                "WAIT": "⚪ WAIT", "ERROR": "⚠️ ERR"}

grid_rows = []
for symbol in selected_symbols:
    ma  = all_results.get(symbol, {})
    row = {"Symbol": symbol}
    for tf in selected_tfs:
        row[tf] = normalize_timeframe_result(ma.get(tf, {}))["signal"]
    grid_rows.append(row)

header_cols = st.columns([2] + [1] * len(selected_tfs))
header_cols[0].markdown("**Symbol**")
for j, tf in enumerate(selected_tfs):
    header_cols[j + 1].markdown(f"**{tf}**")

st.markdown("<hr style='margin:4px 0 8px 0'>", unsafe_allow_html=True)

for row in grid_rows:
    cols = st.columns([2] + [1] * len(selected_tfs))
    cols[0].markdown(f"**{row['Symbol']}**")
    for j, tf in enumerate(selected_tfs):
        sig   = row.get(tf, "WAIT")
        css   = SIGNAL_CLASS.get(sig, "cell-wait")
        label = SIGNAL_ICON.get(sig, sig)
        cols[j + 1].markdown(f"<div class='{css}'>{label}</div>",
                              unsafe_allow_html=True)

st.markdown("---")

# ── Market Results (compact, paginated) ────────────────────────────────────────
st.subheader("📋 Market Results")

# Build a flat list of all symbol results
def _make_market_row(symbol: str, result: dict) -> dict | None:
    if not isinstance(result, dict):
        return None
    final = result.get("FINAL", {})
    if not isinstance(final, dict):
        final = {}

    if "_error" in result:
        return {
            "symbol": symbol, "decision": "ERROR", "direction": "NONE",
            "decision_score": 0.0, "confidence": 0.0,
            "confidence_label": "LOW", "signal": "ERROR",
        }

    dec  = normalize_decision_result(final)
    norm = normalize_timeframe_result(final)
    return {
        "symbol":           symbol,
        "decision":         dec["decision"],
        "direction":        dec["decision_direction"],
        "decision_score":   dec["decision_score"],
        "confidence":       norm["confidence"],
        "confidence_label": norm["confidence_label"],
        "signal":           norm["signal"],
    }


all_market_rows = [
    r for sym in selected_symbols
    if (r := _make_market_row(sym, all_results.get(sym, {}))) is not None
]

# Filter
def _passes_market_filter(row: dict) -> bool:
    if show_all:
        return True
    dec = row["decision"]
    sig = row["signal"]
    if dec == "ERROR":
        return True
    if dec == "TAKE":
        return True
    if dec == "WATCH" and show_watch:
        return True
    if dec == "SKIP" and show_skip:
        return True
    if sig in ("LONG", "SHORT") and show_wait:
        return True
    return False

visible_rows = [r for r in all_market_rows if _passes_market_filter(r)]

if not visible_rows:
    st.caption("All results hidden by current filters. Enable 'Show all symbols' in sidebar.")
else:
    page_result = paginate_items(
        visible_rows,
        page=st.session_state["market_results_page"],
        page_size=DEFAULT_RESULT_PAGE_SIZE,
    )
    # Correct page if out of range after filter change
    if st.session_state["market_results_page"] != page_result["page"]:
        st.session_state["market_results_page"] = page_result["page"]

    # Header row
    hdr = st.columns([2, 2, 1, 1, 1])
    hdr[0].markdown("**Symbol**")
    hdr[1].markdown("**Decision**")
    hdr[2].markdown("**Score**")
    hdr[3].markdown("**Conf**")
    hdr[4].markdown("**Signal**")
    st.markdown("<hr style='margin:2px 0 6px 0'>", unsafe_allow_html=True)

    for row in page_result["items"]:
        badge = decision_badge(row["decision"], row["direction"])
        d_pct = format_score_percent(row["decision_score"])
        c_pct = format_score_percent(row["confidence"])
        sig   = row["signal"]
        sig_html = (
            f"<span class='{SIGNAL_CLASS.get(sig, 'cell-wait')}'>"
            f"{SIGNAL_ICON.get(sig, sig)}</span>"
        )

        r_cols = st.columns([2, 2, 1, 1, 1])
        r_cols[0].markdown(f"**{row['symbol']}**")
        badge_css = badge["css_class"]
        r_cols[1].markdown(
            f"<span class='{badge_css}'>{badge['icon']} {badge['label']}</span>",
            unsafe_allow_html=True,
        )
        r_cols[2].markdown(d_pct)
        r_cols[3].markdown(c_pct)
        r_cols[4].markdown(sig_html, unsafe_allow_html=True)

        if st.button("Details", key=f"mkt_{row['symbol']}", use_container_width=False):
            st.session_state["selected_detail_symbol"] = row["symbol"]
            st.session_state["market_results_page"] = page_result["page"]
            st.rerun()

        st.markdown("<hr style='margin:2px 0 2px 0; opacity:0.3'>",
                    unsafe_allow_html=True)

    # Pagination controls
    if page_result["total_pages"] > 1:
        pg_cols = st.columns([1, 3, 1])
        with pg_cols[0]:
            if page_result["has_previous"]:
                if st.button("← Prev", key="pg_prev"):
                    st.session_state["market_results_page"] -= 1
                    st.rerun()
        with pg_cols[1]:
            st.caption(
                f"Page {page_result['page']} of {page_result['total_pages']}  "
                f"({page_result['total_items']} results)"
            )
        with pg_cols[2]:
            if page_result["has_next"]:
                if st.button("Next →", key="pg_next"):
                    st.session_state["market_results_page"] += 1
                    st.rerun()

st.markdown("---")

# ── Detail Panel ───────────────────────────────────────────────────────────────
detail_sym = st.session_state.get("selected_detail_symbol")

# Symbol selector for detail
detail_sym = st.selectbox(
    "📌 Detail view",
    options=selected_symbols,
    index=selected_symbols.index(detail_sym) if detail_sym in selected_symbols else 0,
    key="detail_sym_select",
)
st.session_state["selected_detail_symbol"] = detail_sym

detail_result = all_results.get(detail_sym, {})
if not isinstance(detail_result, dict):
    detail_result = {}

detail_final  = detail_result.get("FINAL", {})
if not isinstance(detail_final, dict):
    detail_final = {}

if "_error" in detail_result:
    st.error(f"⚠️ Error loading {detail_sym}: {detail_result.get('_error', '')}")
else:
    # ── FINAL metrics ──────────────────────────────────────────────────────────
    norm_final = normalize_timeframe_result(detail_final)
    dec_final  = normalize_decision_result(detail_final)
    badge_final = decision_badge(dec_final["decision"], dec_final["decision_direction"])

    f_signal = norm_final["signal"]
    f_conf   = norm_final["confidence"]
    f_label  = norm_final["confidence_label"]
    f_ds     = detail_final.get("directional_score", 0.0)
    ds_str   = format_directional_score(f_ds)

    st.markdown(f"### 📌 {detail_sym}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Signal",       f_signal)
    m2.metric("Confidence",   f"{f_conf:.0f}% ({f_label})")
    m3.metric("Dir Score",    ds_str)
    m4.metric("Decision",
              f"{badge_final['icon']} {badge_final['label']}")

    d_score_pct = format_score_percent(dec_final["decision_score"])
    st.caption(
        f"Decision Score: **{d_score_pct}** &nbsp;|&nbsp; "
        f"Available TFs: {detail_final.get('available_timeframes', '—')} &nbsp;|&nbsp; "
        f"{dec_final['decision_reason']}"
    )

    # ── Why this decision? ─────────────────────────────────────────────────────
    # Find the TF with highest decision_score matching FINAL direction
    chosen_direction = dec_final["decision_direction"]
    best_tf_details  = None
    best_tf_score    = -1.0

    for tf_key in _ALL_TFS:
        tf_data = detail_result.get(tf_key)
        if not isinstance(tf_data, dict):
            continue
        dd = tf_data.get("decision_details")
        if not isinstance(dd, dict):
            continue
        dir_data = dd.get(chosen_direction) if chosen_direction != "NONE" else None
        if dir_data is None:
            # fall back to any direction
            for d in ("LONG", "SHORT"):
                dir_data = dd.get(d)
                if dir_data:
                    break
        if not isinstance(dir_data, dict):
            continue
        tf_dscore = float(dir_data.get("decision_score", 0.0))
        if tf_dscore > best_tf_score:
            best_tf_score   = tf_dscore
            best_tf_details = dir_data

    if best_tf_details:
        factors = summarize_decision_factors(best_tf_details, max_items=5)
    else:
        factors = {"positive": [], "warnings": [], "blockers": []}

    with st.expander("💡 Why this decision?", expanded=True):
        if not any(factors.values()):
            st.caption("No factor detail available for this symbol.")
        else:
            fcol1, fcol2, fcol3 = st.columns(3)
            with fcol1:
                st.markdown("**✓ Positive**")
                for p in factors["positive"]:
                    st.markdown(f"<div class='reason-positive'>✓ {p}</div>",
                                unsafe_allow_html=True)
                if not factors["positive"]:
                    st.caption("—")
            with fcol2:
                st.markdown("**⚠ Warnings**")
                for w in factors["warnings"]:
                    st.markdown(f"<div class='reason-warning'>⚠ {w}</div>",
                                unsafe_allow_html=True)
                if not factors["warnings"]:
                    st.caption("—")
            with fcol3:
                st.markdown("**✕ Blockers**")
                for b in factors["blockers"]:
                    st.markdown(f"<div class='reason-blocker'>✕ {b}</div>",
                                unsafe_allow_html=True)
                if not factors["blockers"]:
                    st.caption("—")

    # ── Per-TF breakdown ───────────────────────────────────────────────────────
    st.markdown("**Multi-Timeframe Breakdown**")

    avail_tfs_detail = [tf for tf in _ALL_TFS if isinstance(detail_result.get(tf), dict)]

    if avail_tfs_detail:
        tf_header = st.columns([1, 2, 2, 1, 1, 2])
        for i, h in enumerate(["TF", "Signal", "Decision", "D.Score", "Conf", "Trend"]):
            tf_header[i].markdown(f"**{h}**")
        st.markdown("<hr style='margin:2px 0 4px 0'>", unsafe_allow_html=True)

        for tf in _ALL_TFS:
            tf_raw = detail_result.get(tf)
            if not isinstance(tf_raw, dict):
                continue
            tnorm   = normalize_timeframe_result(tf_raw)
            tdec    = normalize_decision_result(tf_raw)
            tbadge  = decision_badge(tdec["decision"], tdec["decision_direction"])
            tds_pct = format_score_percent(tdec["decision_score"])
            tc_pct  = format_score_percent(tnorm["confidence"])
            tsig    = tnorm["signal"]
            sig_lbl = SIGNAL_ICON.get(tsig, tsig)
            sig_css = SIGNAL_CLASS.get(tsig, "cell-wait")

            tf_row = st.columns([1, 2, 2, 1, 1, 2])
            tf_row[0].markdown(f"**{tf}**")
            tf_row[1].markdown(
                f"<span class='{sig_css}'>{sig_lbl}</span>",
                unsafe_allow_html=True,
            )
            tbadge_css = tbadge["css_class"]
            tf_row[2].markdown(
                f"<span class='{tbadge_css}'>"
                f"{tbadge['icon']} {tbadge['label']}</span>",
                unsafe_allow_html=True,
            )
            tf_row[3].markdown(tds_pct)
            tf_row[4].markdown(tc_pct)
            tf_row[5].markdown(tnorm["trend"])

        st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)
    else:
        st.caption("No timeframe data available.")

    # ── Quality Breakdown ──────────────────────────────────────────────────────
    avail_tfs_qual = [
        tf for tf in _ALL_TFS
        if isinstance(detail_result.get(tf), dict)
        and detail_result[tf].get("trend", "ERROR") != "ERROR"
    ]
    sym_key = detail_sym.replace("/", "_")

    if avail_tfs_qual:
        with st.expander("🔍 Quality Breakdown", expanded=False):
            qb_tf = st.selectbox(
                "Timeframe",
                avail_tfs_qual,
                key=f"qb_tf_{sym_key}",
                label_visibility="visible",
            )
            tf_result = detail_result.get(qb_tf, {})

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
                        score_disp   = c["score"] if c["available"] else top_score
                        weight_disp  = c["effective_weight"]
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

    # ── Debug data ─────────────────────────────────────────────────────────────
    with st.expander("🔧 Debug data", expanded=False):
        debug_tfs = [tf for tf in _ALL_TFS if tf in detail_result]
        if debug_tfs:
            dbg_tf = st.selectbox(
                "TF", debug_tfs,
                key=f"debug_tf_{sym_key}",
                label_visibility="visible",
            )
            raw = detail_result.get(dbg_tf, {})
            if isinstance(raw, dict):
                compact = {k: v for k, v in raw.items() if k != "quality"}
                st.json(compact)
                st.json({"FINAL": detail_result.get("FINAL", {})})

st.markdown("---")

# ── Price Chart — only for selected_detail_symbol ──────────────────────────────
st.subheader("📈 Price Chart")

try:
    import plotly.graph_objects as go

    chart_symbol = detail_sym
    chart_tf     = st.select_slider(
        "Timeframe", options=selected_tfs, key="chart_tf"
    )

    with st.spinner(f"Loading {chart_symbol} {chart_tf}…"):
        df = cached_get_data(chart_symbol, chart_tf)

    highs, lows = find_pivots(df)
    down_line   = create_trendline(highs)
    up_line     = create_trendline(lows)

    sym_analysis = all_results.get(chart_symbol, {})
    tf_analysis  = sym_analysis.get(chart_tf, {})
    if not isinstance(tf_analysis, dict):
        tf_analysis = {}

    chart_norm   = normalize_timeframe_result(tf_analysis)
    chart_sig    = chart_norm["signal"]
    chart_conf   = chart_norm["confidence"]
    chart_label  = chart_norm["confidence_label"]
    chart_reason = chart_norm["reason"]

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

    fig = go.Figure()
    has_time = "time" in df.columns
    x_vals = pd.to_datetime(df["time"], unit="ms") if has_time else df.index

    fig.add_trace(go.Candlestick(
        x=x_vals,
        open=df["open"], high=df["high"],
        low=df["low"],   close=df["close"],
        increasing_line_color="#00e676",
        decreasing_line_color="#ff5252",
        name="Price",
    ))

    if down_line and has_time:
        fig.add_trace(go.Scatter(
            x=[pd.to_datetime(df["time"].iloc[down_line["x1"]], unit="ms"),
               pd.to_datetime(df["time"].iloc[down_line["x2"]], unit="ms")],
            y=[down_line["y1"], down_line["y2"]],
            mode="lines",
            line=dict(color="#ef9a9a", width=1.5, dash="dot"),
            name="Resistance",
        ))

    if up_line and has_time:
        fig.add_trace(go.Scatter(
            x=[pd.to_datetime(df["time"].iloc[up_line["x1"]], unit="ms"),
               pd.to_datetime(df["time"].iloc[up_line["x2"]], unit="ms")],
            y=[up_line["y1"], up_line["y2"]],
            mode="lines",
            line=dict(color="#80cbc4", width=1.5, dash="dot"),
            name="Support",
        ))

    quality = tf_analysis.get("quality", {})
    if isinstance(quality, dict) and chart_sig in ("LONG", "SHORT"):
        dir_data  = quality.get(chart_sig, {})
        confirmed = isinstance(dir_data, dict) and bool(dir_data.get("confirmed", False))
        if confirmed and len(df) >= 2 and has_time:
            signal_candle = df.iloc[-2]
            x_marker = pd.to_datetime(signal_candle["time"], unit="ms")
            if chart_sig == "LONG":
                y_marker    = signal_candle["low"] * 0.999
                marker_sym  = "triangle-up"
                marker_col  = "#00e676"
                marker_name = "▲ Breakout LONG"
            else:
                y_marker    = signal_candle["high"] * 1.001
                marker_sym  = "triangle-down"
                marker_col  = "#ff5252"
                marker_name = "▼ Breakout SHORT"

            fig.add_trace(go.Scatter(
                x=[x_marker], y=[y_marker],
                mode="markers",
                marker=dict(symbol=marker_sym, size=14, color=marker_col,
                            line=dict(color="#ffffff", width=1)),
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
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
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
