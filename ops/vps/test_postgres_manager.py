import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import postgres_manager as pg


def test_local_url_is_restricted_to_this_private_socket_and_app_role(tmp_path):
    url = pg.local_url(tmp_path, 'trendscanner_20261101_123abc')
    assert pg.validate_local_url(tmp_path, url) == 'trendscanner_20261101_123abc'
    for bad in ['postgresql://remote/trendscanner', 'postgresql:///postgres', url.replace('55432', '5432'),
                url.replace('user=trendscanner', 'user=postgres'), url + '&password=secret']:
        with pytest.raises(ValueError):
            pg.validate_local_url(tmp_path, bad)
    with pytest.raises(ValueError):
        pg.local_url(tmp_path, 'postgres; DROP DATABASE x')


def test_pg_environment_does_not_inherit_trading_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('BYBIT_DEMO_API_SECRET', 'must-not-leak')
    env = pg.pg_env(tmp_path, 'trendscanner', admin=False)
    assert 'BYBIT_DEMO_API_SECRET' not in env
    assert env['PGHOST'] == str(tmp_path / 'socket') and env['PGUSER'] == 'trendscanner'


def test_cron_preserves_runtime_and_other_jobs_is_idempotent_and_private(tmp_path, monkeypatch):
    tab = ['17 3 * * * echo existing\n* * * * * python3 /runtime.py\n']
    original = tab[0]
    monkeypatch.setattr(pg.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, tab[0], ''))
    def command(args, **kwargs):
        if args == ['crontab', '-']:
            tab[0] = kwargs['input']; return ''
        assert args == ['crontab', '-l']; return tab[0]
    monkeypatch.setattr(pg, 'command', command)
    pg.install_cron(tmp_path); first = tab[0]; pg.install_cron(tmp_path)
    assert first == tab[0] and first.startswith(original)
    assert first.count('# TrendScanner private PostgreSQL') == 1
    assert 'ensure --root' in first and 'backup --root' in first
    assert (tmp_path / 'crontab-before.json').stat().st_mode & 0o777 == 0o600


def test_backup_encryption_roundtrip_and_tamper_rejection(tmp_path):
    ops = tmp_path / 'ops'; ops.mkdir()
    key, certificate = tmp_path / 'private.pem', ops / 'backup-recipient.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                    '-out', str(certificate), '-days', '1', '-subj', '/CN=Test'], capture_output=True, check=True)
    plain = tmp_path / 'raw'; plain.write_bytes(b'private rows and exact decimals\n' * 100)
    cipher = tmp_path / 'cipher.cms'; record = pg.encrypt(tmp_path, plain, cipher)
    assert plain.read_bytes() not in cipher.read_bytes() and record['size_bytes'] == cipher.stat().st_size
    decoded = tmp_path / 'decoded'
    args = ['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER', '-in', str(cipher),
            '-recip', str(certificate), '-inkey', str(key), '-out', str(decoded)]
    subprocess.run(args, capture_output=True, check=True)
    assert decoded.read_bytes() == plain.read_bytes()
    bad = bytearray(cipher.read_bytes()); bad[-1] ^= 1; cipher.write_bytes(bad)
    assert subprocess.run(args, capture_output=True).returncode != 0


def test_secrets_are_not_echoed_on_failed_database_command(monkeypatch):
    monkeypatch.setattr(pg.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, 'private-row', 'secret-password'))
    with pytest.raises(RuntimeError) as exc:
        pg.command(['/private/psql', 'postgresql://user:secret-password@host/db'])
    assert str(exc.value) == 'COMMAND_FAILED:psql:1'


def test_postgres_death_restarts_only_the_private_cluster(tmp_path, monkeypatch):
    data = tmp_path / 'data'; data.mkdir(); (data / 'PG_VERSION').write_text('18')
    monkeypatch.setattr(pg.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 3, '', ''))
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        return '|' + str(data) + '\n' if 'psql' in str(args[0]) else ''
    monkeypatch.setattr(pg, 'command', command)
    pg.ensure_running(tmp_path)
    assert calls[0][-1] == 'start' and str(data) in list(map(str, calls[0]))
    monkeypatch.setattr(pg, 'command', lambda *a, **k: 'localhost|/other/data\n')
    with pytest.raises(ValueError, match='NOT_PRIVATE_CLUSTER'):
        pg.ensure_running(tmp_path)
