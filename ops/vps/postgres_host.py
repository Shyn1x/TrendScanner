"""Read-only capacity inspection before preparing a private local Postgres."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

MIB = 1024 ** 2
GIB = 1024 ** 3


def capacity(memory, disk_free, *, architecture, pg_version=18):
    reasons = []
    if architecture not in ('x86_64', 'aarch64'):
        reasons.append('UNSUPPORTED_ARCHITECTURE')
    if memory.get('MemTotal', 0) < 900 * MIB:
        reasons.append('TOTAL_MEMORY_BELOW_900_MIB')
    if memory.get('MemAvailable', 0) < 384 * MIB:
        reasons.append('AVAILABLE_MEMORY_BELOW_384_MIB')
    if disk_free < 3 * GIB:
        reasons.append('FREE_DISK_BELOW_3_GIB')
    return {'status': 'READY_TO_PREPARE' if not reasons else 'REVIEW_REQUIRED',
            'reasons': reasons, 'target_postgres_major': pg_version,
            'memory_total_mib': memory.get('MemTotal', 0) // MIB,
            'memory_available_mib': memory.get('MemAvailable', 0) // MIB,
            'swap_total_mib': memory.get('SwapTotal', 0) // MIB,
            'swap_free_mib': memory.get('SwapFree', 0) // MIB,
            'disk_free_mib': disk_free // MIB, 'architecture': architecture}


def inspect(root):
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, rest = line.split(':', 1)
        memory[key] = int(rest.strip().split()[0]) * 1024
    result = capacity(memory, shutil.disk_usage(root).free, architecture=platform.machine())
    result['cpu_count'] = os.cpu_count()
    result['os_release'] = platform.freedesktop_os_release().get('PRETTY_NAME', 'unknown')
    result['tools_available'] = {name: bool(shutil.which(name)) for name in
                                 ('psql', 'pg_dump', 'pg_restore', 'initdb', 'pg_ctl', 'dpkg-deb', 'apt-get', 'crontab', 'openssl')}
    result['installed_postgres_majors'] = sorted(p.name for p in Path('/usr/lib/postgresql').glob('*') if p.is_dir())
    check = subprocess.run(['sudo', '-n', 'true'], capture_output=True, timeout=5)
    result['passwordless_sudo'] = check.returncode == 0
    result['existing_local_database'] = (Path.home() / 'trendscanner-postgres/data/PG_VERSION').exists()
    print(json.dumps(result, sort_keys=True))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['inspect'])
    parser.add_argument('--root', type=Path, default=Path.home())
    args = parser.parse_args()
    inspect(args.root)


if __name__ == '__main__':
    main()
