"""Read-only, credential-free resource summary for colocating Lomenti."""
import json
from pathlib import Path
import shutil

def meminfo():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
            values[key] = int(value.strip().split()[0]) // 1024
    return values

def main():
    disk = shutil.disk_usage(str(Path.home()))
    memory = meminfo()
    result = {
        'memory_total_mb': memory.get('MemTotal'),
        'memory_available_mb': memory.get('MemAvailable'),
        'swap_total_mb': memory.get('SwapTotal'),
        'swap_free_mb': memory.get('SwapFree'),
        'home_disk_free_mb': disk.free // (1024 * 1024),
        'home_disk_total_mb': disk.total // (1024 * 1024),
    }
    Path('vps-capacity.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, sort_keys=True))

if __name__ == '__main__':
    main()
