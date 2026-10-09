"""Process-local plumbing for protocol 2; imports no oTree models."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from functools import lru_cache
from contextlib import closing, contextmanager

PROCESS_BOOT_ID = uuid.uuid4().hex
_lock = threading.RLock()
_journal = None
_identity = None
_file_identity = None
_active = False
_pending = {}
# Authorization metadata only. Choices, clocks and settlement remain in SQLite.
# Refuse further issuance at the bound rather than evicting a live capability.
_controls = {}
_MAX_CONTROLS = 32768
_faults = {}
_faults_to_persist = set()
_settlement_closed = set()


def get_journal():
    """Initialize once, on a business/control worker, and fail closed if replaced."""
    global _journal, _identity, _file_identity
    with _lock:
        if _journal is not None:
            _check_file()
            return _journal
        from .sync_journal import SyncJournal
        journal = SyncJournal(os.environ.get('DYNAMIC_ROOM_SYNC_DB', 'room_sync.sqlite3'))
        with closing(sqlite3.connect(journal.path, timeout=30)) as connection, connection:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS sync_runtime_metadata '
                               '(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            connection.execute('INSERT OR IGNORE INTO sync_runtime_metadata VALUES (?, ?)',
                               ('journal_id', uuid.uuid4().hex))
            identity = connection.execute('SELECT value FROM sync_runtime_metadata '
                                          'WHERE key=?', ('journal_id',)).fetchone()[0]
            if not re.fullmatch(r'[0-9a-f]{32}', identity):
                raise ValueError('invalid sync journal identity')
        journal.ensure_boot(PROCESS_BOOT_ID)
        stat = Path(journal.path).stat()
        _file_identity = (stat.st_dev, stat.st_ino)
        _identity, _journal = identity, journal
        return journal


def _check_file():
    try:
        stat = Path(_journal.path).stat()
    except OSError:
        raise RuntimeError('sync journal unavailable') from None
    if (stat.st_dev, stat.st_ino) != _file_identity:
        raise RuntimeError('sync journal replaced')


def journal_identity():
    get_journal()
    return _identity


def journal_initialized():
    """A nonblocking guard; ASGI initializes the journal on its worker pool."""
    return _journal is not None


@lru_cache(maxsize=1)
def _key():
    # settings is configuration only; never import otree.settings/models here.
    from settings import SECRET_KEY
    if not isinstance(SECRET_KEY, str) or not SECRET_KEY:
        raise ValueError('sync signing unavailable')
    return SECRET_KEY.encode('utf-8')


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) != {
            'session_code', 'round_number', 'participant_code', 'shell_token',
            'page_index', 'journal_id'}:
        raise ValueError('invalid control identity')
    for key in ('session_code', 'participant_code'):
        if not isinstance(payload[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', payload[key]):
            raise ValueError('invalid control identity')
    for key in ('round_number', 'page_index'):
        if type(payload[key]) is not int or not 0 <= payload[key] <= 2147483647:
            raise ValueError('invalid control identity')
    if payload['round_number'] < 1:
        raise ValueError('invalid control identity')
    for key, length in [('shell_token', 24), ('journal_id', 32)]:
        if not isinstance(payload[key], str) or not re.fullmatch('[0-9a-f]{%d}' % length, payload[key]):
            raise ValueError('invalid control identity')


def sign_control_token(session_code, round_number, participant_code, shell_token, page_index):
    payload = dict(session_code=session_code, round_number=round_number,
                   participant_code=participant_code, shell_token=shell_token,
                   page_index=page_index, journal_id=journal_identity())
    _validate(payload)
    journal = get_journal()
    with journal._connection() as connection:
        record, _ = journal._load(connection, session_code, round_number)
        registered = connection.execute('SELECT page_index FROM sync_shells WHERE session_code=? '
            'AND round_number=? AND participant_code=? AND shell_token=?',
            (session_code, round_number, participant_code, shell_token)).fetchone()
    if registered == (page_index,) and record['state']['decision_page_index'] == page_index:
        with _lock:
            identity = _control_identity(payload)
            if identity not in _controls and len(_controls) >= _MAX_CONTROLS:
                raise RuntimeError('control authorization capacity exhausted')
            _controls[identity] = frozenset(record['private']['allowed_minutes'])
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True,
                separators=(',', ':')).encode()).rstrip(b'=').decode()
    signature = hmac.new(_key(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + signature


def _control_identity(payload):
    return tuple(payload[key] for key in ('journal_id', 'session_code', 'round_number',
                 'participant_code', 'shell_token', 'page_index'))


def validate_control_request(payload, data):
    """Pure preflight, using only capabilities vetted at business token issuance."""
    with _lock:
        minutes = _controls.get(_control_identity(payload))
    if minutes is None:
        raise PermissionError('unregistered control identity')
    if data['kind'] == 'decision' and (type(data.get('departure_minute')) is not int
            or data['departure_minute'] not in minutes):
        raise ValueError('invalid departure minute')


def latch_control_fault(payload, reason):
    """Nonblocking safety interlock, never a replacement for persisted round facts."""
    key = payload['session_code'], payload['round_number']
    with _lock:
        if key not in _settlement_closed:
            if key not in _faults:
                _faults[key] = reason
                _faults_to_persist.add(key)


def control_fault(session_code, round_number):
    with _lock:
        return _faults.get((session_code, round_number), '')


def pending_fault_rounds():
    """Snapshot only; callers must release the registry lock before disk work."""
    with _lock:
        return tuple(_faults_to_persist)


def persist_control_fault(session_code, round_number):
    """Worker/business-thread only. Retain the interlock if storage is unavailable."""
    reason = control_fault(session_code, round_number)
    if reason:
        try:
            journal = get_journal()
            state = journal.state(session_code, round_number)
            if state['settled']:
                with _lock:
                    _faults.pop((session_code, round_number), None)
                    _faults_to_persist.discard((session_code, round_number))
                return ''
            journal.mark_anomaly(session_code, round_number, reason)
            with _lock:
                _faults_to_persist.discard((session_code, round_number))
        except Exception:
            pass
    return reason


def close_control_round(session_code, round_number):
    """Atomic final control boundary, called after successful native commit.

    No database work under this lock. Later replay failures cannot invalidate a
    round whose native commit and control drain already succeeded.
    """
    key = session_code, round_number
    with _lock:
        if key in _faults or any((s, n) == key for s, n, *_ in _pending.values()):
            return False
        _settlement_closed.add(key)
        return True


@contextmanager
def control_body_boundary():
    """Linearize pure complete-body admission with native settlement closure.

    Callers must do no SQLite, ORM, awaits, or executor work inside this lock.
    """
    with _lock:
        yield


def fail_control_settlement(session_code, round_number):
    with _lock:
        key = session_code, round_number
        _settlement_closed.discard(key)
        _faults.setdefault(key, 'control_settlement_failure')
        _faults_to_persist.add(key)
    persist_control_fault(session_code, round_number)


def verify_control_token(token):
    try:
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError
        encoded, signature = token.split('.')
        expected = hmac.new(_key(), encoded.encode('ascii'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        payload = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
        _validate(payload)
        # Verification is deliberately pure: no file check, lock or SQLite on
        # the control loop. The persistence worker checks the backing file.
        if _identity is None or payload['journal_id'] != _identity:
            raise ValueError
        return payload
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('invalid control token') from None


def control_transport_active():
    return _active


def set_control_transport_active(active):
    global _active
    _active = bool(active)


def register_pending(payload, kind, arrival):
    ticket = object()
    with _lock:
        _pending[ticket] = (payload['session_code'], payload['round_number'],
                            payload['participant_code'], kind, arrival)
    return ticket


def finish_pending(ticket):
    with _lock:
        _pending.pop(ticket, None)


def pending_control_codes(session_code, round_number, *, kind, before_ts=float('inf')):
    with _lock:
        return tuple(sorted({code for session, number, code, request_kind, timestamp
            in _pending.values() if session == session_code and number == round_number
            and request_kind == kind and timestamp < before_ts}))


def reset_runtime():
    """Lifecycle reset after workers have drained; also supports disposable tests."""
    global _journal, _identity, _file_identity, _active
    with _lock:
        if _pending:
            raise RuntimeError('control persistence still running')
        _journal = _identity = _file_identity = None
        _active = False
        _controls.clear()
        _faults.clear()
        _faults_to_persist.clear()
        _settlement_closed.clear()
