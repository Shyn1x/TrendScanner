"""Activate only the reviewed shadow scheduler; retain frozen trading workspaces."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from hourly_shadow import counts, new_state, validate_state
from runtime import atomic_json, run_command


def activate(root, patch, sha):
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('FULL_COMMIT_SHA_REQUIRED')
    if not (root / 'ACTIVE').is_file():
        raise ValueError('ACTIVE_RUNTIME_REQUIRED')
    check = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                           capture_output=True, text=True, timeout=10)
    if check.returncode or check.stdout.strip() != 'yes':
        raise ValueError('CLOCK_NOT_SYNCHRONIZED')
    with (root / 'state/cycle.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = (root / 'current').resolve(strict=True)
        release = root / 'releases' / sha
        state_path = root / 'state/hourly-shadow.json'
        if previous == release:
            validate_state(json.loads(state_path.read_text()))
            print('HOURLY SHADOW ALREADY ACTIVE: PASS')
            return
        if release.exists():
            raise ValueError('RELEASE_EXISTS_INSPECT_BEFORE_RETRY')
        release.mkdir(mode=0o700)
        # Retain byte-identical, pinned trading code, dependencies and configuration.
        for workspace in ('lower', 'four'):
            (release / workspace).symlink_to((previous / workspace).resolve(strict=True), target_is_directory=True)
        shutil.copytree(patch, release / 'ops', ignore=shutil.ignore_patterns('__pycache__'))
        config = json.loads((root / 'credentials.json').read_text())
        args = [str(release / 'lower/.venv/bin/python'), str(release / 'ops/hourly_shadow.py'),
                '--root', str(root), '--preflight']
        code, output = run_command(args, release / 'lower', 'hourly_shadow', {'DATABASE_URL': config['DATABASE_URL']})
        print(output, end='', flush=True)
        if code:
            # A failed preparation is retained for inspection; never switch current.
            raise ValueError('SHADOW_PREFLIGHT_FAILED')
        if state_path.exists():
            state = json.loads(state_path.read_text())
            validate_state(state)
        else:
            state = new_state(int(time.time() * 1000))
            atomic_json(state_path, state)
        temporary = root / 'current.shadow-next'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(release, target_is_directory=True)
        temporary.replace(root / 'current')
        report = {'release': sha, 'previous_release': previous.name,
                  'trading_workspaces_preserved': True, 'progress': counts(state)}
        atomic_json(root / 'state/hourly-shadow-activation.json', report)
        # Counts and code SHAs only; private fills/results never leave the VPS.
        print(json.dumps(report, sort_keys=True))
        print('HOURLY SHADOW ACTIVATED: PASS')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-runtime')
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    args = parser.parse_args()
    activate(args.root.resolve(), args.patch.resolve(), args.sha)


if __name__ == '__main__':
    main()
