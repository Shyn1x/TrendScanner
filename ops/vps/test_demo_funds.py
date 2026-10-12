from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location('demo_funds', Path(__file__).with_name('demo_funds.py'))
funds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(funds)


class Client:
    base_url = funds.DEMO_HOST
    def __init__(self, balance=0):
        self.balance = Decimal(balance)
        self.calls = []
    def request(self, method, path, params):
        self.calls.append((method, path, params))
        if method == 'GET':
            assert path == '/v5/account/wallet-balance'
            return {'list': [{'accountType': 'UNIFIED', 'totalAvailableBalance': str(self.balance),
                              'coin': [{'coin': 'USDT', 'walletBalance': str(self.balance), 'spotBorrow': '0'}]}]}
        assert method == 'POST' and path == '/v5/account/demo-apply-money'
        assert params['adjustType'] == 0
        self.balance += Decimal(params['utaDemoApplyMoney'][0]['amountStr'])
        return {}


def test_exact_shortfall_and_repeated_run_cannot_duplicate_deposit():
    client = Client('12.75')
    wrapped = funds.FundsClient(client)
    report = funds.ensure_funds(wrapped, apply=True)
    assert report['requested_virtual_usdt'] == Decimal('287.25')
    assert report['after']['wallet_usdt'] == 300
    assert report['status'] == 'PASS'
    again = funds.ensure_funds(wrapped, apply=True)
    assert again['requested_virtual_usdt'] == 0
    assert len([c for c in client.calls if c[0] == 'POST']) == 1
    assert report['orders_submitted'] == 0


def test_wallet_only_does_not_write_and_does_not_invent_available_margin():
    client = Client()
    report = funds.ensure_funds(funds.FundsClient(client))
    assert report['status'] == 'ATTENTION'
    assert not report['available_margin_confirmed']
    assert all(c[0] == 'GET' for c in client.calls)


def test_mainnet_and_invalid_amounts_are_rejected_before_transport():
    client = Client()
    client.base_url = 'https://api.bybit.com'
    with pytest.raises(ValueError, match='DEMO_HOST'):
        funds.FundsClient(client)
    assert not client.calls
    client.base_url = funds.DEMO_HOST
    for amount in [Decimal('-1'), Decimal(0), Decimal(301), Decimal('NaN')]:
        with pytest.raises((ValueError, ArithmeticError)):
            funds.FundsClient(client).add_shortfall(amount)
    assert not client.calls


def test_ambiguous_post_failure_is_never_retried():
    class Uncertain(Client):
        def request(self, method, path, params):
            result = super().request(method, path, params)
            if method == 'POST':
                raise TimeoutError()
            return result
    client = Uncertain()
    with pytest.raises(TimeoutError):
        funds.ensure_funds(funds.FundsClient(client), apply=True)
    assert len([c for c in client.calls if c[0] == 'POST']) == 1
    assert funds.ensure_funds(funds.FundsClient(client), apply=True)['requested_virtual_usdt'] == 0


def test_missing_balance_field_in_existing_coin_fails_before_top_up():
    class Invalid(Client):
        def request(self, *args):
            return {'list': [{'accountType': 'UNIFIED', 'coin': [{'coin': 'USDT'}]}]}
    client = Invalid()
    with pytest.raises(KeyError):
        funds.ensure_funds(funds.FundsClient(client), apply=True)


def test_success_response_without_credit_is_not_claimed_as_funded():
    class NoCredit(Client):
        def request(self, method, path, params):
            if method == 'POST':
                self.calls.append((method, path, params))
                return {}
            return super().request(method, path, params)
    client = NoCredit()
    report = funds.ensure_funds(funds.FundsClient(client), apply=True, sleep=lambda _: None)
    assert report['status'] == 'ATTENTION'
    assert not report['wallet_target_met']
    assert len([c for c in client.calls if c[0] == 'POST']) == 1


def test_funding_requires_main_and_manual_dispatch():
    path = Path(__file__).resolve().parents[2] / '.github/workflows/vps-runtime.yml'
    config = yaml.safe_load(path.read_text())
    step = next(s for s in config['jobs']['manage']['steps'] if s.get('name') == 'Read or request virtual Demo USDT')
    assert "inputs.operation == 'fund-demo'" in step['if']
    assert "refs/heads/main" in step['if']
    assert config['jobs']['manage']['if'] == "github.event_name == 'workflow_dispatch'"
    assert '--apply' in step['run'] and 'run_command' in step['run']
