# Market-regime prospective shadow

Status: standalone module, **not connected to the live app**. No historical
replay, HOLDOUT rerun, threshold adjustment, or production shadow collection is included.
The HOLDOUT2 results in the handoff are user-supplied research context; this
implementation does not independently reproduce them or infer profitability.

## Architecture audit and live integration blocker

- `analysis.analyze_timeframe` finishes decisions and returns quality/decision
  fields. `multi_tf.multi_analysis` assembles timeframe results and FINAL.
- `app.py` finishes `all_results` before calling
  `persist_completed_scan_analytics`. That is an existing post-decision point.
- Later, `ready_ui.render_ready_section` calls
  `ready_engine.build_ready_report(all_results)`. Its candidates carry `ready`,
  symbol, timeframe, direction, production_decision, confidence and decision_score.
- Signal candle metadata now follows `df["time"].iloc[-2]` →
  `analysis.ready_timestamp` → unchanged `multi_tf` timeframe result →
  `evaluate_ready_candidate.ready_timestamp` → READY report. Invalid/missing
  timestamps remain unavailable; no clock or latest-candle fallback is used.
- No live observer hook is installed. Future integration must submit both True
  and False directional evaluations, not just the filtered READY report.

The existing `context_trigger_shadow.py` experiment is untouched and not reused.

## Context and mapping

`exchange.milliseconds()` determines `boundary = floor(now / TIMEFRAME_MS) *
TIMEFRAME_MS`, and `target = boundary - TIMEFRAME_MS`. The research loader requests
1213 candles with `get_research_data_before(symbol, "4h",
before_timestamp=boundary, total_limit=SHADOW_CONTEXT_ROWS)` for the same 25-symbol
universe as the READY research pilot. Every accepted window must be contiguous,
unique, exactly aligned to the target and contain finite, coherent positive OHLC.
No open or missing candle is substituted.

Reuse is direct: `ready_market_regime_analysis._frame_features` computes each
symbol's features; `_attach_market` computes BTC context/breadth and invokes the
existing `classify_regime`. The returned market volatility and ATR fields refer
to BTC, as in validated research, not the candidate asset. The target feature
must have exactly 200 previous ATR observations. No formulas are copied. `SHADOW_CONTEXT_ROWS = WINDOW_BARS + CONTEXT_BARS`;
shared `CONTEXT_BARS` remains 213. A rolling 1213-row window is not necessarily
the same EMA initialization window as a historical period prefix; parity must
be measured, not assumed.

| Direction | Market | Volatility | Tag | Tier |
|---|---|---|---|---|
| LONG | MIXED | NORMAL | P3_MATCH | PRIMARY |
| LONG | BULL | NORMAL | P2_MATCH | EXPERIMENTAL |
| Other combinations | Any | Any | NONE | NONE |

P1 and P4 are not tags or filters. These labels never change trading decisions.

The module-level `_PROCESS_CONTEXT` cache is shared within one Python process.
A lock serializes the first build for each target. Returned snapshots are deep
copies. Both successful and unavailable snapshots are cached, so retries/cards
cannot cause additional loads at the same target. The next target triggers a new
snapshot; older targets remain cached. Tests use isolated injected instances.

Any missing symbol (including BTC), invalid window, network or internal error
produces UNAVAILABLE/NONE. Successfully loaded symbol count is retained; partial
breadth is not promoted to a candidate tag. This deliberately uses the permitted
UNAVAILABLE behavior rather than a partial-universe classification. Errors expose
short codes/types, not arbitrary exception text containing URLs or secrets.

## Observation and persistence contract

`observe_ready` returns a separate result and never mutates the candidate. It
accepts explicit boolean READY evaluations with identity and original
`ready_timestamp` (Unix ms). Only closed, aligned **4h** candles are accepted;
1h cannot create either P2 or P3 events. True evaluations also supply existing
production_decision, decision_score and confidence.

Per `(shadow_version, symbol, timeframe, direction)`, initial state is False.
Only False → True inserts an event, as in the historical outcome analyzer.
True → True on subsequent candles inserts nothing; a later explicit False resets
state. Missing evaluations are not interpreted as False. Submit complete ordered
observations for historical-equivalent transitions; skipped scans cannot reveal
unobserved transitions. The first observation of each candle wins; repeated or
older timestamps never roll state backward.

