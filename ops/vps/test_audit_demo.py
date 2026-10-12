import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('audit_demo', Path(__file__).with_name('audit_demo.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class Client:
    base_url = 'https://api-demo.bybit.com'
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []
    def request(self, method, path, params):
        assert method == 'GET'
        self.calls.append((path, dict(params)))
        return self.pages.pop(0)


def test_live_host_and_write_paths_are_rejected_before_transport():
    client = Client([])
    with pytest.raises(ValueError, match='READ_ENDPOINT'):
        audit.Reader(client).rows('/v5/order/create', {})
    assert not client.calls
    client.base_url = 'https://api.bybit.com'
    with pytest.raises(ValueError, match='DEMO_ENDPOINT'):
        audit.Reader(client)


def test_pagination_keeps_distinct_fills_and_deduplicates_repeated_rows():
    a, b = {'execId': 'a', 'orderId': 'same'}, {'execId': 'b', 'orderId': 'same'}
    client = Client([{'list': [a], 'nextPageCursor': 'next'}, {'list': [a, b]}])
    assert audit.Reader(client).rows('/v5/execution/list', {}) == [a, b]
    assert client.calls[1][1]['cursor'] == 'next'


def test_repeated_cursor_fails_instead_of_claiming_complete_evidence():
    client = Client([{'list': [], 'nextPageCursor': 'x'}] * 2)
    with pytest.raises(ValueError, match='REPEATED_PAGE_CURSOR'):
        audit.Reader(client).rows('/v5/execution/list', {})


def test_windows_cover_boundary_once_and_never_exceed_seven_days():
    client = Client([{'list': []}] * 3)
    audit.Reader(client).window('/v5/order/history', {}, 0, 14 * audit.DAY)
    ranges = [(p['startTime'], p['endTime']) for _, p in client.calls]
    assert ranges == [(0, 7*audit.DAY-1), (7*audit.DAY, 14*audit.DAY-1), (14*audit.DAY, 14*audit.DAY)]


class EmptyConnection:
    read_only = False
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, sql, args=None):
        assert self.read_only
        assert sql.startswith(('SELECT', 'SET LOCAL statement_timeout'))
    def fetchone(self):
        return None, None


def test_missing_journal_is_read_only_waiting_state_not_a_bootstrap():
    connection = EmptyConnection()
    client = Client([{'list': []}])
    result = audit.audit(connection, audit.Reader(client), 'executor', 'bybit-experiment', 1)
    assert result['journal_trades'] == 0
    assert result['comparison_status'] == 'WAITING_FOR_DEMO_TRADES'
    assert result['journal_virtual_equity'] == Decimal(300)


def test_failure_codes_never_echo_stored_exception_text(monkeypatch):
    assert audit.failure_code('BYBIT_110007:SECRET') == 'BYBIT_110007'
    assert audit.failure_code('BybitAPIError:BYBIT_10005:SECRET') == 'BYBIT_10005'
    assert audit.failure_code('connection failed: SECRET') == 'UNKNOWN_REQUIRES_PRIVATE_LOG_REVIEW'
    assert audit.failure_code('ENTRY_Rejected') == 'ENTRY_Rejected'
    monkeypatch.setattr(audit, 'load_journal', lambda *args: ([
        {'symbol': 'BTC/USDT', 'ready_timestamp': 0, 'signal_close_ms': 0,
         'status': 'FAILED', 'last_error': 'BybitAPIError:BYBIT_110007:SECRET'}], {}))
    result = audit.audit(None, audit.Reader(Client([{'list': []}])), 'v', 'e', 31*audit.DAY)
    assert result['journal_failure_codes'] == {'BYBIT_110007': 1}
    assert 'SECRET' not in str(result)


def trade():
    return {'symbol': 'BTC/USDT', 'ready_timestamp': 0, 'signal_close_ms': audit.HOUR,
            'exit_due_ms': 13*audit.HOUR, 'submitted_ms': audit.HOUR+120000,
            'status': 'CLOSED', 'risk_amount': '3', 'order_link_id': 'entry-link',
            'exit_order_link_id': 'time-link', 'entry_order_id': 'entry',
            'exit_order_id': None, 'closed_pnl': '-3.35'}


class TradeReader:
    def __init__(self, funding_available=True, linked=True):
        self.funding_available, self.linked = funding_available, linked
    def window(self, path, params, start, end):
        if path == '/v5/order/history':
            return [{'orderId': 'entry', 'side': 'Buy'},
                    {'orderId': 'exit', 'side': 'Sell', 'reduceOnly': True,
                     'parentOrderLinkId': 'entry-link' if self.linked else 'another-trade',
                     'cumExecQty': '.01', 'stopOrderType': 'StopLoss', 'triggerBy': 'MarkPrice'}]
        if path == '/v5/execution/list':
            entry = params['orderId'] == 'entry'
            return [{'execId': params['orderId'], 'execType': 'Trade', 'execQty': '.01',
                     'execPrice': '20000' if entry else '19670', 'execFee': '.025',
                     'feeCurrency': 'USDT', 'side': 'Buy' if entry else 'Sell',
                     'execTime': str(audit.HOUR+120000 if entry else 3*audit.HOUR)}]
        if path == '/v5/position/closed-pnl':
            return [{'orderId': 'exit', 'closedPnl': '-3.35'}, {'orderId': 'unrelated', 'closedPnl': '999'}]
        if path == '/v5/account/transaction-log':
            if not self.funding_available:
                raise RuntimeError('SECRET_MUST_NEVER_APPEAR')
            return [{'symbol': 'BTCUSDT', 'funding': '-.1'}, {'symbol': 'ETHUSDT', 'funding': '-99'}]
        raise AssertionError(path)


def test_linked_stop_fees_funding_and_same_event_model_remain_separate():
    model = {'entry_price': 20000, 'stop_hit': True, 'bars_to_stop': 2, 'gross_return_pct': -1.5}
    r = audit.compare_trade(TradeReader(), trade(), model, 14*audit.HOUR)
    assert r['model']['net_pnl_at_journal_risk'] == Decimal('-3.32')
    assert r['entry_fees_usdt'] == r['exit_fees_usdt'] == Decimal('.025')
    assert r['funding_cashflow_usdt'] == Decimal('-.1')
    assert r['bybit_closed_pnl_usdt'] == Decimal('-3.35')
    assert r['journal_pnl_minus_model_pnl'] == Decimal('-.03')
    assert r['exit_reason'] == 'STOP_LOSS'
    assert r['stop_trigger_types'] == ['MarkPrice']
    assert r['entry_delay_ms'] == 120000
    assert r['entry_gap_from_signal_close_bps'] == 0
    assert r['issues'] == []


def test_unavailable_funding_does_not_erase_fills_or_become_zero():
    r = audit.compare_trade(TradeReader(funding_available=False), trade(), None, 14*audit.HOUR)
    assert r['entry_fees_usdt'] == Decimal('.025')
    assert r['funding_cashflow_usdt'] is None
    assert 'FUNDING_NOT_AVAILABLE' in r['issues']
    assert 'MODEL_OUTCOME_PENDING_OR_MISSING' in r['issues']


def test_unrelated_stop_is_not_assigned_by_symbol_or_nearest_time():
    r = audit.compare_trade(TradeReader(linked=False), trade(), None, 14*audit.HOUR)
    assert r['exit_reason'] == 'UNKNOWN_OR_MIXED'
    assert r['exit_fees_usdt'] is None
    assert 'EXIT_FILLS_NOT_AVAILABLE' in r['issues']


def test_journal_pnl_mismatch_is_exposed_without_reconciling_or_double_counting():
    t = trade()
    t['closed_pnl'] = '999'
    r = audit.compare_trade(TradeReader(), t, None, 14*audit.HOUR)
    assert r['bybit_closed_pnl_usdt'] == Decimal('-3.35')
    assert 'JOURNAL_PNL_DIFFERS_FROM_MATCHED_EXIT_ORDERS' in r['issues']


def test_audit_workflow_uses_existing_credentials_and_does_not_install_release():
    import yaml
    path = Path(__file__).resolve().parents[2] / '.github/workflows/vps-runtime.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert 'audit' in workflow['on']['workflow_dispatch']['inputs']['operation']['options']
    audit_step = next(s for s in workflow['jobs']['manage']['steps'] if s.get('if') == "inputs.operation == 'audit'")
    assert 'credentials.json' in audit_step['run']
    assert 'current/lower' in audit_step['run']
    assert 'audit_demo.py' in audit_step['run']
    assert '--execute' not in audit_step['run']
    assert '--upgrade' not in audit_step['run']
    assert 'secrets.' not in audit_step['run']
    assert workflow['permissions'] == {'contents': 'read'}
