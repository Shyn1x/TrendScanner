from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_demo import DAY
from audit_demo_bulk import BudgetExhausted, BulkReader


class Client:
    base_url = 'https://api-demo.bybit.com'

    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def request(self, method, path, params):
        assert method == 'GET'
        self.calls.append((path, dict(params)))
        return next(self.pages)


def test_grouped_execution_pages_are_reused_and_filtered_on_resume(tmp_path):
    a = {'orderId': 'a', 'execId': 'one', 'execTime': 200}
    b = {'orderId': 'b', 'execId': 'two', 'execTime': 300}
    client = Client([{'list': [a], 'nextPageCursor': 'next'}, {'list': [a, b]}])
    reader = BulkReader(client, tmp_path, 0, DAY)
    path = '/v5/execution/list'
    assert reader.window(path, {'orderId': 'a'}, 100, 400) == [a]
    assert reader.window(path, {'orderId': 'b'}, 100, 250) == []
    assert 'orderId' not in client.calls[0][1]
    assert len(client.calls) == 2
    resumed = BulkReader(Client([]), tmp_path, 0, DAY, max_requests=0)
    assert resumed.window(path, {'orderId': 'b'}, 100, 400) == [b]
    assert resumed.requests == 0


def test_partial_page_is_not_cached_and_retry_preserves_bucket_boundaries(tmp_path):
    path = '/v5/account/transaction-log'
    truncated = Client([{'list': [], 'nextPageCursor': 'next'}])
    reader = BulkReader(truncated, tmp_path, 10, 8 * DAY, max_requests=1)
    with pytest.raises(BudgetExhausted):
        reader.window(path, {}, 10, 8 * DAY)
    assert list(tmp_path.iterdir()) == []
    client = Client([{'list': []}, {'list': []}])
    resumed = BulkReader(client, tmp_path, 10, 8 * DAY)
    assert resumed.window(path, {}, 10, 8 * DAY) == []
    assert [(q['startTime'], q['endTime']) for _, q in client.calls] == [
        (10, 10 + 7 * DAY - 1), (10 + 7 * DAY, 8 * DAY)]


def test_bulk_workflow_only_uploads_ciphertext():
    path = Path(__file__).resolve().parents[2] / '.github/workflows/vps-runtime.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert 'audit-bulk' in workflow['on']['workflow_dispatch']['inputs']['operation']['options']
    steps = workflow['jobs']['manage']['steps']
    run = next(s['run'] for s in steps if s.get('id') == 'audit-bulk')
    assert "'cms', '-encrypt'" in run and "print(output)" not in run
    assert '--execute' not in run and '--upgrade' not in run
    upload = next(s for s in steps if s.get('with', {}).get('name', '').startswith('demo-audit-encrypted'))
    assert upload['with']['path'] == '${{ runner.temp }}/demo-audit-${{ github.run_id }}.cms'