`market.target_4h_timestamp` must equal `ready_timestamp` exactly. Either an older
or newer context produces UNAVAILABLE/NONE, never P3_MATCH. The unavailable first
observation is immutable, so retrying cannot retrospectively upgrade its tag. Tests inject the
context clock, loader, observation time and temporary database; no network needed.

Each event contains:

- schema_version=1, shadow_version=`market-regime-v1-preregistered`, observed_at UTC;
- symbol, timeframe, direction, ready_timestamp;
- production: decision, decision_score, confidence;
- market: target_4h_timestamp, market_regime, volatility, btc_return_60,
  ema_distance, breadth_above_ema50, median_return_20, atr_pct,
  atr_percentile_past_200, available_symbols, prior_atr_observations;
- shadow: shadow_status, shadow_error, shadow_tag, candidate_tier.

No outcome is written. Missing market fields are null, never fabricated.

Existing analytics uses local SQLite but `analysis_records` is keyed by scan,
not READY candle, and has a fixed schema. Reuse its `DEFAULT_DB_PATH` and SQLite
storage with a separate `market_regime_shadow_events` table. No changes to the
existing analytics table/service are needed. Unique key:
`(shadow_version, symbol, timeframe, direction, ready_timestamp)`.

A separate local `market_regime_shadow_state` table stores the last observed
candle and boolean state. State advancement and event insertion share one
`BEGIN IMMEDIATE` transaction, including concurrency and process restarts.
This state table is mutable; event rows remain immutable.

Transactions handle concurrent writes; INSERT OR IGNORE retains the first
observation, including UNAVAILABLE, across refreshes/process restarts. Duplicate
calls return that stored snapshot. UPDATE/DELETE triggers reject mutation via
normal SQL. Storage errors return UNAVAILABLE/NONE and do not propagate. The
one-second SQLite busy timeout bounds lock waiting. No database is created just
by importing the module, and no real observations are collected in this patch.

### Durable storage backend (`shadow_storage.py`)

`market_regime_shadow._persist` delegates to `shadow_storage.persist`, which
picks the backend per call: SQLite (as above) by default, or PostgreSQL when
`DATABASE_URL` is set (env var, or an explicit `database_url=` override used
by tests). The event/state contract is identical either way — same table
names and columns, same first-observation-wins/immutable-event semantics, same
False→True/True→True/reset rules. PostgreSQL uses a transaction-scoped
advisory lock keyed by `(shadow_version, symbol, timeframe, direction)` in
place of SQLite's `BEGIN IMMEDIATE`, and `ON CONFLICT` upserts/inserts in
place of `INSERT OR REPLACE`/`INSERT OR IGNORE`. If `DATABASE_URL` is set but
PostgreSQL is unreachable (or the `psycopg` driver isn't installed), the
connection attempt raises and `observe_ready` returns UNAVAILABLE — there is
no silent fallback to SQLite. Credentials only ever come from `DATABASE_URL`
(environment or Streamlit Secrets bridged into the environment); none are
read from or written to the repository. Still not wired into `app.py`.

SQLite files are already ignored by the repository's `*.db`, `*.db-wal` and
`*.db-shm` patterns. No JSONL fallback or external database is introduced.
**Local Streamlit Cloud disk is not treated as durable storage**: restart or
redeployment may lose observations and their deduplication history. A future
collection deployment needs an explicit retention/export arrangement before
these observations can be relied upon as a complete validation dataset.

## Verification

Run with project dependencies installed:

```bash
PYTHONPATH=artifacts/trendscanner python3 -m py_compile \
  artifacts/trendscanner/market_regime_shadow.py \
  artifacts/trendscanner/test_market_regime_shadow.py
PYTHONPATH=artifacts/trendscanner python3 -m pytest -q \
  artifacts/trendscanner/test_market_regime_shadow.py
```

Tests cover mapping, exact closed windows, shared function calls, 200 prior ATR,
concurrent caching/rollover, missing/malformed data, fail-open errors, input
immutability, immutable event schema, concurrent deduplication and restart reuse.
UI and actual live collection remain intentionally unimplemented.
