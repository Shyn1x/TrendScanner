"""Manual virtual-USDT top-up; strictly Bybit Demo, never order submission."""
import argparse
from decimal import Decimal
import fcntl
import json
from pathlib import Path
import time

TARGET_USDT = Decimal('300')
DEMO_HOST = 'https://api-demo.bybit.com'


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError('INVALID_DEMO_BALANCE')
    return result


class FundsClient:
    def __init__(self, client):
        if client.base_url != DEMO_HOST:
            raise ValueError('DEMO_HOST_REQUIRED')
        self.client = client

    def wallet(self):
        # Query only the requested test asset; omit account identifiers.
        result = self.client.request('GET', '/v5/account/wallet-balance',
                                     {'accountType': 'UNIFIED', 'coin': 'USDT'})
        accounts = result.get('list', [])
        if len(accounts) != 1 or accounts[0].get('accountType') != 'UNIFIED':
            raise ValueError('UNIFIED_DEMO_WALLET_REQUIRED')
        account = accounts[0]
        coins = account.get('coin', [])
        if any(c.get('coin') != 'USDT' for c in coins) or len(coins) > 1:
            raise ValueError('UNEXPECTED_DEMO_WALLET_COINS')
        # Bybit omits zero asset/liability rows. Missing fields in a present row
        # are invalid, rather than an excuse to apply another 300 USDT.
        coin = coins[0] if coins else {}
        balance = decimal(coin['walletBalance']) if coins else Decimal(0)
        available = account.get('totalAvailableBalance')
        return {'wallet_usdt': balance,
                'account_available_usd': decimal(available) if available not in (None, '') else None,
                'usdt_collateral_enabled': coin.get('collateralSwitch'),
                'spot_borrow_usdt': decimal(coin.get('spotBorrow') or '0')}

    def add_shortfall(self, amount):
        if not Decimal(0) < amount <= TARGET_USDT:
            raise ValueError('DEMO_TOP_UP_LIMIT_EXCEEDED')
        # Only this zero-value sandbox operation is exposed. Never reduce funds,
        # transfer assets, change the account mode, or create/cancel an order.
        return self.client.request('POST', '/v5/account/demo-apply-money',
                                   {'adjustType': 0, 'utaDemoApplyMoney': [
                                       {'coin': 'USDT', 'amountStr': format(amount, 'f')}]})


def ensure_funds(client, *, apply=False, sleep=time.sleep):
    before = client.wallet()
    if before['spot_borrow_usdt']:
        raise ValueError('DEMO_BORROW_REQUIRES_REVIEW')
    shortfall = max(Decimal(0), TARGET_USDT - before['wallet_usdt'])
    added = Decimal(0)
    after = before
    if apply and shortfall:
        # Do not retry POST on an ambiguous network response. A fresh manual run
        # must read the wallet again before calculating any further request.
        client.add_shortfall(shortfall)
        added = shortfall
        for attempt in range(3):
            after = client.wallet()
            if after['wallet_usdt'] >= TARGET_USDT:
                break
            if attempt < 2:
                sleep(attempt + 1)
    funded = after['wallet_usdt'] >= TARGET_USDT
    available = after['account_available_usd']
    return {'endpoint': DEMO_HOST, 'target_wallet_usdt': TARGET_USDT,
            'before': before, 'requested_virtual_usdt': added, 'after': after,
            'wallet_target_met': funded,
            'available_margin_confirmed': available is not None and available > 0,
            'status': 'PASS' if funded and available is not None and available > 0 else 'ATTENTION',
            'orders_submitted': 0}


def main():
    import os
    from bybit_demo_executor import BybitDemoClient
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-runtime')
    args = parser.parse_args()
    client = FundsClient(BybitDemoClient(os.environ['BYBIT_DEMO_API_KEY'], os.environ['BYBIT_DEMO_API_SECRET']))
    # Serialize with the local runtime so its next entry cannot spend funds
    # between our before/after balance checks. No scheduler configuration edits.
    with (args.root / 'state/cycle.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = ensure_funds(client, apply=args.apply)
    output = json.dumps(result, indent=2, sort_keys=True, default=str)
    print(output)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('### Bybit Demo virtual funds\n\n```json\n' + output + '\n```\n')
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
