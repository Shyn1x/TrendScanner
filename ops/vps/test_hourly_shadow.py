import copy
import json
from pathlib import Path
import sys
import pytest

DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(DIRECTORY))
import hourly_shadow as shadow
BASE = 1_800_000_000_000


def trade(symbol='BTC/USDT', status='OPEN'):
    return {'symbol': symbol, 'ready_timestamp': BASE, 'signal_close_ms': BASE + shadow.HOUR,
            'exit_due_ms': BASE + 13 * shadow.HOUR, 'status': status,
            'risk_amount': '3', 'quantity': '2', 'stop_price': '98.5',
            'entry_order_id': 'entry-' + symbol, 'closed_pnl': None}


class Reader:
    def rows(self, endpoint, params):
        assert endpoint == '/v5/execution/list'
        return [{'execId': 'fill', 'execType': 'Trade', 'orderId': params['orderId'],
                 'side': 'Buy', 'execQty': '2', 'execPrice': '100', 'execFee': '.11',
                 'execTime': BASE + shadow.HOUR + 6 * shadow.MINUTE + 4000}]


def bars(start, end, *, low='99', close='101'):
    return [[ts, '100', '102', low, close] for ts in range(start, end, shadow.MINUTE)]


def entry():
    return shadow.freeze_entry(Reader(), trade(), BASE + 2 * shadow.HOUR)


def test_activation_excludes_old_signals_and_contract_is_frozen():
    state = shadow.new_state(BASE + shadow.MINUTE)
    assert state['start_signal_close_ms'] == BASE + shadow.HOUR
    old = trade(); old['signal_close_ms'] = BASE
    shadow.update(state, [old], Reader(), lambda s: None, now_ms=BASE + 14 * shadow.HOUR)
    assert not state['cohort'] and not state['candidates']
    state['contract'] = {**state['contract'], 'target': 60}
    with pytest.raises(ValueError, match='CONTRACT_CHANGED'):
        shadow.validate_state(state)


def test_full_fills_deduplication_delay_price_stop_and_partial_fill_wait():
    reader = Reader(); original = reader.rows
    reader.rows = lambda *args: original(*args) * 2
    frozen = shadow.freeze_entry(reader, trade(), BASE + 2 * shadow.HOUR)
    assert frozen['price'] == '100' and frozen['quantity'] == '2'
    assert frozen['stop_price'] == '98.5' and frozen['fees_usdt'] == '0.11'
    assert frozen['delay_ms'] == 6 * shadow.MINUTE + 4000
    t = trade(); t['quantity'] = '3'
    assert shadow.freeze_entry(reader, t, BASE + 2 * shadow.HOUR) is None


def test_execution_identity_and_unknown_fees_are_not_invented():
    reader = Reader(); original = reader.rows
    reader.rows = lambda *args: [{**original(*args)[0], 'orderId': 'other'}]
    assert shadow.freeze_entry(reader, trade(), BASE + 2 * shadow.HOUR) is None
    reader.rows = lambda *args: [{**original(*args)[0], 'execFee': ''}]
    with pytest.raises(ValueError, match='FEES_UNKNOWN'):
        shadow.freeze_entry(reader, trade(), BASE + 2 * shadow.HOUR)


def test_contiguous_closed_minutes_ohlc_and_public_path_allowlist():
    start, end = BASE, BASE + 2 * shadow.MINUTE
    requests = []
    def get(path, params):
        requests.append((path, params)); return list(reversed(bars(start, end)))
    assert shadow.minute_window('BTC/USDT', start, end, get=get) == bars(start, end)
    assert requests[0][1]['end'] == end - 1
    shadow.minute_window('BTC/USDT', start, end, mark=True, get=get)
    assert requests[-1][0] == '/v5/market/mark-price-kline'
    with pytest.raises(ValueError, match='MISSING_CLOSED'):
        shadow.minute_window('BTC/USDT', start, end, get=lambda *args: bars(start, end)[1:])
    with pytest.raises(ValueError, match='INVALID_OHLC'):
        shadow.minute_window('BTC/USDT', start, end, get=lambda *args: bars(start, end, low='103'))
    with pytest.raises(ValueError, match='PUBLIC_MARKET_GET_REQUIRED'):
        shadow.public_get('/v5/order/create', {})


def test_pre_entry_minutes_excluded_and_costs_applied_once():
    e = entry(); start = e['last_ms'] // shadow.MINUTE * shadow.MINUTE; due = BASE + 2 * shadow.HOUR
    result = shadow.outcome(e, bars(start, due), bars(start, due), due)
    assert not result['stop_hit']
    assert float(result['net_price_pct']) == pytest.approx(.84)
    assert float(result['model_pnl_usdt']) == pytest.approx(1.68)
    with pytest.raises(ValueError, match='INCOMPLETE_OUTCOME_PATH'):
        shadow.outcome(e, bars(start - shadow.MINUTE, due), bars(start, due), due)


