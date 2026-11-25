# TrendScanner GitHub Actions monitor (all workflows)

The collector is **generic**: it polls `GET /repos/Shyn1x/TrendScanner/actions/runs`,
so every current and future workflow is included without editing a list of names.

- Every 2 minutes from the VPS **once a persistent scheduler is installed**.
- Alerts once per completed run (success / failure / cancellation / skipped)
  by creating a GitHub Issue which mentions `@Shyn1x`.
- Does not touch strategy, orders, exchange, or trade secrets.
- The state file saves seen run IDs so polling does not duplicate notices.
- On first install, `--bootstrap` skips noisy notifications for old runs.

**Credentials needed for persistent notifications:** a dedicated fine-grained GitHub
token with Actions: read and Issues: write for *Shyn1x/TrendScanner*. The short-lived
`GITHUB_TOKEN` supplied to a GitHub Actions job is **not sufficient** for a
permanent VPS cron job; it expires. Do not commit a token or print it in logs.

VPS setup (once a suitable token is securely configured in the environment):
```bash
python3 ops/vps/monitor_all_workflows.py --bootstrap
# After installing into a protected, stable location:
# */2 * * * * /usr/bin/flock -n /tmp/trendscanner-workflow-monitor.lock /usr/local/bin/trendscanner-workflow-monitor
```

The launcher must load the protected token and run the Python module with a
stable `--state` path. Do not overwrite the existing crontab; append one entry.
GitHub Issues notifications are **not ChatGPT push notifications**. Configure
GitHub notification delivery separately, if desired.
