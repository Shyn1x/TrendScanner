import streamlit as st
import pandas as pd
import time

from config          import SYMBOLS, TIMEFRAMES
from scanner         import get_data
from trendlines      import find_pivots, create_trendline, line_value
from multi_tf        import multi_analysis
from quality_pipeline import PIPELINE_VERSION
from settings import (
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
from explain_engine import build_timeframe_explanation
from breakout_diagnostic_report import build_breakout_diagnostic_report
from analytics_scan_lifecycle import (
    PERSISTED_SCAN_ID_KEY,
    ensure_active_scan_id,
    request_new_scan,
)
from scan_analytics_service import persist_completed_scan_analytics
from product_helpers import (
    get_local_datetime,
    format_scan_timestamp,
    build_product_diagnostic,
    build_mobile_tf_card,
    order_decision_factors,
    build_empty_state_summary,
    count_final_signals,
    count_final_decisions,
)

st.set_page_config(
    page_title="Trend Scanner",
    page_icon="🚀",
    layout="wide",
)


def _format_diag_value(value):
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        formatted = ", ".join(str(x) for x in value if x is not None)
        return formatted if formatted else "N/A"
    return str(value)


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

    /* Mobile market cards */
    .market-card {
        background:#1a1d26; border-radius:8px;
        padding:12px 14px; margin-bottom:8px;
        border-left:3px solid #37474f;
    }
    .market-card-take  { border-left-color:#00e676; }
    .market-card-watch { border-left-color:#ffcc02; }
    .market-card-skip  { border-left-color:#37474f; }
    .market-card-error { border-left-color:#ffa726; }
    .mc-sym  { font-weight:700; font-size:1rem; }
    .mc-field { color:#90a4ae; font-size:0.83rem; margin-top:3px; }
    .mc-reason { color:#78909c; font-size:0.81rem;
                 margin-top:6px; font-style:italic; }

    /* Empty state */
    .empty-state {
        background:#1a1d26; border-radius:8px;
        padding:12px 14px; color:#78909c; font-size:0.9rem;
    }

    /* Info panel */
    .info-label { color:#546e7a; font-size:0.78rem; text-transform:uppercase;
                  letter-spacing:0.05em; margin-bottom:2px; }
    .info-value { font-size:0.95rem; font-weight:500; }

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
    st.session_state["last_scan_completed"] = None  # stored as "HH:MM:SS TZ" string
if "analytics_last_scan_id" not in st.session_state:
    st.session_state["analytics_last_scan_id"] = None
if "analytics_last_inserted" not in st.session_state:
    st.session_state["analytics_last_inserted"] = 0
if "analytics_last_error" not in st.session_state:
    st.session_state["analytics_last_error"] = None

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
    dev_diagnostics = st.toggle("Developer diagnostics", value=False)

    st.markdown("---")
    auto_refresh = st.checkbox("Auto-refresh (60s)", value=False)
    if st.button("🔄 Refresh", use_container_width=True):
        request_new_scan(st.session_state)
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Exchange: KuCoin Futures\nNo API key required")

# ── Cache wrappers ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_multi_analysis(symbol: str, pipeline_version: str = PIPELINE_VERSION) -> dict:
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

_active_scan_id = ensure_active_scan_id(
    st.session_state,
    selected_symbols,
)

# ── Compact info panel — 3 columns, all using empty() to prevent duplication ──
_hdr_c1, _hdr_c2, _hdr_c3 = st.columns(3)
_hdr_c1.markdown("<div class='info-label'>Exchange</div>"
                 "<div class='info-value'>KuCoin Futures</div>",
                 unsafe_allow_html=True)

_last_scan_ph = _hdr_c2.empty()   # ← placeholder avoids duplicate renders
_last_scan_str = st.session_state["last_scan_completed"] or "No completed scan yet"
_last_scan_ph.markdown(
    f"<div class='info-label'>Last scan</div>"
    f"<div class='info-value'>{_last_scan_str}</div>",
    unsafe_allow_html=True,
)

_status_ph = _hdr_c3.empty()      # ← updated live during and after scan

# ── Scan progress area ─────────────────────────────────────────────────────────
_scan_msg_ph = st.empty()
_progress_ph = st.empty()

# ── Fetch all results — ONE call per symbol ────────────────────────────────────
all_results: dict[str, dict] = {}
_total = len(selected_symbols)

for _i, symbol in enumerate(selected_symbols):
    _frac = (_i + 1) / _total
    _status_ph.markdown(
        f"<div class='info-label'>Status</div>"
        f"<div class='info-value'>🔄 Scanning {symbol} — {_i+1}/{_total}</div>",
        unsafe_allow_html=True,
    )
    _scan_msg_ph.markdown(f"Scanning **{symbol}**  \n{_i+1} / {_total} symbols")
    _progress_ph.progress(_frac)
    try:
        all_results[symbol] = cached_multi_analysis(symbol)
    except Exception as e:
        all_results[symbol] = {"_error": str(e), "FINAL": {}}
    time.sleep(0.05)

# ── Scan complete — update timestamp ONCE after full cycle ─────────────────────
_completed_dt  = get_local_datetime()
_completed_str = format_scan_timestamp(_completed_dt)
st.session_state["last_scan_completed"] = _completed_str

_last_scan_ph.markdown(
    f"<div class='info-label'>Last scan</div>"
    f"<div class='info-value'>{_completed_str}</div>",
    unsafe_allow_html=True,
)
_status_ph.markdown(
    "<div class='info-label'>Status</div>"
    "<div class='info-value'>✅ Ready</div>",
    unsafe_allow_html=True,
)
_scan_msg_ph.success("Scan completed successfully.")
_progress_ph.empty()

# Ensure all_results values have FINAL key
for _sym, _res in all_results.items():
    if isinstance(_res, dict) and "FINAL" not in _res:
        _res["FINAL"] = {}

# ── Persist Strategy Analytics once per completed scan cycle ──────────────────
if st.session_state.get(PERSISTED_SCAN_ID_KEY) != _active_scan_id:
    _analytics_result = persist_completed_scan_analytics(
        all_results,
        pipeline_version=PIPELINE_VERSION,
        scan_id=_active_scan_id,
    )
    st.session_state["analytics_last_scan_id"] = _analytics_result.get("scan_id")
    st.session_state["analytics_last_inserted"] = int(
        _analytics_result.get("inserted", 0)
    )
    st.session_state["analytics_last_error"] = _analytics_result.get("error")
    if not _analytics_result.get("error"):
        st.session_state[PERSISTED_SCAN_ID_KEY] = _active_scan_id

# ── Summary metrics — always from FINAL ────────────────────────────────────────
_ALL_TFS = ["1M", "1w", "1d", "4h", "1h"]

sig_counts = count_final_signals(all_results, selected_symbols)
dec_counts = count_final_decisions(all_results, selected_symbols)

c1, c2, c3 = st.columns(3)
c1.metric("🟢 LONG",   sig_counts["LONG"])
c2.metric("🔴 SHORT",  sig_counts["SHORT"])
c3.metric("⚪ WAIT",   sig_counts["WAIT"])

c4, c5, c6 = st.columns(3)
c4.metric("🔥 TAKE",   dec_counts["TAKE"])
c5.metric("👀 WATCH",  dec_counts["WATCH"])
c6.metric("⬜ SKIP",   dec_counts["SKIP"])

st.caption("Final symbol results")
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

SIGNAL_CLASS = {"LONG": "cell-long", "SHORT": "cell-short",
                "WAIT": "cell-wait", "ERROR": "cell-error"}
SIGNAL_ICON  = {"LONG": "🟢 LONG", "SHORT": "🔴 SHORT",
                "WAIT": "⚪ WAIT", "ERROR": "⚠️ ERR"}

if not top_setups:
    ess = build_empty_state_summary(all_results, top_setups)
    blocker_line = (
        f"Most common blocker: {ess['most_common_blocker']}"
        if ess["most_common_blocker"] else ""
    )
    st.markdown(
        f"<div class='empty-state'>"
        f"No qualifying setups right now.<br>"
        f"<small>TAKE: {ess['take_count']} &nbsp;·&nbsp; "
        f"WATCH: {ess['watch_count']}</small>"
        + (f"<br><small>{blocker_line}</small>" if blocker_line else "")
        + "</div>",
        unsafe_allow_html=True,
    )
else:
    n_setups  = len(top_setups)
    col_pairs = [top_setups[i:i+2] for i in range(0, n_setups, 2)]

    for pair in col_pairs:
        cols = st.columns(len(pair))
        for col, setup in zip(cols, pair):
            badge     = decision_badge(setup["decision"], setup["direction"])
            badge_css = badge["css_class"]
            d_pct     = format_score_percent(setup["decision_score"])
            c_pct     = format_score_percent(setup["confidence"])
            reason    = setup["decision_reason"][:90] + (
                "…" if len(setup["decision_reason"]) > 90 else ""
            )
            card_cls = (
                "setup-card-take"  if setup["decision"] == "TAKE" else
                "setup-card-watch" if setup["decision"] == "WATCH" else
                ""
            )

            with col:
                st.markdown(
                    f"<div class='setup-card {card_cls}'>"
                    f"<b>#{setup['rank']} {setup['symbol']}</b><br>"
                    f"<span class='{badge_css}'>{badge['icon']} {badge['label']}</span>"
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

# ── Legacy Signal Grid — collapsed by default ──────────────────────────────────
with st.expander("📊 Legacy Signal Grid", expanded=False):
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

# ── Market Results — mobile-friendly vertical cards ────────────────────────────
st.subheader("📋 Market Results")


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
            "decision_reason": result.get("_error", ""),
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
        "decision_reason":  dec["decision_reason"],
    }


all_market_rows = [
    r for sym in selected_symbols
    if (r := _make_market_row(sym, all_results.get(sym, {}))) is not None
]


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
    if st.session_state["market_results_page"] != page_result["page"]:
        st.session_state["market_results_page"] = page_result["page"]

    for row in page_result["items"]:
        badge     = decision_badge(row["decision"], row["direction"])
        badge_css = badge["css_class"]
        d_pct     = format_score_percent(row["decision_score"])
        c_pct     = format_score_percent(row["confidence"])
        reason    = str(row.get("decision_reason", ""))[:120]

        # Border colour per decision
        border_cls = {
            "TAKE":  "market-card-take",
            "WATCH": "market-card-watch",
            "SKIP":  "market-card-skip",
            "ERROR": "market-card-error",
        }.get(row["decision"], "market-card-skip")

        st.markdown(
            f"<div class='market-card {border_cls}'>"
            f"<div class='mc-sym'>{row['symbol']}</div>"
            f"<div class='mc-field'>"
            f"<span class='{badge_css}'>{badge['icon']} {badge['label']}</span>"
            f"</div>"
            f"<div class='mc-field'>Signal: <b>{row['signal']}</b>"
            f" &nbsp;·&nbsp; Score: <b>{d_pct}</b>"
            f" &nbsp;·&nbsp; Conf: <b>{c_pct}</b></div>"
            + (f"<div class='mc-reason'>{reason}</div>" if reason else "")
            + "</div>",
            unsafe_allow_html=True,
        )

        btn_col, _ = st.columns([1, 3])
        if btn_col.button("Details", key=f"mkt_{row['symbol']}"):
            st.session_state["selected_detail_symbol"] = row["symbol"]
            st.session_state["market_results_page"] = page_result["page"]
            st.rerun()

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

detail_final = detail_result.get("FINAL", {})
if not isinstance(detail_final, dict):
    detail_final = {}

if "_error" in detail_result:
    st.error(f"⚠️ Error loading {detail_sym}: {detail_result.get('_error', '')}")
else:
    norm_final  = normalize_timeframe_result(detail_final)
    dec_final   = normalize_decision_result(detail_final)
    badge_final = decision_badge(dec_final["decision"], dec_final["decision_direction"])

    f_signal = norm_final["signal"]
    f_conf   = norm_final["confidence"]
    f_label  = norm_final["confidence_label"]
    f_ds     = detail_final.get("directional_score", 0.0)
    ds_str   = format_directional_score(f_ds)

    st.markdown(f"### 📌 {detail_sym}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Signal",     f_signal)
    m2.metric("Confidence", f"{f_conf:.0f}% ({f_label})")
    m3.metric("Dir Score",  ds_str)
    m4.metric("Decision",   f"{badge_final['icon']} {badge_final['label']}")

    d_score_pct = format_score_percent(dec_final["decision_score"])
    st.caption(
        f"Decision Score: **{d_score_pct}** &nbsp;|&nbsp; "
        f"Available TFs: {detail_final.get('available_timeframes', '—')} &nbsp;|&nbsp; "
        f"{dec_final['decision_reason']}"
    )

    # ── Why this decision? — factor order depends on decision ─────────────────
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

    ordered_sections = order_decision_factors(factors, dec_final["decision"])

    with st.expander("💡 Why this decision?", expanded=True):
        st.caption(
            "Final decision is determined by blockers first, then by quality score."
        )
        if not any(factors.values()):
            st.caption("No factor detail available for this symbol.")
        else:
            fcols = st.columns(3)
            for fcol, section in zip(fcols, ordered_sections):
                with fcol:
                    st.markdown(f"**{section['title']}**")
                    for item in section["items"]:
                        pfx = section["prefix"]
                        css = section["css_class"]
                        st.markdown(
                            f"<div class='{css}'>{pfx} {item}</div>",
                            unsafe_allow_html=True,
                        )
                    if not section["items"]:
                        st.caption("—")

    # ── Multi-TF Breakdown — vertical mobile-friendly expanders ───────────────
    st.markdown("**Multi-Timeframe Breakdown**")

    avail_tfs_detail = [tf for tf in _ALL_TFS if isinstance(detail_result.get(tf), dict)]

    if avail_tfs_detail:
        for tf in _ALL_TFS:
            tf_raw = detail_result.get(tf)
            if not isinstance(tf_raw, dict):
                continue

            card = build_mobile_tf_card(tf, tf_raw)
            badge_css = card["badge"]["css_class"]
            hdr_label = (
                f"**{tf}** — "
                f"<span class='{badge_css}'>"
                f"{card['badge']['icon']} {card['decision_label']}</span>"
            )

            with st.expander(
                f"{tf} — {card['badge']['icon']} {card['decision_label']}",
                expanded=card["should_expand"],
            ):
                tfr1, tfr2 = st.columns(2)
                with tfr1:
                    st.markdown(
                        f"Signal: **{card['signal_icon']} {card['signal']}**  \n"
                        f"Decision score: **{card['decision_score']}**  \n"
                        f"Trend: **{card['trend']}**"
                    )
                with tfr2:
                    st.markdown(
                        f"Confidence: **{card['confidence']}** ({card['confidence_label']})  \n"
                        f"Decision: **{card['decision_label']}**"
                    )
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

    if dev_diagnostics:
        explain_tfs = [tf for tf in _ALL_TFS if isinstance(detail_result.get(tf), dict)]
        with st.expander("🧭 Explain Decision", expanded=False):
            if explain_tfs:
                explain_tf = st.selectbox(
                    "Timeframe",
                    explain_tfs,
                    index=0,
                    key=f"diag_tf_{sym_key}",
                    label_visibility="visible",
                )
                tf_result = detail_result.get(explain_tf, {})

                for direction in ("LONG", "SHORT"):
                    with st.expander(f"{direction}", expanded=(direction == "LONG")):
                        try:
                            explanation = build_timeframe_explanation(tf_result, direction)
                        except Exception as exc:
                            st.warning(
                                f"Unable to build explanation for {direction}: {exc}"
                            )
                            continue

                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown(f"**Decision**: {_format_diag_value(explanation.get('decision'))}")
                            st.markdown(f"**Decision score**: {_format_diag_value(explanation.get('decision_score'))}")
                            st.markdown(f"**Confidence**: {_format_diag_value(explanation.get('confidence'))}")
                            st.markdown(f"**Breakout confirmed**: {_format_diag_value(explanation.get('breakout_confirmed'))}")
                            st.markdown(f"**Trend quality**: {_format_diag_value(explanation['scores'].get('trend_quality'))}")
                            st.markdown(f"**Volume score**: {_format_diag_value(explanation['scores'].get('volume'))}")
                        with c2:
                            st.markdown(f"**Breakout score**: {_format_diag_value(explanation['scores'].get('breakout'))}")
                            st.markdown(f"**Structure score**: {_format_diag_value(explanation['scores'].get('structure'))}")
                            stage = explanation.get('pipeline_stage')
                            if stage == 'breakout_confirmation':
                                stage = 'Blocked at: Breakout confirmation'
                            else:
                                stage = stage.replace('_', ' ').capitalize()
                            st.markdown(f"**Pipeline stage**: {_format_diag_value(stage)}")
                            st.markdown(f"**Breakout line price**: {_format_diag_value(explanation.get('breakout_line_price'))}")
                            st.markdown(f"**Breakout close**: {_format_diag_value(explanation.get('breakout_close'))}")
                            st.markdown(f"**Directional breakout distance** ({_format_diag_value(explanation.get('breakout_distance_direction'))}): {_format_diag_value(explanation.get('breakout_distance'))}")
                            st.markdown(f"**Breakout ATR**: {_format_diag_value(explanation.get('breakout_atr'))}")
                            st.markdown(f"**Distance ATR ratio**: {_format_diag_value(explanation.get('distance_atr_ratio'))}")
                            st.markdown(f"**Candle body ratio**: {_format_diag_value(explanation.get('candle_body_ratio'))}")
                            st.markdown(f"**Rejection wick ratio**: {_format_diag_value(explanation.get('rejection_wick_ratio'))}")
                            st.markdown(f"**Breakout cross score**: {_format_diag_value(explanation.get('breakout_cross_score'))}")
                            st.markdown(f"**Breakout distance score**: {_format_diag_value(explanation.get('breakout_distance_score'))}")
                            st.markdown(f"**Breakout body score**: {_format_diag_value(explanation.get('breakout_body_score'))}")
                            st.markdown(f"**Breakout wick score**: {_format_diag_value(explanation.get('breakout_wick_score'))}")
                            st.markdown(f"**Confirmation check**: {_format_diag_value(explanation.get('breakout_confirmation_comparison'))}")
                            st.markdown(f"**Confirmation expression**: {_format_diag_value(explanation.get('breakout_confirmation_expression'))}")
                            st.markdown(f"**Primary blocker**: {_format_diag_value(explanation.get('primary_blocker'))}")
                            st.markdown(f"**Secondary blocker**: {_format_diag_value(explanation.get('secondary_blocker'))}")
                            st.markdown(f"**Distance to watch**: {_format_diag_value(explanation.get('distance_to_watch'))}")
                            st.markdown(f"**Distance to take**: {_format_diag_value(explanation.get('distance_to_take'))}")

                        st.markdown("**Blockers**: " + _format_diag_value(explanation.get('blockers')))
                        st.markdown("**Warnings**: " + _format_diag_value(explanation.get('warnings')))
                        st.markdown("**Positive factors**: " + _format_diag_value(explanation.get('positive_factors')))
                        st.markdown("**Raw blocker codes**: " + _format_diag_value(explanation.get('raw_blocker_codes')))

                with st.expander("Copy-friendly JSON", expanded=False):
                    try:
                        output_json = {
                            "timeframe": explain_tf,
                            "LONG": build_timeframe_explanation(tf_result, "LONG"),
                            "SHORT": build_timeframe_explanation(tf_result, "SHORT"),
                        }
                        st.json(output_json)
                    except Exception as exc:
                        st.warning(f"Unable to render JSON diagnostics: {exc}")
            else:
                st.caption("No timeframe results available for diagnostics.")

        # ── Breakout Bottleneck Report (Developer diagnostics only) ─────
        with st.expander("🔬 Breakout Bottleneck Report", expanded=False):
            try:
                report = build_breakout_diagnostic_report(all_results)

                totals = report.get("totals", {})
                bcols = st.columns(4)
                bcols[0].metric("Total analyses", totals.get("total_direction_analyses", 0))
                bcols[1].metric("Data unavailable", totals.get("data_unavailable", 0))
                bcols[2].metric("Trendline missing", totals.get("trendline_unavailable", 0))
                bcols[3].metric("Crossed", totals.get("breakout_crossed", 0))

                st.markdown("**Bottleneck frequency**")
                bott = report.get("bottleneck_counts", {})
                # convert to dataframe-friendly list
                bott_table = [{"bottleneck": k, "count": v} for k, v in sorted(bott.items(), key=lambda x: -x[1])]
                st.table(bott_table)

                st.markdown("**Breakout score distribution**")
                buckets = report.get("buckets", {})
                bucket_table = [{"range": k, "count": v} for k, v in buckets.items()]
                st.table(bucket_table)

                st.markdown("**Top 10 closest rejected candidates**")
                top10 = report.get("top10_closest_rejected", [])
                if top10:
                    # Display selected fields in a table
                    rows = []
                    for c in top10:
                        rows.append({
                            "symbol": c.get("symbol"),
                            "tf": c.get("timeframe"),
                            "dir": c.get("direction"),
                            "decision": c.get("decision"),
                            "breakout_score": c.get("breakout_score"),
                            "gap_to_50": c.get("breakout_score_gap"),
                            "primary_blocker": c.get("primary_blocker"),
                        })
                    st.table(rows)
                else:
                    st.caption("No rejected candidates to show.")

                # --- Cross-state classification summary
                st.markdown("**Cross-state classification**")
                cross_counts = report.get("cross_state_counts", {})
                total_cs = sum(cross_counts.values()) or 1
                cs_rows = []
                for k, v in cross_counts.items():
                    pct = (v / total_cs) * 100.0
                    cs_rows.append({"state": k, "count": v, "pct": f"{pct:.1f}%"})
                st.table(cs_rows)

                st.markdown("**By timeframe**")
                cs_tf = report.get("cross_state_by_timeframe", {})
                if cs_tf:
                    for tf_key, tf_counts in cs_tf.items():
                        st.markdown(f"**{tf_key}**")
                        st.table([{"state": k, "count": v} for k, v in sorted(tf_counts.items(), key=lambda x: -x[1])])

                st.markdown("**By direction**")
                cs_dir = report.get("cross_state_by_direction", {})
                for d in ("LONG", "SHORT"):
                    st.markdown(f"**{d}**")
                    dcounts = cs_dir.get(d, {})
                    st.table([{"state": k, "count": v} for k, v in sorted(dcounts.items(), key=lambda x: -x[1])])

                st.markdown("**Examples — Already beyond line (up to 10)**")
                ex_al = report.get("examples_already_beyond", [])
                if ex_al:
                    st.table(ex_al)
                else:
                    st.caption("No examples")

                st.markdown("**Examples — Crossed on latest candle (up to 10)**")
                ex_cl = report.get("examples_crossed_on_latest", [])
                if ex_cl:
                    st.table(ex_cl)
                else:
                    st.caption("No examples")

                with st.expander("Copy-friendly JSON", expanded=False):
                    st.json(report)

            except Exception as exc:
                st.warning(f"Unable to build Breakout Bottleneck Report: {exc}")

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

# ── Scanner Diagnostics — collapsed ───────────────────────────────────────────
with st.expander("🔍 Scanner diagnostics", expanded=False):
    diag = build_product_diagnostic(all_results)

    d1, d2, d3 = st.columns(3)
    d1.metric("Total symbols",   diag["symbols_total"])
    d2.metric("Valid results",   diag["valid_results"])
    d3.metric("Errors",          diag["error_results"])

    st.markdown("**Final signals (LONG/SHORT/WAIT)**")
    ds1, ds2, ds3 = st.columns(3)
    ds1.metric("LONG",  diag["final_signals"]["LONG"])
    ds2.metric("SHORT", diag["final_signals"]["SHORT"])
    ds3.metric("WAIT",  diag["final_signals"]["WAIT"])

    st.markdown("**Decisions (TAKE/WATCH/SKIP)**")
    dd1, dd2, dd3 = st.columns(3)
    dd1.metric("TAKE",  diag["decisions"]["TAKE"])
    dd2.metric("WATCH", diag["decisions"]["WATCH"])
    dd3.metric("SKIP",  diag["decisions"]["SKIP"])

    st.markdown("**Top Setups exclusion reasons**")
    ex = diag["excluded_reasons"]
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("SKIP decisions",         ex["skip"])
    e2.metric("WATCH below threshold",  ex["watch_below_threshold"])
    e3.metric("Direction NONE",         ex["direction_none"])
    e4.metric("Invalid data",           ex["invalid_data"])
    st.caption(
        f"WATCH above min score ({TOP_SETUPS_MIN_DECISION_SCORE}%): "
        f"{diag['watch_above_min_score']}"
    )

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
    x_vals = (
    pd.to_datetime(df["time"], unit="ms", utc=True)
    if has_time
    else df.index
)

    fig.add_trace(go.Candlestick(
        x=x_vals,
        open=df["open"], high=df["high"],
        low=df["low"],   close=df["close"],
        increasing_line_color="#00e676",
        decreasing_line_color="#ff5252",
        name="Price",
    ))

    last_idx = len(df) - 1
    current_close = float(df["close"].iloc[last_idx])

    if down_line and has_time:
        resistance_current = float(line_value(down_line, last_idx))
        resistance_distance_pct = (
            ((resistance_current - current_close) / current_close) * 100.0
            if current_close
            else 0.0
        )

        resistance_customdata = [
            ["Pivot 1", ""],
            ["Pivot 2", ""],
            [
                "Current projection",
                (
                    f"Current price: {current_close:,.4f}<br>"
                    f"Distance to resistance: {resistance_distance_pct:+.2f}%"
                ),
            ],
        ]

        fig.add_trace(go.Scatter(
            x=[
                x_vals.iloc[down_line["x1"]],
                x_vals.iloc[down_line["x2"]],
                x_vals.iloc[last_idx],
            ],
            y=[
                down_line["y1"],
                down_line["y2"],
                resistance_current,
            ],
            mode="lines+markers",
            line=dict(color="#ef5350", width=1.5, dash="dot"),
            marker=dict(size=5),
            name="Resistance",
            customdata=resistance_customdata,
            hovertemplate=(
                "<b>Resistance</b><br>"
                "%{customdata[0]}<br>"
                "Date: %{x|%Y-%m-%d %H:%M}<br>"
                "Level: %{y:,.4f}<br>"
                "%{customdata[1]}"
                "<extra></extra>"
            ),
        ))

    if up_line and has_time:
        support_current = float(line_value(up_line, last_idx))
        support_distance_pct = (
            ((current_close - support_current) / current_close) * 100.0
            if current_close
            else 0.0
        )

        support_customdata = [
            ["Pivot 1", ""],
            ["Pivot 2", ""],
            [
                "Current projection",
                (
                    f"Current price: {current_close:,.4f}<br>"
                    f"Distance to support: {support_distance_pct:+.2f}%"
                ),
            ],
        ]

        fig.add_trace(go.Scatter(
            x=[
                x_vals.iloc[up_line["x1"]],
                x_vals.iloc[up_line["x2"]],
                x_vals.iloc[last_idx],
            ],
            y=[
                up_line["y1"],
                up_line["y2"],
                support_current,
            ],
            mode="lines+markers",
            line=dict(color="#4caf50", width=1.5, dash="dot"),
            marker=dict(size=5),
            name="Support",
            customdata=support_customdata,
            hovertemplate=(
                "<b>Support</b><br>"
                "%{customdata[0]}<br>"
                "Date: %{x|%Y-%m-%d %H:%M}<br>"
                "Level: %{y:,.4f}<br>"
                "%{customdata[1]}"
                "<extra></extra>"
            ),
        ))

    quality = tf_analysis.get("quality", {})
    if isinstance(quality, dict) and chart_sig in ("LONG", "SHORT"):
        dir_data  = quality.get(chart_sig, {})
        confirmed = isinstance(dir_data, dict) and bool(dir_data.get("confirmed", False))
        if confirmed and len(df) >= 2 and has_time:
            signal_candle = df.iloc[-2]
            x_marker = pd.to_datetime(
    signal_candle["time"],
    unit="ms",
    utc=True,
)
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
    request_new_scan(st.session_state)
    st.cache_data.clear()
    st.rerun()