def test_mark_stop_entry_minute_bounds_and_trade_gap_loss():
    e = entry(); start = e['last_ms'] // shadow.MINUTE * shadow.MINUTE; due = BASE + 2 * shadow.HOUR
    trades, marks = bars(start, due), bars(start, due)
    marks[0][3] = '98'
    primary = shadow.outcome(e, trades, marks, due)
    alternative = shadow.outcome(e, trades, marks, due, optimistic=True)
    assert primary['stop_hit'] and primary['entry_minute_ambiguous']
    assert float(primary['net_price_pct']) == pytest.approx(-1.66)
    assert not alternative['stop_hit']
    marks[0][3] = '99'; marks[1][3] = '97'
    trades[1] = [start + shadow.MINUTE, '97', '100', '96', '99']
    result = shadow.outcome(e, trades, marks, due)
    assert not result['entry_minute_ambiguous']
    assert float(result['net_price_pct']) == pytest.approx(-3.16)


def test_trade_low_does_not_trigger_mark_stop():
    e = entry(); start = e['last_ms'] // shadow.MINUTE * shadow.MINUTE; due = BASE + 2 * shadow.HOUR
    assert not shadow.outcome(e, bars(start, due, low='97'), bars(start, due), due)['stop_hit']


def test_durable_enrollment_before_outcome_restart_and_separate_actual_pnl():
    state = shadow.new_state(BASE); snapshots = []; calls = []
    def save(s): snapshots.append(copy.deepcopy(s))
    def market(symbol, start, end, *, mark=False):
        assert snapshots[-1]['cohort'] == ['BTC/USDT:' + str(BASE)]
        calls.append(mark); return bars(start, end)
    now = BASE + 14 * shadow.HOUR
    summary = shadow.update(state, [trade()], Reader(), save, now_ms=now, market=market)
    assert summary['paired_complete'] == 1 and len(calls) == 4
    assert summary['last_success_ms'] == now
    restarted = json.loads(json.dumps(state)); closed = trade(status='CLOSED'); closed['closed_pnl'] = '-7'
    shadow.update(restarted, [closed], Reader(), save, now_ms=now + 300000,
                  market=lambda *a, **k: pytest.fail('duplicate fetch'))
    candidate = next(iter(restarted['candidates'].values()))
    assert candidate['actual_closed_pnl_usdt'] == '-7'
    assert candidate['outcomes']['1']['funding_included'] is False
    assert len(restarted['cohort']) == 1


def test_maturity_no_peeking_and_missing_data_retry_retains_cohort():
    state = shadow.new_state(BASE); now = BASE + 2 * shadow.HOUR + 119999
    shadow.update(state, [trade()], Reader(), lambda s: None, now_ms=now,
                  market=lambda *a, **k: pytest.fail('not mature'))
    assert shadow.counts(state)['one_hour_complete'] == 0
    def missing(*args, **kwargs): raise ValueError('MISSING_CLOSED_MINUTE')
    summary = shadow.update(state, [trade()], Reader(), lambda s: None, now_ms=now + 1, market=missing)
    assert summary['retry_errors'] == 1 and summary['enrolled'] == 1
    assert not next(iter(state['candidates'].values()))['outcomes']


def test_fifty_cap_and_counts_only_progress():
    state = shadow.new_state(BASE)
    summary = shadow.update(state, [trade('COIN' + str(i) + '/USDT') for i in range(55)], Reader(), lambda s: None,
                            now_ms=BASE + shadow.HOUR + 30 * shadow.MINUTE)
    assert summary['enrolled'] == 50 and not summary['checkpoint_ready']
    text = json.dumps(summary)
    assert all(word not in text for word in ['COIN', 'fees_usdt', 'quantity', 'pnl', 'order_id'])


def test_late_entry_is_visible_and_ineligible_before_outcome_evaluation():
    reader = Reader(); original = reader.rows
    reader.rows = lambda *args: [{**original(*args)[0], 'execTime': BASE + 2 * shadow.HOUR}]
    summary = shadow.update(shadow.new_state(BASE), [trade()], reader, lambda s: None, now_ms=BASE + 3 * shadow.HOUR)
    assert summary['enrolled'] == 0
    assert summary['unpaired_reasons'] == {'ENTRY_AFTER_1H_EXIT': 1}


def test_journal_readonly_and_new_hourly_long_experiment_scope():
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None):
            assert connection.read_only
            assert sql.startswith(('SELECT ', 'SET LOCAL '))
            if params:
                assert params == ('executor', 'experiment', '1h', 'LONG', BASE)
                assert 'signal_close_ms >= %s' in sql
        def fetchall(self): return []
    class Connection:
        read_only = False
        def cursor(self): return Cursor()
    connection = Connection()
    assert shadow.load_journal(connection, 'executor', 'experiment', BASE) == []


