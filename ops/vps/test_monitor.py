import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from monitor import evaluate, snapshot, PERIODS, TIMEOUTS
import pytest
import yaml

NOW = 1791726000


def healthy():
    tasks = {task: {'ok': True, 'finished_at': NOW - 60, 'attempted_at': NOW - 120,
                    'returncode': 0} for task in TIMEOUTS}
    for task, period in PERIODS.items():
        tasks[task]['covered_timestamp'] = (NOW // period - 1) * period * 1000
    return {'version': 1, 'observed_at': NOW, 'active': True, 'clock_ok': True,
            'heartbeat': NOW - 60, 'demo_enabled': True, 'tasks': tasks}


def test_healthy_and_external_clock_detects_powered_off_server():
    data = healthy()
    assert evaluate(data, NOW)['status'] == 'PASS'
    report = evaluate(data, NOW + 5401)
    assert 'MISSING_FRESH_VPS_PROBE' in report['problems']
    assert 'STALE_RUNTIME_HEARTBEAT' in report['problems']
    assert 'demo:STALE' in report['problems']


@pytest.mark.parametrize('task', list(PERIODS))
def test_fresh_probe_does_not_hide_stalled_collector(task):
    data = healthy()
    data['tasks'][task]['covered_timestamp'] -= 2 * PERIODS[task] * 1000 + 2700000
    assert task + ':STALE_CANDLES' in evaluate(data, NOW)['problems']


def test_expected_daily_analyzer_and_boundary_work_do_not_false_alarm():
    data = healthy()
    data['tasks']['outcomes'] = {'ok': False, 'attempted_at': NOW - 2300}
    data['heartbeat'] = NOW - 2300
    data['tasks']['demo']['finished_at'] = NOW - 2300
    for task, period in PERIODS.items():
        data['tasks'][task]['covered_timestamp'] = ((NOW - 2300) // period - 1) * period * 1000
    report = evaluate(data, NOW)
    assert report['status'] == 'PASS'
    assert report['running'] == ['outcomes']
    assert 'outcomes:FAILED_OR_TIMED_OUT' in evaluate(data, NOW + 401)['problems']


@pytest.mark.parametrize('task', list(TIMEOUTS))
def test_completed_failure_is_not_treated_as_work_in_progress(task):
    data = healthy()
    data['tasks'][task].update(ok=False, returncode=1, finished_at=NOW - 1)
    assert task + ':FAILED_OR_TIMED_OUT' in evaluate(data, NOW)['problems']


@pytest.mark.parametrize('field,problem', [('active', 'VPS_RUNTIME_INACTIVE'),
    ('clock_ok', 'CLOCK_NOT_SYNCHRONIZED'), ('demo_enabled', 'DEMO_DISABLED_OR_UNKNOWN')])
def test_runtime_gates(field, problem):
    data = healthy()
    data[field] = False
    assert problem in evaluate(data, NOW)['problems']


def test_snapshot_cannot_upload_credentials_logs_or_trade_summaries(tmp_path):
    (tmp_path / 'state').mkdir()
    (tmp_path / 'ACTIVE').touch()
    state = healthy()
    state['DATABASE_URL'] = 'SENSITIVE'
    state['tasks']['demo']['summary'] = {'private': 'SENSITIVE'}
    state['tasks']['demo']['log'] = 'SENSITIVE'
    state['tasks']['other'] = {'secret': 'SENSITIVE'}
    (tmp_path / 'state/status.json').write_text(json.dumps(state))
    (tmp_path / 'credentials.json').write_text('invalid: must never read this')
    result = snapshot(tmp_path, NOW, True)
    assert 'SENSITIVE' not in json.dumps(result)
    assert evaluate(result, NOW)['status'] == 'PASS'


def test_unknown_or_missing_evidence_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        evaluate({}, NOW)
    report = evaluate(snapshot(tmp_path, NOW, False), NOW)
    assert report['status'] == 'ATTENTION'
    assert len(report['problems']) >= 8


def test_future_timestamps_rejected():
    data = healthy()
    data['observed_at'] += 120
    data['heartbeat'] += 180
    data['tasks']['collect_1h']['covered_timestamp'] += 3600000
    report = evaluate(data, NOW)
    assert 'MISSING_FRESH_VPS_PROBE' in report['problems']
    assert 'STALE_RUNTIME_HEARTBEAT' in report['problems']
    assert 'collect_1h:FUTURE_CANDLES' in report['problems']


def test_watchdog_completes_without_vps_or_new_credentials():
    root = Path(__file__).resolve().parents[2]
    text = (root / '.github/workflows/vps-health-watchdog.yml').read_text()
    watchdog = yaml.safe_load(text)
    probe = yaml.safe_load((root / '.github/workflows/vps-health-probe.yml').read_text())
    assert watchdog['jobs']['check']['runs-on'] == 'ubuntu-latest'
    assert 'needs' not in watchdog['jobs']['check']
    assert watchdog['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert probe['concurrency']['cancel-in-progress'] is True
    assert 'secrets.' not in text
    assert "status: 'completed'" in text
    assert "age > 5400" in text
