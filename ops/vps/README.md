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
| Paired hourly exit shadow | Every five minutes, after existing tasks; no orders |

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
# Prospective paired hourly exit shadow

After merging, run `VPS Runtime Manager` on `main` with
`operation=enable-hourly-shadow`. This obtains the existing scheduler lock,
checks NTP and performs a read-only DB preflight. It atomically activates the
reviewed scheduler while retaining symlinks to the exact existing lower/4h
workspaces, dependencies and credentials. It does not run a full runtime upgrade
or reconcile/submit any order. A busy scheduler aborts activation for later retry.

The experiment freezes its protocol and start boundary before observing trades.
Only signals closing at/after the next hour boundary qualify. The first 50 full
Demo entries confirmed by polling are enrolled before their outcomes are queried.
It uses only the existing 1h LONG executor/Bybit experiment. Existing trades,
failed/unfilled orders and late entries after the 1h exit are not paired; missing
fills and late-entry exclusions remain visible in counters. Ties in one poll use
journal signal-time/symbol order. The cap fixes a research cohort; it does not
stop or modify the Demo executor. No new entries are enrolled after the cap, but
pending outcomes and actual closures continue to be reconciled.

Both virtual exits use the same actual Demo fill VWAP, last fill timestamp,
quantity and journal protective stop. One exits 1h after signal close; the
control model exits 12h after signal close. The existing actual 12h Demo's closed
PnL is retained separately, already net according to its journal convention.
No additional orders are placed and capital freed by a hypothetical earlier exit
is not reinvested. This is a paired exit study, not a second executable portfolio.

The shadow checks closed contiguous 1m public Bybit Mark Price candles for stop
triggers, and Trade candles for modeled exit prices. It never includes minutes
before the fill minute. A touch within the partial fill minute is ambiguous:
the primary calculation conservatively includes it and an alternative skips it.
Multi-minute partial fills are flagged. Stop execution uses the worse of the
stop level and that minute's Trade open. Neither those prices nor the public
Mark Price path guarantee an actual Demo exit fill. Missing candles remain
pending/retry; they are never forward-filled or counted as wins.

Modeled costs are fixed at 6bp fee + 2bp slippage per side (0.16% total), the
predefined research assumption. Actual entry fees are also retained, but are not
subtracted twice. Funding is excluded from both model arms; the separately saved
actual Demo PnL must not be confused with either model. New-data evidence is
required before changing parameters; 50 completed pairs are an initial checkpoint,
not proof of profitability. Market regime dependence and simultaneous signals
remain relevant when interpreting confidence intervals.

The worker runs last every five minutes with a 100s soft budget and 180s timeout.
Failure reports research health but does not affect the existing 1h collection
entry gate. Code can access only read-only PostgreSQL and authenticated Demo
execution-history GET. Private fills/outcomes live in
`state/hourly-shadow.json` (0600), preserved across restarts/activation retries.
The protocol hash prevents silent resets or parameter changes. `shadow-status`
prints counts only: no order IDs, prices, PnL, raw DB rows or artifacts. Normal
runtime logs therefore expose no shadow trade details in GitHub.

To roll back only this activation, with the scheduler idle and its cycle lock
held, restore `current` to `previous_release` recorded privately in
`state/hourly-shadow-activation.json`. Keep the shadow state for research; do not
reset it to restart the same experiment. The previous frozen workspaces and
existing scheduler ownership/cron remain intact.

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

# Private VPS PostgreSQL migration

The `postgres-inspect` operation reads CPU, memory, disk and available tools only.
The initial VPS has Ubuntu 24.04, one CPU, ~2 GiB RAM and ~43 GiB free; Neon reports
~73 MB logical data and PostgreSQL 18. `postgres-prepare` and `postgres-migrate`
are manual, main-only operations. They never change signal definitions or contact
an order-creation API. Source data is retained in Neon.

`postgres-prepare` downloads PostgreSQL 18 from signed official PGDG/Ubuntu apt
repositories into a private user-owned prefix. `apt` is download-only; extracted
packages do not execute maintainer scripts, install OS services or require sudo.
The cluster listens on a 0700 Unix-socket directory only, with TCP disabled and
peer identity mapping to a non-superuser application role. Memory is bounded by
48 MB shared buffers, 16 connections and 2 MB per work operation. WAL durability
and checksums stay enabled. Existing crontab entries are preserved; a separate
minute supervisor restarts this cluster after process/server restarts.

Preparation performs a dump/restore drill on two uniquely named disposable test
databases containing synthetic numeric/JSON/Unicode values. Only those databases
created by the drill are removed. Its encrypted proof is exported, never real
strategy rows. Activation replaces ops/scheduler code atomically while symlinking
the exact existing frozen lower/4h trading workspaces and retaining credentials.

Migration is separate and refuses an unavailable/exhausted Neon source before
pausing the runtime. With source access restored, it pauses the existing scheduler,
exports a read-only consistent snapshot with pg_dump 18 using the direct Neon
endpoint, restores only to a newly created isolated database, and compares row
counts and ordered SHA-256 row hashes for every public table. Unexpected schemas,
extensions or missing journal tables require review rather than partial migration.
The source is not dropped, truncated or modified. The original connection remains
private for rollback. An unexpected source change before cutover aborts the switch.

The pre-migration source dump must be encrypted and uploaded offsite successfully
before `apply` can switch the local runtime's DATABASE_URL. A read-only Demo
preflight is then required. Failed preflight or failed/uncommitted workflow cleanup
restores the original connection. A committed local connection survives later full
runtime upgrades: the old GitHub Neon secret cannot silently replace it. GitHub's
legacy writers remain disabled by existing VPS ownership guards; the Unix-socket
DSN is deliberately not copied to GitHub-hosted job secrets.

Backups are custom-format pg_dump archives encrypted with OpenSSL CMS
AES-256-GCM and an RSA-OAEP/SHA-256 recipient. Only the public certificate lives
on the VPS/in git. The recovery private key was saved separately in the user's
private recovery ZIP; it must never be committed or copied onto this VPS. Preserve
that ZIP to decrypt backups if the VPS is lost. Tamper rejection and recovery were
tested before publication.

The local daily cron creates encrypted snapshots at 01:17 UTC after cutover.
`VPS Postgres Encrypted Backup` creates and uploads a fresh encrypted snapshot
daily at 01:47 UTC, with 14-day GitHub artifact retention and a strict 20 MiB cap
per export. It skips before local cutover. Exact, immutable .cms paths only are
uploaded: no credentials, private key, raw .dump, arbitrary JSON or directory globs.
An acknowledgement is tied to that exact ciphertext hash, avoiding races with a
concurrent local backup. GitHub schedules may be delayed; this is not continuous
WAL archiving or point-in-time recovery. At worst, recovery uses the most recent
successful offsite snapshot. Monitor backup failures/age and periodically perform
a full recovery drill with the separate recovery key.

Manual rootless packages do not receive OS-level automatic PostgreSQL minor
updates. Plan maintenance to back up, stop this private cluster, update its signed
PostgreSQL 18 packages and verify restart. Never upgrade the data directory to a
different major version by replacing binaries alone. A full future upgrade and
backup retention policy deserve explicit review if data volume outgrows the VPS
or the export cap. No paid Neon upgrade or new VPS was purchased by this change.

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
