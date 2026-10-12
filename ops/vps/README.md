# Local Bybit runtime

The VPS owns scheduling; GitHub remains the deployment, validation and inspection interface.
Signal definitions, fixed 200-row windows, 1213-row 4h context, experiment namespaces,
virtual 300 USDT account, 1% risk, 1.5% stop and 12-bar exits are unchanged.
Only Bybit Demo is supported. No real-money endpoint or credentials are introduced.

## Schedule (UTC)

One local timer checks due tasks every minute, starting again after reboot.
Use systemd when existing sudo access permits it; otherwise use the existing
boot-enabled OS cron service under the runner user, without new sudo access.
Tasks execute sequentially under a nonblocking local file lock, in this order:

| Task | Frequency |
| --- | --- |
| 1h collection | Each closed hour, starting two minutes after its close |
| 15m collection | Each closed 15m candle, starting two minutes after its close |
| Demo management | Every five minutes; delayed ticks run on the next free cycle |
| 4h collection | Each closed 4h candle, starting two minutes after its close |
| Outcome/stop analyzer | Daily at 02:40 UTC, including missed work after reboot |

This is one small VPS, not a real-time execution system. The expensive 4h context
and daily analyzer can delay the next tick. The scheduler catches up closed candles,
retries failures after five minutes and records timing/coverage. A missed hourly candle,
failed/partial catch-up, or a crossed hourly boundary blocks demo entries until 1h
coverage is current. `--reconcile-only` still checks existing orders, repairs stops,
reconciles closed PnL and submits due exits. It never submits an unplaced reservation
or a new signal. An unsynchronized system clock blocks all scheduled tasks.

## Install

1. Merge the reviewed PR into `main`.
2. Run **VPS Runtime Manager → inspect** to check clock, runner user and sudo.
3. Run **VPS Runtime Manager → install** on **main**. The job prepares isolated
   frozen lower-TF and 4h workspaces, overlays the reviewed modules, installs the
   frozen dependencies and performs dry collection plus authenticated read-only
   demo preflight. Only then does it install the timer and switch ownership.
4. If passwordless sudo is unavailable, installation uses the existing active,
   boot-enabled cron service and preserves the runner's existing crontab. If neither
   option is available, preparation finishes but root activation is still required.
5. Run **VPS Runtime Manager → status** to inspect first-cycle results and logs.
6. Once the timer and runtime results are verified, set repository variable
   `TREND_SCANNER_RUNTIME=vps` to suppress redundant scheduled Actions jobs.

The `~/trendscanner-runtime/ACTIVE` ownership flag independently prevents Actions
writes and demo execution even before that variable is set. Installation runs on
our only Linux runner, after older jobs finish. Already queued jobs must use the
merged main revision with this guard; do not deploy while an older revision's
runtime jobs are still queued. Manual old workflows also honor local ownership.

