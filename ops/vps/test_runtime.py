import importlib.util
import json
from pathlib import Path
import pytest
import yaml

DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('runtime', DIRECTORY / 'runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_closed_candles_wait_two_minutes_and_daily_outcomes_are_utc():
    boundary = 1_800_000_000  # Hour-aligned UTC epoch.
    assert runtime.slot('collect_1h', boundary + 119) < runtime.slot('collect_1h', boundary + 120)
    assert runtime.slot('collect_15m', boundary + 119) < runtime.slot('collect_15m', boundary + 120)
    midnight = boundary // 86400 * 86400
    assert runtime.slot('outcomes', midnight + 9599) < runtime.slot('outcomes', midnight + 9600)


def test_failed_task_retries_after_five_minutes_without_repeating_success():
    now = 1_800_000_180
    state = {'tasks': {'collect_1h': {'slot': runtime.slot('collect_1h', now), 'ok': False, 'attempted_at': now}}}
    assert not runtime.due('collect_1h', state, now + 299)
    assert runtime.due('collect_1h', state, now + 300)
    state['tasks']['collect_1h']['ok'] = True
    assert not runtime.due('collect_1h', state, now + 300)


@pytest.mark.parametrize('ok,lag,allowed', [(True,0,True),(True,1,False),(False,0,False)])
def test_entry_gate_requires_exact_successful_current_hour_coverage(ok, lag, allowed):
    now = 1_800_000_180
    target = (now // 3600 - 1 - lag) * 3_600_000
    state = {'tasks': {'collect_1h': {'ok': ok, 'covered_timestamp': target, 'finished_at': now-10}}}
    assert runtime.entry_gate(state, now) is allowed
    args, _ = runtime.command('demo', Path('/release'), state, now)
    assert args[-1] == ('--execute' if allowed else '--reconcile-only')


def test_crossing_hour_boundary_blocks_entries_until_next_collector():
    now = 1_800_000_180
    state = {'tasks': {'collect_1h': {'ok': True, 'covered_timestamp': (now//3600-1)*3_600_000, 'finished_at': now}}}
    assert runtime.entry_gate(state, now)
    assert not runtime.entry_gate(state, now//3600*3600+3600)


def test_partial_catchup_does_not_unlock_entries():
    output = 'Latest closed target: 123\nCatch-up remaining after this run: 3\nNEON WRITE + SEQUENTIAL COVERAGE: PASS\n'
    assert runtime.collector_result(output) == (False, None)
    assert runtime.collector_result(output.replace(': 3', ': 0')) == (True, 123)
    assert runtime.collector_result('Latest closed target: 123\nUP TO DATE: PASS\n') == (True, 123)


def fixture_root(tmp_path):
    for folder in ('state','logs','release'):
        (tmp_path/folder).mkdir()
    (tmp_path/'current').symlink_to(tmp_path/'release', target_is_directory=True)
    (tmp_path/'ACTIVE').touch()
    (tmp_path/'credentials.json').write_text(json.dumps({'BYBIT_DEMO_ENABLED':'true'}))
    return tmp_path


def test_collector_failure_preserves_demo_reconciliation_and_other_timeframes(tmp_path):
    root = fixture_root(tmp_path)
    now = 1_800_000_180
    calls = []
    def run(args, workspace, task, config):
        calls.append((task,args[-1]))
        if task == 'collect_1h':
            return 1, 'FAIL\n'
        if task.startswith('collect_'):
            period = runtime.PERIODS[task]
            target = (now//period-1)*period*1000
            return 0, f'Latest closed target: {target}\nUP TO DATE: PASS\n'
        if task == 'demo':
            return 0, '{"entries_submitted":0}\nBYBIT DEMO EXECUTOR: PASS\n'
        return 0, 'LOWER-TF OUTCOME WRITE: PASS\n'
    assert runtime.cycle(root, clock=lambda:now, run=run, clock_check=lambda:True) == 1
    assert ('demo','--reconcile-only') in calls
    state = json.loads((root/'state/status.json').read_text())
    assert state['tasks']['collect_1h']['ok'] is False
    assert state['tasks']['demo']['ok'] is True
    assert state['tasks']['collect_4h']['ok'] is True


def test_bad_clock_prevents_all_writes_and_orders(tmp_path):
    root = fixture_root(tmp_path)
    def forbidden(*args):
        raise AssertionError('no task may run against an unsynchronized clock')
    with pytest.raises(RuntimeError,match='CLOCK_NOT_SYNCHRONIZED'):
        runtime.cycle(root, clock=lambda:1_800_000_180, run=forbidden, clock_check=lambda:False)
    assert json.loads((root/'state/status.json').read_text())['clock_ok'] is False


def test_restart_after_child_crash_does_not_remember_false_success(tmp_path):
    root = fixture_root(tmp_path)
    def crash(*args):
        raise RuntimeError('process crashed')
    with pytest.raises(RuntimeError,match='process crashed'):
        runtime.cycle(root, clock=lambda:1_800_000_180, run=crash, clock_check=lambda:True)
    state = json.loads((root/'state/status.json').read_text())
    assert not state['tasks']['collect_1h']['ok']
    assert runtime.due('collect_1h',state,1_800_000_480)


def test_secrets_redacted_and_private_atomic_status(tmp_path):
    config = {'DATABASE_URL':'postgres://private', 'BYBIT_DEMO_API_KEY':'private-key','BYBIT_DEMO_API_SECRET':'private-secret'}
    masked = runtime.redact(' '.join(config.values()),config)
    assert all(value not in masked for value in config.values())
    path=tmp_path/'status.json'
    runtime.atomic_json(path,{'ok':True})
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())['ok']


def test_health_detects_inactive_runtime_and_outdated_coverage():
    now = 1_800_000_180
    state={'heartbeat':now,'clock_ok':True,'tasks':{'collect_1h':{'ok':True,'covered_timestamp':0}}}
    report=runtime.health(state,now,active=False)
    assert report['status']=='ATTENTION'
    assert 'VPS_RUNTIME_INACTIVE' in report['problems']
    assert 'collect_1h:BEHIND_CLOSED_CANDLES' in report['problems']


def test_existing_actions_writes_have_local_ownership_guard():
    root=DIRECTORY.parents[1]
    for name,job_name in [('prospective-lower-tf.yml','validate-and-collect'),('prospective-4h.yml','collect'),
                          ('lower-tf-outcomes.yml','analyze'),('bybit-demo-executor.yml','demo-cycle')]:
        data=yaml.safe_load((root/'.github/workflows'/name).read_text())
        job=data['jobs'][job_name]
        assert 'TREND_SCANNER_RUNTIME' in job['if']
        assert job['steps'][0]['id']=='owner'
        assert 'trendscanner-runtime/ACTIVE' in job['steps'][0]['run']
        for step in job['steps'][1:]:
            assert "steps.owner.outputs.write == 'true'" in step['if']
            assert '&& (>-' not in step['if']
    manager=yaml.load((root/'.github/workflows/vps-runtime.yml').read_text(),Loader=yaml.BaseLoader)
    assert manager['on']['workflow_dispatch']['inputs']['operation']['default']=='inspect'
