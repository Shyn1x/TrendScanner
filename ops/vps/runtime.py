"""Single local scheduler for the frozen Bybit experiments. Standard library only."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

SECRET_NAMES = ('DATABASE_URL', 'BYBIT_DEMO_API_KEY', 'BYBIT_DEMO_API_SECRET')
PERIODS = {'collect_1h': 3600, 'collect_15m': 900, 'demo': 300, 'collect_4h': 14400, 'outcomes': 86400}
TIMEOUTS = {'collect_1h': 600, 'collect_15m': 600, 'demo': 300, 'collect_4h': 1500, 'outcomes': 2400}
ORDER = ('collect_1h', 'collect_15m', 'demo', 'collect_4h', 'outcomes')


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def slot(task, now):
    # Closed candle collectors start two minutes after the boundary.
    # Outcomes run daily at 02:40 UTC; missed daily work runs on the next tick.
    offset = 9600 if task == 'outcomes' else (0 if task == 'demo' else 120)
    return int((now - offset) // PERIODS[task])


def due(task, state, now):
    record = state.get('tasks', {}).get(task, {})
    if record.get('slot') == slot(task, now) and record.get('ok'):
        return False
    return now - record.get('attempted_at', 0) >= 300


def entry_gate(state, now):
    record = state.get('tasks', {}).get('collect_1h', {})
    expected = (int(now) // 3600 - 1) * 3_600_000
    return bool(record.get('ok') and record.get('covered_timestamp') == expected
                and 0 <= now - record.get('finished_at', 0) <= 3900)


def command(task, release, state, now):
    workspace = release / ('four' if task == 'collect_4h' else 'lower')
    args = [str(workspace / '.venv/bin/python')]
    if task.startswith('collect_'):
        name = 'prospective_4h_v2_collector.py' if task == 'collect_4h' else 'prospective_lower_tf_collector.py'
        args += ['artifacts/trendscanner/' + name]
        if task != 'collect_4h':
            args += ['--timeframe', task.removeprefix('collect_')]
        args += ['--write']
    elif task == 'outcomes':
        args += ['artifacts/trendscanner/lower_tf_outcome_analyzer.py', '--write']
    else:
        args += ['artifacts/trendscanner/bybit_demo_executor.py',
                 '--execute' if entry_gate(state, now) else '--reconcile-only']
    return args, workspace


def redact(text, config):
    for name in SECRET_NAMES:
        value = config.get(name)
        if value:
            text = text.replace(value, '[REDACTED]')
    return text


def run_command(args, workspace, task, config):
    env = os.environ.copy()
    env.update(config)
    env['TREND_SCANNER_MARKET_SOURCE'] = 'bybit_linear'
    env['PYTHONPATH'] = str(workspace / 'artifacts/trendscanner')
    env['PYTHONUNBUFFERED'] = '1'
    try:
        result = subprocess.run(args, cwd=workspace, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=TIMEOUTS[task])
        return result.returncode, redact(result.stdout, config)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ''
        if isinstance(output, bytes):
            output = output.decode(errors='replace')
        return 124, redact(output, config) + '\nTASK TIMEOUT\n'


def collector_result(output):
    targets = re.findall(r'^Latest closed target: (\d+)$', output, re.M)
    remaining = re.findall(r'^Catch-up remaining after this run: (\d+)$', output, re.M)
    complete = ('UP TO DATE: PASS' in output or
                ('NEON WRITE + SEQUENTIAL COVERAGE: PASS' in output and remaining and int(remaining[-1]) == 0))
    return bool(complete and targets), int(targets[-1]) if complete and targets else None


def health(state, now, active=True):
    problems = []
    if not active:
        problems.append('VPS_RUNTIME_INACTIVE')
    if now - state.get('heartbeat', 0) > 1800:
        problems.append('STALE_HEARTBEAT')
    if not state.get('clock_ok'):
        problems.append('CLOCK_NOT_SYNCHRONIZED')
    for task in ORDER:
        if task == 'demo' and state.get('demo_enabled') is False:
            continue
        record = state.get('tasks', {}).get(task, {})
        if not record.get('ok'):
            problems.append(task + ':NO_SUCCESS_OR_FAILURE')
        elif task.startswith('collect_'):
            expected = (int((now - 120) // PERIODS[task]) - 1) * PERIODS[task] * 1000
            if (record.get('covered_timestamp') or 0) < expected:
                problems.append(task + ':BEHIND_CLOSED_CANDLES')
        elif now - record.get('finished_at', 0) > (900 if task == 'demo' else 90000):
            problems.append(task + ':STALE')
    return {'checked_at': int(now), 'status': 'PASS' if not problems else 'ATTENTION',
            'problems': problems, 'entries_allowed': bool(active and state.get('clock_ok') and state.get('demo_enabled') and entry_gate(state, now)), 'state': state}


def cycle(root, clock=time.time, run=run_command, clock_check=None):
    root = Path(root)
    state_path = root / 'state/status.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'tasks': {}}
    config = json.loads((root / 'credentials.json').read_text())
    if not (root / 'ACTIVE').is_file():
        raise RuntimeError('VPS_RUNTIME_INACTIVE')
    release = (root / 'current').resolve(strict=True)
    if clock_check is None:
        check = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                               capture_output=True, text=True, timeout=10)
        clock_ok = check.returncode == 0 and check.stdout.strip() == 'yes'
    else:
        clock_ok = clock_check()
    state.update(heartbeat=int(clock()), clock_ok=clock_ok, release=release.name,
                 demo_enabled=config.get('BYBIT_DEMO_ENABLED', '').lower() == 'true')
    atomic_json(state_path, state)
    if not clock_ok:
        raise RuntimeError('CLOCK_NOT_SYNCHRONIZED')
    failed = False
    for task in ORDER:
        now = clock()
        if task == 'demo' and not state['demo_enabled']:
            continue
        if not due(task, state, now):
            continue
        args, workspace = command(task, release, state, now)
        record = {'slot': slot(task, now), 'attempted_at': int(now), 'ok': False}
        state['tasks'][task] = record
        atomic_json(state_path, state)  # A crash must never leave a false success.
        code, output = run(args, workspace, task, config)
        finished = int(clock())
        log = root / 'logs' / f'{task}-{int(now)}.log'
        log.write_text(output)
        os.chmod(log, 0o600)
        record.update(finished_at=finished, returncode=code, log=log.name, ok=code == 0)
        if task.startswith('collect_') and code == 0:
            complete, covered = collector_result(output)
            record.update(ok=complete, covered_timestamp=covered)
        if task == 'demo' and code == 0:
            summaries = [line for line in output.splitlines() if line.startswith('{')]
            if summaries:
                record['summary'] = json.loads(summaries[-1])
            record['mode'] = args[-1]
        if task == 'outcomes' and code == 0:
            record['ok'] = 'LOWER-TF OUTCOME WRITE: PASS' in output
        failed |= not record['ok']
        state['heartbeat'] = finished
        atomic_json(state_path, state)
        print(json.dumps({'task': task, 'ok': record['ok'], 'finished_at': finished, 'log': log.name}), flush=True)
        # Retain 30 days of operational evidence without filling the small VPS disk.
        for old in (root / 'logs').glob(task + '-*.log'):
            if old.stat().st_mtime < finished - 30 * 86400:
                old.unlink()
    report = health(state, clock())
    atomic_json(root / 'state/health.json', report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-runtime')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.status:
        path = args.root / 'state/status.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        print(json.dumps(health(state, time.time(), (args.root / 'ACTIVE').is_file()), sort_keys=True, indent=2))
        return
    with (args.root / 'state/cycle.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('RUNTIME ALREADY RUNNING: SKIP')
            return
        raise SystemExit(cycle(args.root))


if __name__ == '__main__':
    main()
