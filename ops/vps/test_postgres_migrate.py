import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import postgres_migrate as migration
from postgres_manager import local_url


def fixture_root(tmp_path):
    root = tmp_path / 'trendscanner-runtime'; (root / 'state').mkdir(parents=True)
    pgroot = tmp_path / 'trendscanner-postgres'
    original = 'postgresql://test:private@ep-example-pooler.c-1.eu.neon.tech/db?sslmode=require'
    (root / 'credentials.json').write_text(json.dumps({'DATABASE_URL': original, 'BYBIT_DEMO_API_SECRET': 'untouched'}))
    return root, pgroot, original


def test_source_uses_existing_neon_credentials_and_direct_dump_connection():
    original = 'postgresql://owner:secret@ep-example-pooler.c-1.eu.neon.tech/db?sslmode=require'
    params = migration.source_parameters(original)
    assert params['host'] == 'ep-example.c-1.eu.neon.tech'
    assert params['password'] == 'secret' and params['sslmode'] == 'require'
    for bad in ['postgresql://localhost/db', 'postgresql://ep-example.neon.tech.evil/db']:
        with pytest.raises(ValueError): migration.source_parameters(bad)


def test_ops_activation_keeps_exact_trading_workspaces_and_credentials(tmp_path):
    root, pgroot, original = fixture_root(tmp_path)
    previous = root / 'releases' / ('a' * 40)
    for name in ['lower', 'four']: (previous / name).mkdir(parents=True)
    (root / 'current').symlink_to(previous)
    patch = tmp_path / 'patch'; patch.mkdir(); (patch / 'runtime.py').write_text('# guard only')
    migration.activate_ops(root, patch, 'b' * 40)
    current = (root / 'current').resolve()
    assert (current / 'lower').resolve() == previous / 'lower'
    assert (current / 'four').resolve() == previous / 'four'
    assert json.loads((root / 'credentials.json').read_text())['DATABASE_URL'] == original


def test_abort_restores_source_after_crash_between_switch_and_preflight(tmp_path):
    root, pgroot, original = fixture_root(tmp_path)
    config_path = root / 'credentials.json'
    config = json.loads(config_path.read_text()); config['DATABASE_URL'] = local_url(pgroot, 'trendscanner_abc')
    config_path.write_text(json.dumps(config))
    (root / 'state/database-source-before-migration.json').write_text(json.dumps({'DATABASE_URL': original}))
    (root / 'state/postgres-migration.json').write_text(json.dumps({'phase': 'STAGED', 'source_config_sha256': hashlib.sha256(original.encode()).hexdigest()}))
    (root / 'state/database-migration-paused.json').write_text('{}')
    migration.abort(root, pgroot)
    restored = json.loads(config_path.read_text())
    assert restored['DATABASE_URL'] == original and restored['BYBIT_DEMO_API_SECRET'] == 'untouched'
    assert not (root / 'state/database-migration-paused.json').exists()


def test_cutover_refuses_missing_offsite_ack_before_any_state_read(tmp_path):
    for value in [None, '', 'not-an-artifact-id']:
        with pytest.raises(ValueError, match='OFFSITE_ENCRYPTED_ARTIFACT_ACK_REQUIRED'):
            migration.apply(tmp_path, tmp_path, value)


def test_runtime_pause_prevents_every_child_and_entry_gate(tmp_path):
    import runtime
    root, pgroot, _ = fixture_root(tmp_path)
    (root / 'ACTIVE').touch()
    (root / 'state/database-migration-paused.json').write_text('{}')
    def forbidden(*args): pytest.fail('no collector, order or analyzer may run during migration')
    assert runtime.cycle(root, clock=lambda:1_800_000_180, run=forbidden, clock_check=forbidden) == 0
    state = json.loads((root / 'state/status.json').read_text())
    assert state['database_migration_paused']
    assert not runtime.entry_gate(state, 1_800_000_180)


def test_future_upgrade_cannot_overwrite_local_database_with_old_neon_secret(tmp_path):
    import install
    root, pgroot, _ = fixture_root(tmp_path)
    (root / 'state/postgres-migration.json').write_text('{"phase":"COMMITTED"}')
    config = json.loads((root / 'credentials.json').read_text())
    config['DATABASE_URL'] = local_url(pgroot, 'trendscanner_verified')
    (root / 'credentials.json').write_text(json.dumps(config))
    assert install.database_configuration(root, 'outdated-Neon-secret') == config['DATABASE_URL']
    config['DATABASE_URL'] = 'postgresql://unreviewed-external/db'
    (root / 'credentials.json').write_text(json.dumps(config))
    with pytest.raises(ValueError): install.database_configuration(root, 'outdated-Neon-secret')


def test_workflows_export_exact_encrypted_files_and_no_private_key_or_raw_dump():
    import yaml
    directory = Path(__file__).parent.parents[1] / '.github/workflows'
    for filename in ['vps-runtime.yml', 'vps-postgres-backup.yml']:
        workflow = yaml.load((directory / filename).read_text(), Loader=yaml.BaseLoader)
        for job in workflow['jobs'].values():
            for step in job['steps']:
                if step.get('uses', '').startswith('actions/upload-artifact'):
                    path = step['with']['path']
                    assert path.endswith('.cms') or path == '${{ steps.encrypted.outputs.file }}'
                    assert '*' not in path and '.dump' not in path and 'credentials' not in path and 'key' not in path