def test_scheduler_shadow_last_and_failure_does_not_gate_entries(tmp_path):
    import runtime
    assert runtime.ORDER[-1] == 'hourly_shadow'
    release = tmp_path / 'releases' / 'sha'
    args, workspace = runtime.command('hourly_shadow', release, {}, BASE / 1000)
    assert str(release / 'ops/hourly_shadow.py') in args and args[-1] == str(tmp_path)
    assert workspace == release / 'lower'
    now = BASE / 1000 + 180
    state = {'tasks': {'collect_1h': {'ok': True, 'covered_timestamp': (int(now)//3600-1)*shadow.HOUR, 'finished_at': now},
                       'hourly_shadow': {'ok': False}}}
    assert runtime.entry_gate(state, now)


def test_execution_get_only_and_no_raw_state_output():
    calls = []
    class Client:
        base_url = 'https://api-demo.bybit.com'
        def request(self, *args, **kwargs): calls.append((args, kwargs)); return {'list': []}
    client = shadow.ExecutionOnlyClient(Client())
    assert client.request('GET', '/v5/execution/list', {}) == {'list': []}
    assert calls == [(('GET', '/v5/execution/list', {}), {'auth': True})]
    for method, path in [('POST', '/v5/order/create'), ('GET', '/v5/order/create'), ('POST', '/v5/execution/list')]:
        with pytest.raises(ValueError, match='EXECUTION_GET_ONLY'):
            client.request(method, path, {})
    assert len(calls) == 1
    Client.base_url = 'https://api.bybit.com'
    with pytest.raises(ValueError, match='DEMO_HOST_REQUIRED'):
        shadow.ExecutionOnlyClient(Client())
    source = (DIRECTORY / 'hourly_shadow.py').read_text()
    assert "method != 'GET' or endpoint != '/v5/execution/list'" in source
    assert "method='GET'" in source
    assert 'print(json.dumps(state' not in source and 'print(json.dumps(trade' not in source


def test_activation_retains_exact_frozen_workspaces_and_credentials(tmp_path, monkeypatch):
    import activate_hourly_shadow as activation
    import subprocess
    root = tmp_path / 'runtime'; previous = root / 'releases' / ('a' * 40)
    for name in ['lower', 'four', 'ops']:
        (previous / name).mkdir(parents=True)
    (root / 'state').mkdir(); (root / 'ACTIVE').touch()
    (root / 'current').symlink_to(previous)
    credentials = root / 'credentials.json'; credentials.write_text('{"DATABASE_URL":"test-database"}')
    patch = tmp_path / 'patch'; patch.mkdir(); (patch / 'runtime.py').write_text('# reviewed scheduler')
    saved_credentials = credentials.read_bytes()
    monkeypatch.setattr(activation.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, 'yes\n', ''))
    checks = []
    def preflight(args, workspace, task, config):
        checks.append(args)
        assert config == {'DATABASE_URL': 'test-database'}
        assert args[-1] == '--preflight' and workspace.resolve() == previous / 'lower'
        assert (root / 'current').resolve() == previous
        return 0, 'HOURLY SHADOW PREFLIGHT: PASS\n'
    monkeypatch.setattr(activation, 'run_command', preflight)
    activation.activate(root, patch, 'b' * 40)
    current = (root / 'current').resolve()
    assert current.name == 'b' * 40
    assert (current / 'lower').resolve() == previous / 'lower'
    assert (current / 'four').resolve() == previous / 'four'
    assert credentials.read_bytes() == saved_credentials
    assert (root / 'state/hourly-shadow.json').stat().st_mode & 0o777 == 0o600
    first_state = (root / 'state/hourly-shadow.json').read_bytes()
    activation.activate(root, patch, 'b' * 40)
    assert (root / 'state/hourly-shadow.json').read_bytes() == first_state
    assert len(checks) == 1


def test_failed_shadow_preflight_keeps_current_and_does_not_enroll(tmp_path, monkeypatch):
    import activate_hourly_shadow as activation
    import subprocess
    root = tmp_path / 'runtime'; previous = root / 'releases' / ('a' * 40)
    for name in ['lower', 'four']:
        (previous / name).mkdir(parents=True)
    (root / 'state').mkdir(); (root / 'ACTIVE').touch(); (root / 'current').symlink_to(previous)
    (root / 'credentials.json').write_text('{"DATABASE_URL":"test"}')
    patch = tmp_path / 'patch'; patch.mkdir()
    monkeypatch.setattr(activation.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, 'yes\n', ''))
    monkeypatch.setattr(activation, 'run_command', lambda *a, **k: (1, 'PREFLIGHT_FAILED'))
    with pytest.raises(ValueError, match='SHADOW_PREFLIGHT_FAILED'):
        activation.activate(root, patch, 'c' * 40)
    assert (root / 'current').resolve() == previous
    assert not (root / 'state/hourly-shadow.json').exists()
