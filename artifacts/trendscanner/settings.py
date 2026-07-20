"""
settings.py
~~~~~~~~~~~
Product / UI layer settings for Trend Scanner.

These values control DISPLAY and RANKING only.
They do NOT change the trading logic in any analyser module.

Analytical thresholds (TAKE / WATCH / SKIP, confidence weights, signal
thresholds) remain in decision_engine.py, quality_pipeline.py, and
confidence.py.  Do not move them here.
"""

# ── Top Setups ─────────────────────────────────────────────────────────────────

# How many top setups to show by default.
TOP_SETUPS_LIMIT: int = 10

# Minimum decision_score to include a WATCH setup in Top Setups.
# TAKE entries ignore this threshold (they are always shown when valid).
# SKIP entries are controlled by SHOW_SKIP_IN_TOP_SETUPS.
TOP_SETUPS_MIN_DECISION_SCORE: float = 25.0

# Whether to include WATCH setups in Top Setups by default.
SHOW_WATCH_IN_TOP_SETUPS: bool = True

# Whether to include SKIP results in Top Setups by default.
# Typically off — SKIP means "not yet ready".
SHOW_SKIP_IN_TOP_SETUPS: bool = False

# ── Market Results list ────────────────────────────────────────────────────────

# Whether to show WAIT-signal symbols in Market Results by default.
SHOW_WAIT_SIGNALS_BY_DEFAULT: bool = False

# Whether to show all symbols regardless of decision by default.
SHOW_ALL_SYMBOLS_BY_DEFAULT: bool = False

# Number of rows per page in Market Results.
DEFAULT_RESULT_PAGE_SIZE: int = 10

# Hard upper cap for page size (user cannot exceed this via slider).
MAX_RESULT_PAGE_SIZE: int = 50

# ── Cache ──────────────────────────────────────────────────────────────────────

# How long (seconds) cached_multi_analysis results are kept.
# Increasing this reduces API calls; decreasing gives fresher data.
CACHE_TTL_SECONDS: int = 60

# ── Display timezone ───────────────────────────────────────────────────────────

# IANA timezone string used for scan timestamps shown in the UI.
# Falls back to UTC if the timezone cannot be loaded.
DISPLAY_TIMEZONE: str = "Europe/Moscow"
