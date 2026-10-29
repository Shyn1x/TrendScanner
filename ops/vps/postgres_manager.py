"""Private, user-owned PostgreSQL 18. No root access, TCP listener or source writes."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from runtime import atomic_json
from postgres_host import inspect

PORT = 55432
MAX_EXPORT_BYTES = 20 * 1024 * 1024
PGDG_KEY = 'https://www.postgresql.org/media/keys/ACCC4CF8.asc'


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def command(args, *, env=None, cwd=None, timeout=120, input=None):
    # Never print argv/env/stdout on failure: subsequent operations can carry DSNs.
    result = subprocess.run([str(a) for a in args], env=env, cwd=cwd, input=input,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        if Path(str(args[0])).name in ('apt-get', 'dpkg-deb'):
            print(result.stderr[-3000:])  # Vendor package diagnostics contain no credentials.
        raise RuntimeError('COMMAND_FAILED:' + Path(str(args[0])).name + ':' + str(result.returncode))
    return result.stdout


def pg_env(root, database='postgres', *, admin=True):
    env = {'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
           'LD_LIBRARY_PATH': str(root / 'packages/usr/lib/x86_64-linux-gnu'),
           'PGHOST': str(root / 'socket'), 'PGPORT': str(PORT), 'PGDATABASE': database,
           'PGUSER': pwd.getpwuid(os.getuid()).pw_name if admin else 'trendscanner',
           'PGCONNECT_TIMEOUT': '10', 'PGOPTIONS': '-c timezone=UTC -c datestyle=ISO'}
    return env


def binary(root, name):
    return root / 'packages/usr/lib/postgresql/18/bin' / name


def local_url(root, database):
    if not re.fullmatch(r'trendscanner(?:_[a-z0-9_]+)?', database):
        raise ValueError('INVALID_LOCAL_DATABASE_NAME')
    query = urllib.parse.urlencode({'host': str(root / 'socket'), 'port': PORT, 'user': 'trendscanner'})
    return 'postgresql:///' + database + '?' + query


def validate_local_url(root, url):
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qs(parsed.query)
    expected = {'host': [str(root / 'socket')], 'port': [str(PORT)], 'user': ['trendscanner']}
    if parsed.scheme != 'postgresql' or parsed.netloc or params != expected:
        raise ValueError('PRIVATE_LOCAL_SOCKET_REQUIRED')
    database = parsed.path.removeprefix('/')
    if local_url(root, database) != url:
        raise ValueError('CANONICAL_LOCAL_URL_REQUIRED')
    return database


def install_packages(root):
    names = ['postgres', 'pg_dump', 'pg_restore', 'psql', 'initdb', 'pg_ctl']
    if all(binary(root, name).exists() for name in names):
        for name in names:
            if not re.search(r'\b18\.', command([binary(root, name), '--version'], env=pg_env(root))):
                raise ValueError('POSTGRES_18_REQUIRED')
        return
    release = __import__('platform').freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_CODENAME') != 'noble' or __import__('platform').machine() != 'x86_64':
        raise ValueError('REVIEW_PACKAGES_FOR_THIS_OS')
    apt = private_dir(root / 'apt')
    for directory in ['lists/partial', 'archives/partial', 'sources']:
        private_dir(apt / directory)
    key = apt / 'pgdg.asc'
    with urllib.request.urlopen(PGDG_KEY, timeout=30) as response:
        if response.geturl() != PGDG_KEY:
            raise ValueError('KEY_REDIRECT_REJECTED')
        key.write_bytes(response.read(100000))
    if not key.read_text().startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        raise ValueError('INVALID_VENDOR_SIGNING_KEY')
    os.chmod(key, 0o600)
    # A private apt state/cache reads official signed Ubuntu + PGDG sources. No
    # source files, dpkg status, packages or services on the OS are modified.
    for source in Path('/etc/apt/sources.list.d').glob('*'):
        if source.suffix in ('.list', '.sources'):
            (apt / 'sources' / source.name).symlink_to(source) if not (apt / 'sources' / source.name).exists() else None
    (apt / 'sources/pgdg.list').write_text('deb [signed-by=' + str(key) + '] https://apt.postgresql.org/pub/repos/apt noble-pgdg main\n')
    options = ['-o', 'Dir::State::lists=' + str(apt / 'lists'),
               '-o', 'Dir::Cache::archives=' + str(apt / 'archives'),
               '-o', 'Dir::Cache::pkgcache=' + str(apt / 'pkgcache.bin'),
               '-o', 'Dir::Cache::srcpkgcache=' + str(apt / 'srcpkgcache.bin'),
               '-o', 'Dir::State::extended_states=' + str(apt / 'extended_states'),
               '-o', 'Dir::Etc::sourceparts=' + str(apt / 'sources'),
               '-o', 'Dir::Etc::sourcelist=/etc/apt/sources.list',
               '-o', 'Debug::NoLocking=1', '-o', 'APT::Sandbox::User=' + pwd.getpwuid(os.getuid()).pw_name]
    command(['apt-get', *options, 'update'], timeout=600)
    command(['apt-get', *options, '--download-only', '--no-install-recommends', '-y',
             'install', 'postgresql-18', 'postgresql-client-18', 'libpq5'], timeout=600)
    packages = private_dir(root / 'packages')
    archives = sorted((apt / 'archives').glob('*.deb'))
    if not archives:
        raise ValueError('NO_VERIFIED_PACKAGES_DOWNLOADED')
    for archive in archives:
        command(['dpkg-deb', '--extract', archive, packages], timeout=120)
    for name in ['postgres', 'pg_dump', 'pg_restore', 'psql', 'initdb', 'pg_ctl']:
        command([binary(root, name), '--version'], env=pg_env(root))
    atomic_json(root / 'package-manifest.json', {'prepared_at': int(time.time()),
                'source': 'Signed official PostgreSQL PGDG + Ubuntu apt repositories',
                'archives': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in archives}})


def ensure_running(root):
    data = root / 'data'
    if not (data / 'PG_VERSION').exists():
        raise ValueError('PREPARE_REQUIRED')
    if (data / 'PG_VERSION').read_text().strip() != '18':
        raise ValueError('POSTGRES_18_REQUIRED')
    result = subprocess.run([str(binary(root, 'pg_ctl')), '-D', str(data), 'status'],
                            env=pg_env(root), capture_output=True, text=True, timeout=15)
    if result.returncode == 3:
        command([binary(root, 'pg_ctl'), '-D', data, '-l', root / 'logs/postgres.log',
                 '-w', '-t', '60', 'start'], env=pg_env(root), timeout=90)
    elif result.returncode:
        raise ValueError('POSTGRES_STATUS_UNAVAILABLE')
    value = command([binary(root, 'psql'), '-X', '-At', '-c',
                     "SELECT current_setting('listen_addresses'), current_setting('data_directory')"], env=pg_env(root))
    if value.strip() != '|' + str(data):
        raise ValueError('POSTGRES_IS_NOT_PRIVATE_CLUSTER')


def install_cron(root):
    if any(c in str(root) for c in '\n\r%"\\ '):
        raise ValueError('UNSUPPORTED_PRIVATE_PATH')
    previous = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if previous.returncode not in (0, 1) or previous.returncode == 1 and 'no crontab' not in previous.stderr.lower():
        raise ValueError('CRONTAB_UNAVAILABLE')
    original = previous.stdout if previous.returncode == 0 else ''
    marker = '# TrendScanner private PostgreSQL\n'
    line = ('* * * * * umask 077; /usr/bin/python3 ' + str(root / 'ops/postgres_manager.py') +
            ' ensure --root ' + str(root) + ' >> ' + str(root / 'logs/supervisor.log') + ' 2>&1')
    backup = ('17 1 * * * umask 077; /usr/bin/python3 ' + str(root / 'ops/postgres_manager.py') +
              ' backup --root ' + str(root) + ' >> ' + str(root / 'logs/backup.log') + ' 2>&1')
    if marker in original:
        if line not in original or backup not in original:
            raise ValueError('EXISTING_POSTGRES_CRON_DIFFERS')
        return
    atomic_json(root / 'crontab-before.json', {'crontab': original})
    content = original + ('\n' if original and not original.endswith('\n') else '') + marker + line + '\n' + backup + '\n'
    command(['crontab', '-'], input=content)
    if line not in command(['crontab', '-l']):
        command(['crontab', '-'], input=original)
        raise ValueError('CRONTAB_VERIFICATION_FAILED')


def prepare(root, source_ops):
    if inspect(Path.home())['status'] != 'READY_TO_PREPARE':
        raise ValueError('INSUFFICIENT_CAPACITY')
    private_dir(root)
    for directory in ['socket', 'logs', 'backups', 'exports', 'ops']:
        private_dir(root / directory)
    certificate = source_ops / 'backup-recipient.pem'
    destination = root / 'ops/backup-recipient.pem'
    if destination.exists() and destination.read_bytes() != certificate.read_bytes():
        raise ValueError('BACKUP_RECOVERY_KEY_CHANGE_REQUIRES_REVIEW')
    for name in ['postgres_host.py', 'postgres_manager.py', 'runtime.py', 'backup-recipient.pem']:
        shutil.copy2(source_ops / name, root / 'ops' / name)
        os.chmod(root / 'ops' / name, 0o600)
    install_packages(root)
    data = root / 'data'
    user = pwd.getpwuid(os.getuid()).pw_name
    if not re.fullmatch('[a-z_][a-z0-9_-]*', user):
        raise ValueError('UNSUPPORTED_LOCAL_USER')
    if not (data / 'PG_VERSION').exists():
        command([binary(root, 'initdb'), '-D', data, '-L', root / 'packages/usr/share/postgresql/18',
                 '--encoding=UTF8', '--locale=C.UTF-8', '--auth-local=peer', '--auth-host=reject', '--data-checksums'],
                env=pg_env(root), timeout=120)
        config = """listen_addresses = ''
