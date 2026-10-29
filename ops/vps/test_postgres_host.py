from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from postgres_host import capacity, MIB, GIB


def test_capacity_requires_headroom_for_existing_scanner():
    result = capacity({'MemTotal': GIB, 'MemAvailable': 512 * MIB}, 4 * GIB, architecture='x86_64')
    assert result['status'] == 'READY_TO_PREPARE'
    assert result['memory_available_mib'] == 512
    small = capacity({'MemTotal': 512 * MIB, 'MemAvailable': 100 * MIB}, GIB, architecture='x86_64')
    assert small['status'] == 'REVIEW_REQUIRED'
    assert len(small['reasons']) == 3


def test_unsupported_architecture_is_not_marked_installable():
    result = capacity({'MemTotal': 4 * GIB, 'MemAvailable': 3 * GIB}, 20 * GIB, architecture='unknown')
    assert result['reasons'] == ['UNSUPPORTED_ARCHITECTURE']
