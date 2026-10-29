"""Snapshot-copy, verify and switch a blocked Neon app to its private VPS DB.

Source is read-only. Raw dumps/row hashes remain private. Cutover requires a
successful offsite upload of the encrypted pre-migration dump. No order API.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from postgres_manager import binary, command, encrypt, ensure_running, local_url, pg_env, private_dir, validate_local_url
from runtime import atomic_json, run_command

REQUIRED_TABLES = {'lower_tf_prospective_events', 'bybit_demo_strategy_trades'}


def source_parameters(url):
    from psycopg.conninfo import conninfo_to_dict
    params = conninfo_to_dict(url)
    host = params.get('host', '')
    if not host.startswith('ep-') or not host.endswith('.neon.tech') or ',' in host:
        raise ValueError('EXISTING_NEON_SOURCE_REQUIRED')
    params['host'] = host.replace('-pooler.', '.')
    params['connect_timeout'] = '10'
    return params


def source_environment(root, params):
    from psycopg.conninfo import make_conninfo
    env = pg_env(root)
    env['PGDATABASE'] = make_conninfo(**params)
    # PGDATABASE carries the complete DSN in the environment, never argv/logs.
    for name in ['PGHOST', 'PGPORT', 'PGUSER']:
        env.pop(name, None)
    return env


def manifest(connection):
    from psycopg import sql
    result = {}
    with connection.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'")
        if cur.fetchall():
            raise ValueError('SOURCE_EXTENSIONS_REQUIRE_REVIEW')
        cur.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
                    " WHERE c.relkind IN ('r','p','S') AND n.nspname NOT IN ('pg_catalog','information_schema')"
                    " AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1,2")
        relations = cur.fetchall()
        if any(schema != 'public' for schema, _ in relations):
            raise ValueError('NON_PUBLIC_DATA_REQUIRES_REVIEW')
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables = [r[0] for r in cur.fetchall()]
        if not REQUIRED_TABLES.issubset(tables):
            raise ValueError('INCOMPLETE_STRATEGY_DATABASE')
        for table in tables:
            cur.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier('public', table)))
            rows = cur.fetchone()[0]
            statement = sql.SQL('COPY (SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text COLLATE "C") TO STDOUT').format(sql.Identifier('public', table))
            digest = hashlib.sha256()
            with cur.copy(statement) as copy:
                for chunk in copy:
                    digest.update(chunk)
            result[table] = {'rows': rows, 'sha256': digest.hexdigest()}
    return result


def activate_ops(root, patch, sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('FULL_COMMIT_SHA_REQUIRED')
    previous = (root / 'current').resolve(strict=True)
    destination = root / 'releases' / sha
    if previous == destination:
        return
    if destination.exists():
        raise ValueError('EXISTING_RELEASE_REQUIRES_REVIEW')
    destination.mkdir(mode=0o700)
    for name in ['lower', 'four']:
        (destination / name).symlink_to((previous / name).resolve(strict=True), target_is_directory=True)
    shutil.copytree(patch, destination / 'ops', ignore=shutil.ignore_patterns('__pycache__'))
    temporary = root / 'current.postgres-next'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(destination, target_is_directory=True)
    temporary.replace(root / 'current')
    atomic_json(root / 'state/postgres-ops-activation.json', {'release': sha, 'previous_release': previous.name})


def stage(root, pgroot):
    import psycopg
    from psycopg import sql
    config = json.loads((root / 'credentials.json').read_text())
    params = source_parameters(config['DATABASE_URL'])
    ensure_running(pgroot)
    # Connect before pausing. An exhausted source quota leaves runtime untouched.
    with psycopg.connect(**params, options='-c timezone=UTC -c datestyle=ISO') as source:
        source.read_only = True
        source.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with source.cursor() as cur:
            cur.execute('SET LOCAL statement_timeout = 600000')
            cur.execute('SELECT pg_export_snapshot()')
            snapshot = cur.fetchone()[0]
        paused = root / 'state/database-migration-paused.json'
        if paused.exists():
            raise ValueError('EXISTING_PAUSED_MIGRATION_REQUIRES_REVIEW')
        target = 'trendscanner_' + time.strftime('%Y%m%d', time.gmtime()) + '_' + uuid.uuid4().hex[:8]
        atomic_json(paused, {'phase': 'STAGING', 'target': target, 'started_at': int(time.time())})
        folder = private_dir(pgroot / 'migration' / target)
        dump = folder / 'source.dump'
        command([binary(pgroot, 'pg_dump'), '-Fc', '--no-owner', '--no-acl', '--schema=public',
                 '--snapshot=' + snapshot, '-f', dump], env=source_environment(pgroot, params), timeout=900)
        os.chmod(dump, 0o600)
        expected = manifest(source)
        # New, isolated destination only. Never overwrite any existing database.
        command([binary(pgroot, 'createdb'), '-T', 'template0', '-O', 'trendscanner', target], env=pg_env(pgroot), timeout=60)
        toc = command([binary(pgroot, 'pg_restore'), '--list', dump], env=pg_env(pgroot))
        # New databases already contain public; retain it rather than dropping it.
        filtered = '\n'.join(line for line in toc.splitlines() if not re.match(r'^\d+; \d+ \d+ SCHEMA - public ', line)) + '\n'
        (folder / 'restore.list').write_text(filtered)
        command([binary(pgroot, 'pg_restore'), '--exit-on-error', '--single-transaction', '--no-owner', '--no-acl',
                 '--use-list=' + str(folder / 'restore.list'), '--dbname=' + target, dump],
                env=pg_env(pgroot, target, admin=False), timeout=900)
        with psycopg.connect(local_url(pgroot, target), options='-c timezone=UTC -c datestyle=ISO') as local:
            local.read_only = True
            actual = manifest(local)
        if actual != expected:
            raise ValueError('RESTORED_DATABASE_HASH_MISMATCH')
        encrypted = pgroot / 'exports/migration-source.cms'
        backup = encrypt(pgroot, dump, encrypted)
        record = {'phase': 'STAGED', 'target': target, 'started_at': int(time.time()),
                  'source_manifest': expected, 'source_config_sha256': hashlib.sha256(config['DATABASE_URL'].encode()).hexdigest(),
                  'encrypted_source_backup': backup, 'source_snapshot': snapshot}
        atomic_json(root / 'state/postgres-migration.json', record)
        atomic_json(paused, {'phase': 'STAGED', 'target': target})
        dump.unlink()  # Only the just-created, verified source dump, now encrypted.
    print(json.dumps({'phase': 'STAGED', 'verified_tables': len(expected),
                      'encrypted_backup_sha256': backup['sha256'], 'encrypted_backup_bytes': backup['size_bytes']}))


def apply(root, pgroot, artifact_id):
    import psycopg
    if not re.fullmatch('[0-9]+', artifact_id or ''):
        raise ValueError('OFFSITE_ENCRYPTED_ARTIFACT_ACK_REQUIRED')
    path = root / 'state/postgres-migration.json'
    record = json.loads(path.read_text())
    if record['phase'] != 'STAGED' or not (root / 'state/database-migration-paused.json').exists():
        raise ValueError('VERIFIED_PAUSED_STAGE_REQUIRED')
    config_path = root / 'credentials.json'
    config = json.loads(config_path.read_text())
    if hashlib.sha256(config['DATABASE_URL'].encode()).hexdigest() != record['source_config_sha256']:
        raise ValueError('DATABASE_CONFIGURATION_CHANGED')
    # Recheck source head after offsite upload. Unexpected concurrent writes abort.
    with psycopg.connect(**source_parameters(config['DATABASE_URL']), options='-c timezone=UTC -c datestyle=ISO') as source:
        source.read_only = True
        source.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        if manifest(source) != record['source_manifest']:
            raise ValueError('SOURCE_CHANGED_RESTAGE_REQUIRED')
    destination = local_url(pgroot, record['target'])
    with psycopg.connect(destination, options='-c timezone=UTC -c datestyle=ISO') as local:
        local.read_only = True
        if manifest(local) != record['source_manifest']:
            raise ValueError('TARGET_CHANGED_RESTAGE_REQUIRED')
    original = dict(config)
    atomic_json(root / 'state/database-source-before-migration.json', {'DATABASE_URL': config['DATABASE_URL']})
    config['DATABASE_URL'] = destination
    atomic_json(config_path, config)
    try:
        workspace = (root / 'current/lower').resolve(strict=True)
        code, output = run_command([str(workspace / '.venv/bin/python'), 'artifacts/trendscanner/bybit_demo_executor.py', '--preflight'],
                                   workspace, 'demo', config)
        if code:
            raise ValueError('READONLY_DEMO_PREFLIGHT_FAILED')
        # No orders/reconciliation are performed by migration code.
        record.update(phase='COMMITTED', completed_at=int(time.time()), offsite_artifact_id=artifact_id,
                      destination_database=record['target'])
        atomic_json(path, record)
    except Exception:
        atomic_json(config_path, original)
        raise
    (root / 'state/database-migration-paused.json').unlink()
    print('VERIFIED PRIVATE POSTGRES CUTOVER: PASS')


def abort(root, pgroot):
    paused = root / 'state/database-migration-paused.json'
    path = root / 'state/postgres-migration.json'
    record = json.loads(path.read_text()) if path.exists() else {}
    if not paused.exists():
        return
    config_path = root / 'credentials.json'
    config = json.loads(config_path.read_text())
    if record.get('phase') == 'COMMITTED':
        validate_local_url(pgroot, config['DATABASE_URL'])
    elif config['DATABASE_URL'].startswith('postgresql:///'):
        # Recover a child/job crash between the config switch and preflight.
        original = json.loads((root / 'state/database-source-before-migration.json').read_text())['DATABASE_URL']
        if hashlib.sha256(original.encode()).hexdigest() != record.get('source_config_sha256'):
            raise ValueError('SOURCE_ROLLBACK_CONFIGURATION_MISMATCH')
        config['DATABASE_URL'] = original
        atomic_json(config_path, config)
    paused.unlink()
    print('MIGRATION PAUSE RELEASED: PASS')


def acquire_cycle_lock(lock, timeout=600):
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise ValueError('RUNTIME_STILL_BUSY_RETRY_LATER')
            if not announced:
                print('WAITING FOR CURRENT RUNTIME CYCLE', flush=True)
                announced = True
            time.sleep(0.5)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['activate-ops', 'stage', 'apply', 'abort'])
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-runtime')
    parser.add_argument('--pg-root', type=Path, default=Path.home() / 'trendscanner-postgres')
    parser.add_argument('--sha')
    parser.add_argument('--artifact-id')
    args = parser.parse_args()
    try:
        if args.operation == 'abort' and not (args.root / 'state/database-migration-paused.json').exists():
            print('NO MIGRATION PAUSE TO RELEASE')
            return
        with (args.root / 'state/cycle.lock').open('a') as lock:
            acquire_cycle_lock(lock)
            if args.operation == 'activate-ops':
                activate_ops(args.root, Path(__file__).parent, args.sha)
                print('POSTGRES OPS ACTIVATED: PASS')
            elif args.operation == 'stage':
                stage(args.root, args.pg_root)
            elif args.operation == 'apply':
                apply(args.root, args.pg_root, args.artifact_id)
            else:
                abort(args.root, args.pg_root)
    except Exception as exc:
        # Do not leak connection strings, SQL row values or raw private dumps.
        label = str(exc) if isinstance(exc, (ValueError, BlockingIOError)) else type(exc).__name__
        if not re.fullmatch('[A-Z0-9_ :]+', label):
            label = type(exc).__name__
        raise SystemExit('POSTGRES MIGRATION BLOCKED: ' + label)


if __name__ == '__main__':
    main()
