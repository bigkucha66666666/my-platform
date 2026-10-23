"""Standard-library-only validation, run before importing oTree/settings."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class Instance:
    config_path: Path
    instance_id: str
    mode: str
    port: int
    runtime_dir: Path
    state_dir: Path
    tmp_dir: Path
    journal_path: Path
    database_url: str
    secret_key: str
    rest_key: str
    admin_password: str
    environment: dict

    @property
    def condition(self):
        return self.instance_id.upper()

    @property
    def room_name(self):
        return f'prod_room_{self.instance_id}'


def _absolute(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError('instance paths must be absolute')
    return Path(value).resolve()


def _within(path, directory):
    return path == directory or directory in path.parents


def _database_target(url, mode, runtime):
    if not isinstance(url, str):
        raise ValueError('explicit database_url required')
    if mode == 'local-test' and url.startswith('sqlite:////'):
        # oTree's SQLite creator uses cwd/db.sqlite3, regardless of the URL path.
        path = _absolute(url[len('sqlite:///'):])
        if path != runtime / 'db.sqlite3' or url != f'sqlite:///{path}':
            raise ValueError('local database must be runtime_dir/db.sqlite3')
        return ('sqlite', str(path))
    try:
        parsed = urlsplit(url)
        port = parsed.port or 5432
    except ValueError:
        raise ValueError('invalid database target') from None
    if (parsed.scheme not in {'postgres', 'postgresql', 'postgresql+psycopg2'}
            or not parsed.hostname or parsed.query or parsed.fragment
            or not parsed.path.strip('/') or '/' in parsed.path[1:]):
        raise ValueError('explicit PostgreSQL database URL without query overrides required')
    host = parsed.hostname.lower()
    if host in {'localhost', '127.0.0.1', '::1'}:
        host = 'loopback'
    return ('postgresql', host, port, unquote(parsed.path[1:]))


def load_pair(path):
    path = Path(path).resolve()
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('pair config must be a private file (chmod 600)')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if raw['version'] != 1 or raw['mode'] not in {'production', 'local-test'}:
            raise ValueError('invalid pair version/mode')
        if set(raw['instances']) != {'i0', 'i1'}:
            raise ValueError('pair must contain i0 and i1')
        pair, targets = {}, []
        for instance_id in ('i0', 'i1'):
            row = raw['instances'][instance_id]
            port = row['port']
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError('invalid instance port')
            paths = {key: _absolute(row[key]) for key in
                     ('runtime_dir', 'state_dir', 'tmp_dir', 'journal_path')}
            runtime, state, tmp, journal = paths.values()
            if _within(runtime, state) or _within(state, runtime):
                raise ValueError('runtime and state directories must be separate')
            if not _within(tmp, state) or tmp == state:
                raise ValueError('tmp_dir must be private under state_dir')
            if not _within(journal, state) or journal == state or _within(journal, tmp):
                raise ValueError('journal must be under state_dir and outside tmp_dir')
            for key in ('secret_key', 'rest_key', 'admin_password'):
                if not isinstance(row[key], str) or not row[key].strip():
                    raise ValueError(f'explicit {key} required')
            if min(len(row['secret_key']), len(row['rest_key'])) < 32:
                raise ValueError('secret_key/rest_key must be at least 32 characters')
            extra = row.get('environment', {})
            if not isinstance(extra, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) or '\x00' in value
                or not (key == 'OTREE_PROD_PARTICIPANT_PASSWORD' or key.startswith(
                    ('DEEPSEEK_AGENT_', 'DYNAMIC_BOTTLENECK_', 'DYNAMIC_ROOM_RELEASE_')))
                for key, value in extra.items()
            ):
                raise ValueError('unsupported instance environment override')
            if extra.get('DYNAMIC_ROOM_RELEASE_PROTOCOL') != '2':
                raise ValueError('independent instances require protocol 2')
            drain = float(extra.get('DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS', 'nan'))
            if not math.isfinite(drain) or not 0 < drain <= 120:
                raise ValueError('explicit measured release drain seconds required')
            targets.append(_database_target(row['database_url'], raw['mode'], runtime))
            pair[instance_id] = Instance(path, instance_id, raw['mode'], port, **paths,
                database_url=row['database_url'], secret_key=row['secret_key'],
                rest_key=row['rest_key'], admin_password=row['admin_password'], environment=dict(extra))
        a, b = pair.values()
        if targets[0] == targets[1]:
            raise ValueError('instances must use different database targets')
        if targets[0][0] == targets[1][0] == 'postgresql' and targets[0][-1] == targets[1][-1]:
            # Host aliases/proxies cannot reliably prove separate servers offline.
            # Conservatively require different decoded database names as well.
            raise ValueError('instances must use different PostgreSQL database names, including aliases')
        for field in ('port', 'secret_key', 'rest_key'):
            if getattr(a, field) == getattr(b, field):
                raise ValueError(f'instances must use different {field}')
        for left in (a.runtime_dir, a.state_dir):
            for right in (b.runtime_dir, b.state_dir):
                if _within(left, right) or _within(right, left):
                    raise ValueError('instance runtime/state paths overlap')
        files_a = [a.journal_path] + ([a.runtime_dir / 'db.sqlite3'] if targets[0][0] == 'sqlite' else [])
        files_b = [b.journal_path] + ([b.runtime_dir / 'db.sqlite3'] if targets[1][0] == 'sqlite' else [])
        if any(x == y or (x.exists() and y.exists() and x.samefile(y))
               for x in files_a for y in files_b):
            raise ValueError('instances must use different state/database files')
        return pair
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError('incomplete or invalid instance pair configuration') from None


def build_environment(pair, instance_id, *, inherited=None):
    instance = pair[instance_id]
    inherited = os.environ if inherited is None else inherited
    # Never inherit DB/API credentials, Python import paths, or timeout secrets.
    env = {key: value for key, value in inherited.items()
           if key in {'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TZ',
                      'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}}
    env.update(instance.environment)
    env.update(OTREE_INSTANCE_CONFIG=str(instance.config_path), OTREE_INSTANCE_ID=instance_id,
        DATABASE_URL=instance.database_url, DYNAMIC_ROOM_SYNC_DB=str(instance.journal_path),
        TMPDIR=str(instance.tmp_dir), TMP=str(instance.tmp_dir), TEMP=str(instance.tmp_dir),
        OTREE_SECRET_KEY=instance.secret_key, OTREE_REST_KEY=instance.rest_key,
        OTREE_ADMIN_PASSWORD=instance.admin_password, OTREE_PRODUCTION='1',
        OTREE_AUTH_LEVEL='STUDY', PYTHONDONTWRITEBYTECODE='1')
    return env


def validate_environment(env=None, *, cwd=None):
    env = os.environ if env is None else env
    instance_id = env.get('OTREE_INSTANCE_ID')
    if instance_id not in {'i0', 'i1'} or not env.get('OTREE_INSTANCE_CONFIG'):
        raise ValueError('instance ID and pair config are required')
    pair = load_pair(env['OTREE_INSTANCE_CONFIG'])
    instance = pair[instance_id]
    expected = build_environment(pair, instance_id, inherited={})
    if any(env.get(key) != value for key, value in expected.items()):
        raise ValueError('instance environment differs from explicit pair configuration')
    if env.get('OTREE_IN_MEMORY') or Path(cwd or Path.cwd()).resolve() != instance.runtime_dir:
        raise ValueError('instance must use its own runtime working directory/database')
    return instance


def _runtime_files(source):
    for entry in sorted(source.iterdir()):
        if entry.name.startswith('.'):
            continue
        if entry.is_symlink():
            raise ValueError('runtime source symlinks are not allowed')
        if entry.is_file() and (entry.suffix == '.py' or entry.name in {'requirements.txt', 'requirements-lock.txt'}):
            yield entry
        elif entry.is_dir() and (entry.name in {'_static', '_templates', '_rooms'}
                                 or (entry / '__init__.py').is_file()):
            for directory, dirs, names in os.walk(entry, followlinks=False):
                if any((Path(directory) / name).is_symlink() for name in dirs):
                    raise ValueError('runtime source directory symlinks are not allowed')
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__')
                for name in sorted(names):
                    path = Path(directory) / name
                    if (not name.startswith('.') and path.suffix not in
                            {'.env', '.sqlite3', '.db', '.pyc', '.pyo'}):
                        yield path


def prepare_pair(pair, source):
    source = Path(source).resolve()
    for instance in pair.values():
        if _within(instance.runtime_dir, source) or _within(source, instance.runtime_dir):
            raise ValueError('runtime copies must be outside source tree')
        if instance.runtime_dir.exists():
            raise FileExistsError('runtime already exists; use a new release directory')
    files = list(_runtime_files(source))
    if any(path.is_symlink() for path in files):
        raise ValueError('runtime source symlinks are not allowed')
    hashes = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    created = []
    try:
        for instance in pair.values():
            instance.runtime_dir.mkdir(parents=True, mode=0o700)
            created.append(instance.runtime_dir)
            for path in files:
                target = instance.runtime_dir / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            (instance.runtime_dir / 'instance-manifest.json').write_text(
                json.dumps({'version': 1, 'files': hashes}, indent=2), encoding='utf-8')
            for directory in (instance.state_dir, instance.tmp_dir):
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                if stat.S_IMODE(directory.stat().st_mode) & 0o077:
                    raise ValueError('state/tmp directories must be private (chmod 700)')
    except BaseException:
        for directory in created:
            shutil.rmtree(directory)
        raise


def verify_runtime(instance):
    runtime = instance.runtime_dir
    try:
        manifest = json.loads((runtime / 'instance-manifest.json').read_text())
        files = manifest['files']
        if manifest['version'] != 1 or not files:
            raise ValueError()
        actual_names = {str(path.relative_to(runtime)) for path in _runtime_files(runtime)}
        if actual_names != set(files):
            raise ValueError()
        for directory in (runtime, instance.state_dir, instance.tmp_dir):
            if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
                raise ValueError()
        for name, digest in files.items():
            path = runtime / name
            if (not _within(path.resolve(), runtime) or path.is_symlink()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                raise ValueError()
        if any(runtime.glob('.env*')):
            raise ValueError()
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError('runtime manifest verification failed; prepare a new release') from None
    return files


def acquire_instance_lock(instance):
    """Keep one owning web process, including across exec; timeout child closes it."""
    import fcntl
    descriptor = os.open(instance.state_dir / 'web-process.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(descriptor, True)
    except BlockingIOError:
        os.close(descriptor)
        raise ValueError(f'{instance.instance_id} is already running') from None
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def validate_session_condition(config, groups, *, condition=None):
    condition = os.environ.get('OTREE_INSTANCE_ID', '').upper() if condition is None else condition
    if not condition:
        return
    if condition not in {'I0', 'I1'} or config.get('capacity_information_condition') != condition:
        raise ValueError(f'本实例只允许 {condition} 信息条件。')
    if any(row['information_condition'] != condition for row in groups.values()):
        raise ValueError(f'本实例所有实验组必须使用 {condition} 信息条件。')