port = 55432
unix_socket_permissions = 0700
shared_buffers = '48MB'
effective_cache_size = '256MB'
work_mem = '2MB'
maintenance_work_mem = '32MB'
max_connections = 16
max_worker_processes = 2
max_parallel_workers = 1
max_parallel_workers_per_gather = 0
max_wal_size = '256MB'
min_wal_size = '64MB'
jit = off
fsync = on
full_page_writes = on
log_min_messages = warning
""" + "unix_socket_directories = '" + str(root / 'socket') + "'\n"
        (data / 'postgresql.conf').write_text(config)
        (data / 'pg_hba.conf').write_text('local all ' + user + ' peer\nlocal all trendscanner peer map=trendscanner\nlocal all all reject\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n')
        (data / 'pg_ident.conf').write_text('trendscanner ' + user + ' trendscanner\n')
    ensure_running(root)
    exists = command([binary(root, 'psql'), '-X', '-At', '-c', "SELECT count(*) FROM pg_roles WHERE rolname='trendscanner'"], env=pg_env(root)).strip()
    if exists == '0':
        command([binary(root, 'psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-c',
                 'CREATE ROLE trendscanner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'], env=pg_env(root))
    install_cron(root)
    atomic_json(root / 'prepared.json', {'prepared_at': int(time.time()), 'postgres_major': 18,
                'tcp_disabled': True, 'backups_encrypted': True,
                'certificate_sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
    print('PRIVATE POSTGRES PREPARED: PASS')


def encrypt(root, source, destination):
    temporary = destination.with_suffix('.pending')
    command(['openssl', 'cms', '-encrypt', '-binary', '-aes-256-gcm', '-outform', 'DER',
             '-in', source, '-out', temporary, '-recip', root / 'ops/backup-recipient.pem',
             '-keyopt', 'rsa_padding_mode:oaep', '-keyopt', 'rsa_oaep_md:sha256'], timeout=300)
    os.chmod(temporary, 0o600)
    if temporary.stat().st_size > MAX_EXPORT_BYTES:
        temporary.unlink()
        raise ValueError('ENCRYPTED_BACKUP_EXCEEDS_20_MIB_EXPORT_CAP')
    temporary.replace(destination)
    return {'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'size_bytes': destination.stat().st_size}


def backup(root):
    runtime = Path.home() / 'trendscanner-runtime'
    config = json.loads((runtime / 'credentials.json').read_text())
    try:
        database = validate_local_url(root, config['DATABASE_URL'])
    except ValueError:
        print('LOCAL POSTGRES NOT ACTIVE: BACKUP SKIP')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('ready=false\n')
        return
    ensure_running(root)
    with tempfile.TemporaryDirectory(dir=root / 'backups', prefix='dump-') as folder:
        dump = Path(folder) / 'database.dump'
        command([binary(root, 'pg_dump'), '-Fc', '--no-owner', '--no-acl', '--schema=public', '-f', dump],
                env=pg_env(root, database, admin=False), timeout=600)
        os.chmod(dump, 0o600)
        command([binary(root, 'pg_restore'), '--list', dump], env=pg_env(root), timeout=60)
        name = 'daily-' + str(int(time.time())) + '-' + __import__('uuid').uuid4().hex[:8] + '.cms'
        exported = root / 'exports' / name
        result = encrypt(root, dump, exported)
    result.update(completed_at=int(time.time()), database=database, file=name, funding_or_trade_data_logged=False)
    atomic_json(root / 'backups' / (name + '.json'), result)
    atomic_json(root / 'last-backup.json', result)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('ready=true\nfile=' + str(exported) + '\nsha256=' + result['sha256'] + '\n')
    print('ENCRYPTED LOCAL BACKUP: PASS')


def smoke_test(root):
    """Dump/restore only uniquely named disposable databases created by this call."""
    names = ['trendscanner_probe_' + __import__('uuid').uuid4().hex[:12] for _ in range(2)]
    created = []
    ensure_running(root)
    try:
        for name in names:
            command([binary(root, 'createdb'), '-T', 'template0', '-O', 'trendscanner', name], env=pg_env(root))
            created.append(name)
        statement = "CREATE TABLE proof(id integer PRIMARY KEY,amount numeric(20,8),payload jsonb); INSERT INTO proof VALUES (1,123.00001234,'{\"text\":\"проверка\"}'),(2,-3.14159265,'{\"stop\":true}');"
        command([binary(root, 'psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-c', statement], env=pg_env(root, names[0], admin=False))
        with tempfile.TemporaryDirectory(dir=root / 'backups', prefix='proof-') as folder:
            dump = Path(folder) / 'proof.dump'
            command([binary(root, 'pg_dump'), '-Fc', '--no-owner', '--no-acl', '-f', dump], env=pg_env(root, names[0], admin=False))
            toc = command([binary(root, 'pg_restore'), '--list', dump], env=pg_env(root))
            listing = Path(folder) / 'restore.list'
            listing.write_text('\n'.join(line for line in toc.splitlines() if not re.match(r'^\d+; \d+ \d+ SCHEMA - public ', line)) + '\n')
            command([binary(root, 'pg_restore'), '--exit-on-error', '--single-transaction', '--no-owner', '--no-acl',
                     '--use-list=' + str(listing), '--dbname=' + names[1], dump], env=pg_env(root, names[1], admin=False))
            values = [command([binary(root, 'psql'), '-X', '-At', '-c', 'SELECT row_to_json(p) FROM proof p ORDER BY id'], env=pg_env(root, name, admin=False)) for name in names]
            if values[0] != values[1]:
                raise ValueError('DUMP_RESTORE_SELF_TEST_FAILED')
            proof = encrypt(root, dump, root / 'exports/smoke-test.cms')
        print(json.dumps({'dump_restore_test': 'PASS', 'encrypted_test': proof, 'real_strategy_data_used': False}))
    finally:
        for name in created:
            command([binary(root, 'dropdb'), name], env=pg_env(root))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'ensure', 'backup', 'status', 'smoke-test', 'ack-backup'])
    parser.add_argument('--root', type=Path, default=Path.home() / 'trendscanner-postgres')
    parser.add_argument('--artifact-id')
    parser.add_argument('--file')
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if args.operation == 'status':
        print(json.dumps({'prepared': (args.root / 'prepared.json').exists(),
                          'backup_exists': (args.root / 'last-backup.json').exists()}))
        return
    private_dir(args.root)
    with (args.root / 'manager.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.operation == 'prepare':
            prepare(args.root, Path(__file__).parent)
        elif args.operation == 'ensure':
            ensure_running(args.root)
        elif args.operation == 'smoke-test':
            smoke_test(args.root)
        elif args.operation == 'ack-backup':
            if not re.fullmatch('[0-9]+', args.artifact_id or ''):
                raise ValueError('OFFSITE_ARTIFACT_ID_REQUIRED')
            path = Path(args.file or '').resolve()
            if path.parent != (args.root / 'exports').resolve() or not re.fullmatch(r'daily-\d+-[0-9a-f]{8}\.cms', path.name):
                raise ValueError('EXACT_ENCRYPTED_EXPORT_REQUIRED')
            record = json.loads((args.root / 'backups' / (path.name + '.json')).read_text())
            if record['sha256'] != args.sha256 or hashlib.sha256(path.read_bytes()).hexdigest() != args.sha256:
                raise ValueError('OFFSITE_EXPORT_ACK_MISMATCH')
            record.update(offsite_artifact_id=args.artifact_id, uploaded_at=int(time.time()))
            atomic_json(args.root / 'last-offsite-backup.json', record)
            print('ENCRYPTED OFFSITE BACKUP ACKNOWLEDGED: PASS')
        else:
            backup(args.root)


if __name__ == '__main__':
    main()