No SSH key, new API key, external scheduler or GitHub token is needed. Existing
`DATABASE_URL`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET`, demo enable flag and
start timestamp are stored on the same runner under a private directory in a
0600 JSON configuration file. Values are never put in argv, units, git or artifacts.
Task output is redacted before it is persisted or displayed. Preflight uses the
same minimal environment as the timer; the interpreter library path is derived
from its resolved executable, without relying on temporary Actions variables. Unit processes run
as the existing runner user, with read-only code/home and writable state/log paths.

## Upgrade

After merging an update, run **VPS Runtime Manager → upgrade** on main. It obtains
the existing cycle lock, prepares and validates a new pinned release, and switches
the current symlink only after preflight succeeds. Existing journal, schedule,
ownership and successful task slots are preserved. Busy cycles must finish first.

## Inspection

`VPS Runtime Manager → status` reports current clock/heartbeat, each task's last
success, exact candle coverage, errors, demo mode/positions/virtual equity and the
latest outcome/stop summaries. It reads already redacted local logs, not secrets.
There is no external outage notification yet: a powered-off VPS cannot report
its own failure. The report flags stale evidence rather than calling it healthy.

Runtime directories:
- `current`: pinned release; code never follows new commits automatically.
- `credentials.json`: private existing runtime configuration.
- `state/status.json`, `state/health.json`: atomic operational status, 0600.
- `logs`: task evidence retained for 30 days, 0600; no GitHub artifacts.

### Demo execution comparison

Run **VPS Runtime Manager → audit** for a read-only journal and Bybit execution report.
It uses the existing release interpreter and private credentials without upgrading
the runtime or creating orders. PostgreSQL enforces a read-only transaction; Bybit
requests are restricted to GET calls on the Demo host. No DDL, reconciliation,
new credentials or API permission expansion is needed.

The report counts all trades in the current Bybit experiment and retrieves execution
detail for the last 30 days. It matches entries by exact order ID/link and attached
stops by `parentOrderLinkId`; an unmatched exit remains unknown. Pagination and
seven-day API windows are handled. Fees come from deduplicated execution IDs;
closed PnL is checked against the matched exit orders, without subtracting fees twice.
Funding is a separate signed cash flow, attributed by symbol and holding interval
only under the existing exclusive-position assumption. Missing funding/fees are
unknown, not zero. Manual/shared positions invalidate that funding attribution.

Each trade is joined to the same prospective event's mature 12-bar, 150bp outcome,
using its journal risk amount and the replay's predefined 6bp fee + 2bp slippage per
side. Signal-close-to-fill price gaps include execution delay and market movement;
they are not pure exchange slippage. Trade-candle stop hits are distinct from Demo
MarkPrice stop triggers. A signal candle opens at `ready_timestamp`, closes one hour
later, and a timed exit is due thirteen hours after that open. No closed matched
trades means there is no empirical execution comparison yet.

Demo enable/start configuration is a snapshot at installation. Changing a GitHub
variable afterward does not change a running local process. To stop local execution,
stop the local timer/service. The journal and exchange-side stops remain durable.

## Stop / fallback

For a systemd installation, in the existing server console:

```bash
sudo systemctl stop trendscanner-runtime.timer trendscanner-runtime.service
```

For a cron installation, remove only the two-line `TrendScanner local runtime`
block using `crontab -e` as the runner user, then wait for the current cycle to
finish before changing ownership. Its file lock is `state/cycle.lock`. The private
`state/crontab.before` backup retains the original schedule; never blindly restore
it after unrelated cron entries have been added.

For a full return to GitHub scheduling, after verifying local execution stopped:

```bash
sudo rm /home/runner/trendscanner-runtime/ACTIVE
```

Then clear `TREND_SCANNER_RUNTIME` or set it to `github` in repository variables.
This keeps the DB, journal, logs and credentials; no historical data is deleted.
The GitHub cron delay remains a limitation of that fallback.

Catch-up signals retain their `catchup_replay` label and must be distinguished
from observations collected promptly in research reports. Old KuCoin research
and the Streamlit scanner are outside this scheduling change.
# Manual virtual Demo funds

Run `VPS Runtime Manager` on merged `main` with `operation=wallet` to read the
Bybit Demo UNIFIED USDT wallet. `operation=fund-demo` requests only the shortfall
to a 300 USDT wallet using `/v5/account/demo-apply-money`, then reads the wallet
again to verify credit and positive available account margin. It has no schedule
and requires an explicit manual dispatch. The helper strictly rejects a live
host and never reduces funds, transfers assets or submits an order.

Existing balances of at least 300 USDT receive no top-up. An ambiguous POST is
never retried; any later manual run re-reads the wallet first. The operation
holds the existing scheduler lock during balance checks, uses existing local
credentials and does not upgrade the runtime. It never resets FAILED trades or
replays expired signals. Virtual strategy equity remains a separate journal
calculation; a wallet top-up is not trading profit.

# External health monitoring

`VPS Health Probe` reads only the local ACTIVE marker, NTP status and an allowlist
of scheduler timestamps/status fields every 30 minutes. It uploads a seven-day
GitHub artifact; it never reads credentials, logs, database or trading APIs and
does not require a runtime upgrade. Queued probes are replaced by newer probes.

`External VPS Watchdog` runs independently on GitHub-hosted Ubuntu every 30
minutes, ten minutes after the probe's nominal schedule. It fails if the latest
completed main-branch probe failed, is older than 90 minutes, has no artifact,
or reports inactive scheduling, clock failure, disabled Demo, task failure or
stale runtime/data. Evidence is re-evaluated against the external clock, so a
freshly uploaded file cannot hide an old scheduler heartbeat. A normal running
task receives only its existing timeout plus five minutes; candle freshness has
45 minutes of grace and heartbeat/Demo freshness 50 minutes to accommodate the
serial daily analyzer's 40-minute limit. These are monitoring tolerances only;
the stricter existing trading entry gate is unchanged.

The watchdog completes even when the VPS runner is offline. It uses the existing
ephemeral GitHub token with read-only Actions access, no new secrets or public
VPS endpoint. GitHub Actions failure notifications must be enabled in the owner's
notification settings (email/web); delivery is not proven by a successful green
check. Scheduled workflow notifications follow GitHub's schedule actor rules.
Schedules can be delayed by GitHub and are not an instant outage SLA. If GitHub
itself stops scheduling, this monitor cannot report that failure.
