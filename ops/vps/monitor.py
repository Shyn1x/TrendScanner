"""Credential-free VPS snapshot and independently timed health checks."""
import argparse
import json
from pathlib import Path
import subprocess
import time

PERIODS = {'collect_1h': 3600, 'collect_15m': 900, 'collect_4h': 14400}
TIMEOUTS = {'collect_1h': 600, 'collect_15m': 600, 'collect_4h': 1500,
            'demo': 300, 'outcomes': 2400}
MAX_SNAPSHOT_AGE = 5400
# The serial daily analyzer can legitimately occupy the scheduler for 40 min.
COLLECTION_GRACE = 2700
LIVENESS_GRACE = 3000
FIELDS = ('attempted_at', 'finished_at', 'ok', 'covered_timestamp', 'returncode')


def snapshot(root, now, clock_ok):
    path = root / 'state/status.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    # Never upload credentials, task output, position summaries or arbitrary state.
    return {'version': 1, 'observed_at': int(now),
            'active': (root / 'ACTIVE').is_file(), 'clock_ok': bool(clock_ok),
            'heartbeat': state.get('heartbeat', 0),
            'demo_enabled': state.get('demo_enabled'),
            'tasks': {task: {key: state.get('tasks', {}).get(task, {}).get(key)
                             for key in FIELDS} for task in TIMEOUTS}}


def evaluate(data, now):
    problems, running = [], []
    if data.get('version') != 1:
        raise ValueError('UNSUPPORTED_SNAPSHOT')
    age = now - data.get('observed_at', 0)
    if age < -60 or age > MAX_SNAPSHOT_AGE:
        problems.append('MISSING_FRESH_VPS_PROBE')
    if not data.get('active'):
        problems.append('VPS_RUNTIME_INACTIVE')
    if not data.get('clock_ok'):
        problems.append('CLOCK_NOT_SYNCHRONIZED')
    heartbeat_age = now - data.get('heartbeat', 0)
    if heartbeat_age < -60 or heartbeat_age > LIVENESS_GRACE:
        problems.append('STALE_RUNTIME_HEARTBEAT')
    if data.get('demo_enabled') is not True:
        problems.append('DEMO_DISABLED_OR_UNKNOWN')
    for task, timeout in TIMEOUTS.items():
        record = data.get('tasks', {}).get(task, {})
        if not record.get('ok'):
            attempted = record.get('attempted_at') or 0
            if attempted and record.get('finished_at') is None and 0 <= now - attempted <= timeout + 300:
                running.append(task)
            else:
                problems.append(task + ':FAILED_OR_TIMED_OUT')
            continue
        finished_age = now - (record.get('finished_at') or 0)
        if finished_age < -60:
            problems.append(task + ':FUTURE_TIMESTAMP')
        if task in PERIODS:
            period = PERIODS[task]
            expected = (int((now - 120 - COLLECTION_GRACE) // period) - 1) * period * 1000
            covered = record.get('covered_timestamp') or 0
            if covered < expected:
                problems.append(task + ':STALE_CANDLES')
            if covered > (int(now) // period - 1) * period * 1000:
                problems.append(task + ':FUTURE_CANDLES')
        elif finished_age > (LIVENESS_GRACE if task == 'demo' else 90000):
            problems.append(task + ':STALE')
    return {'checked_at': int(now), 'status': 'ATTENTION' if problems else 'PASS',
            'problems': problems, 'running': running,
            'snapshot_age_seconds': int(age)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['snapshot', 'check'])
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-runtime')
    parser.add_argument('--file', type=Path, default=Path('health-snapshot.json'))
    args = parser.parse_args()
    if args.operation == 'snapshot':
        check = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                               capture_output=True, text=True, timeout=10)
        data = snapshot(args.root, time.time(), check.returncode == 0 and check.stdout.strip() == 'yes')
        args.file.write_text(json.dumps(data, sort_keys=True, indent=2))
        print('VPS HEALTH SNAPSHOT: SAVED')
        return
    report = evaluate(json.loads(args.file.read_text()), time.time())
    output = json.dumps(report, sort_keys=True, indent=2)
    print(output)
    import os
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('### External VPS health: ' + report['status'] + '\n\n```json\n' + output + '\n```\n')
    raise SystemExit(1 if report['problems'] else 0)


if __name__ == '__main__':
    main()
