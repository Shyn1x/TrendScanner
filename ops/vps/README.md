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
