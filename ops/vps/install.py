"""Prepare immutable frozen workspaces and install one systemd timer on the VPS."""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

LOWER_FILES = ('prospective_market_data.py', 'lower_tf_shadow.py', 'prospective_lower_tf_collector.py',
               'lower_tf_outcome_analyzer.py', 'bybit_demo_executor.py')
FOUR_FILES = ('prospective_market_data.py', 'market_regime_shadow_v2.py', 'prospective_4h_v2_coverage.py',
              'prospective_4h_v2_collector.py')


def invoke(args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def units(root, user):
    # Paths/users originate from the local OS, never from a workflow text input.
    if any(c in str(root) + user for c in '\n\r%"\\ ') or not root.is_absolute():
        raise RuntimeError('UNSUPPORTED_RUNTIME_PATH_OR_USER')
    service = f'''[Unit]
Description=TrendScanner Bybit collection and guarded demo
Wants=network-online.target
After=network-online.target time-sync.target
ConditionPathExists={root}/ACTIVE

[Service]
Type=oneshot
User={user}
WorkingDirectory={root}
ExecStart=/usr/bin/python3 {root}/current/ops/runtime.py --root {root}
TimeoutStartSec=90min
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={root}/state {root}/logs
'''
    timer = '''[Unit]
Description=Check TrendScanner local schedule every minute

[Timer]
OnCalendar=*-*-* *:*:00 UTC
AccuracySec=1s
RandomizedDelaySec=0
Persistent=true
Unit=trendscanner-runtime.service

[Install]
WantedBy=timers.target
'''
    return service, timer


def prepare(patch, lower, four, root, sha):
    if (root / 'ACTIVE').exists():
        raise RuntimeError('RUNTIME_ALREADY_ACTIVE_USE_STATUS')
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise RuntimeError('FULL_COMMIT_SHA_REQUIRED')
    values = {name: os.environ.get(name, '') for name in
              ('DATABASE_URL', 'BYBIT_DEMO_API_KEY', 'BYBIT_DEMO_API_SECRET', 'BYBIT_DEMO_ENABLED', 'BYBIT_DEMO_START_MS')}
    if any(not values[name] for name in ('DATABASE_URL', 'BYBIT_DEMO_API_KEY', 'BYBIT_DEMO_API_SECRET', 'BYBIT_DEMO_START_MS')):
        raise RuntimeError('EXISTING_DEMO_AND_DATABASE_CONFIGURATION_REQUIRED')
    if values['BYBIT_DEMO_ENABLED'] not in ('true', 'false'):
        raise RuntimeError('EXPLICIT_DEMO_ENABLED_REQUIRED')
    for directory in (root, root / 'releases', root / 'state', root / 'logs'):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    release = root / 'releases' / sha
    release.mkdir(exist_ok=True, mode=0o700)
    for source, name, files in ((lower, 'lower', LOWER_FILES), (four, 'four', FOUR_FILES)):
        destination = release / name
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__'))
        for filename in files:
            shutil.copy2(patch / 'artifacts/trendscanner' / filename, destination / 'artifacts/trendscanner' / filename)
        invoke(['uv', 'sync', '--frozen', '--python', sys.executable], cwd=destination)
    shutil.copytree(patch / 'ops/vps', release / 'ops', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
    credentials = root / 'credentials.json'
    # No secret value is sent to stdout, argv, unit files, git, or artifacts.
    descriptor = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(values, stream)
    from runtime import command, run_command
    for task in ('collect_1h', 'collect_15m', 'collect_4h', 'demo'):
        args, workspace = command(task, release, {}, 0)
        args[-1] = '--preflight' if task == 'demo' else args[-1]
        if task != 'demo':
            args.remove('--write')
        code, output = run_command(args, workspace, task, values)
        print(output, end='', flush=True)
        if code:
            raise RuntimeError('PREFLIGHT_FAILED:' + task)
    current = root / 'current'
    temporary = root / 'current.next'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release, target_is_directory=True)
    temporary.replace(current)
    user = pwd.getpwuid(os.getuid()).pw_name
    service, timer = units(root, user)
    (root / 'trendscanner-runtime.service').write_text(service)
    (root / 'trendscanner-runtime.timer').write_text(timer)
    print('RUNTIME PREPARED: PASS', flush=True)


def activate(root):
    if os.geteuid() != 0:
        raise RuntimeError('ACTIVATE_REQUIRES_SUDO')
    if not (root / 'credentials.json').is_file() or not (root / 'current').is_symlink():
        raise RuntimeError('PREPARE_REQUIRED')
    if (root / 'ACTIVE').exists():
        raise RuntimeError('RUNTIME_ALREADY_ACTIVE_USE_STATUS')
    invoke(['systemd-analyze', 'verify', root / 'trendscanner-runtime.service', root / 'trendscanner-runtime.timer'])
    for name in ('trendscanner-runtime.service', 'trendscanner-runtime.timer'):
        shutil.copy2(root / name, Path('/etc/systemd/system') / name)
        os.chmod(Path('/etc/systemd/system') / name, 0o644)
    invoke(['systemctl', 'daemon-reload'])
    # The installing Actions job occupies the only Linux runner. Older jobs have
    # finished; queued/new jobs will see this ownership flag before any write.
    (root / 'ACTIVE').touch(mode=0o600)
    invoke(['systemctl', 'enable', '--now', 'trendscanner-runtime.timer'])
    invoke(['systemctl', 'start', '--no-block', 'trendscanner-runtime.service'])
    print('VPS TIMER ENABLED: PASS', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-runtime')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--patch', type=Path)
    parser.add_argument('--lower', type=Path)
    parser.add_argument('--four', type=Path)
    parser.add_argument('--sha')
    args = parser.parse_args()
    if args.activate:
        activate(args.root)
    else:
        prepare(args.patch.resolve(), args.lower.resolve(), args.four.resolve(), args.root, args.sha)


if __name__ == '__main__':
    main()
