"""Share one API prefetch result between oTree processes on the same host."""

import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile


CACHE_ROOT = Path(tempfile.gettempdir()) / f'dynamic_bottleneck_prefetch_uid{os.getuid()}'


def _paths(key):
    CACHE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = sha256(json.dumps(key, separators=(',', ':')).encode('utf-8')).hexdigest()
    return CACHE_ROOT / f'{digest}.lock', CACHE_ROOT / f'{digest}.json'


def try_claim(key):
    lock_path, _result_path = _paths(key)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    lock_file = os.fdopen(descriptor, 'r+')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def release_claim(lock_file):
    if lock_file is not None and not lock_file.closed:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def was_started(lock_file):
    lock_file.seek(0)
    return bool(lock_file.read().strip())


def mark_started(lock_file):
    """Durably record the attempt before any external API call is submitted."""
    lock_file.seek(0)
    lock_file.write('started\n')
    lock_file.truncate()
    lock_file.flush()
    os.fsync(lock_file.fileno())


def read_result(key):
    try:
        _lock_path, result_path = _paths(key)
        with result_path.open(encoding='utf-8') as result_file:
            result = json.load(result_file)
            return result if isinstance(result, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def publish_result(key, payload, lock_file):
    """Publish atomically before allowing another process to claim the call."""
    temporary_path = None
    try:
        _lock_path, result_path = _paths(key)
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=CACHE_ROOT,
            prefix=f'{result_path.stem}.', suffix='.tmp', delete=False,
        ) as result_file:
            temporary_path = Path(result_file.name)
            json.dump(payload, result_file, ensure_ascii=False)
            result_file.flush()
            os.fsync(result_file.fileno())
        os.replace(temporary_path, result_path)
    finally:
        try:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        finally:
            release_claim(lock_file)
