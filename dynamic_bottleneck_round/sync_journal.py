"""Small durable release journal; no oTree objects or database are involved.

Each operation owns and closes its connection. SQLite serializes writers across
threads/processes; receipts and their accompanying state commit together.
"""
from contextlib import contextmanager
from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3

from .room_release import (
    acknowledge_shell, admission_kind, expire_preparation, make_round_state,
    mark_shell_rendered, release_if_ready,
)


SCHEMA_VERSION = 1
SYNC_PROTOCOL_VERSION = 2


class SyncRequestRejected(ValueError):
    """A validated client/admission rejection, distinct from storage failures."""


class SyncJournal:
    def __init__(self, path):
        raw_path = str(path)
        if not raw_path or raw_path == ':memory:' or raw_path.startswith('file:'):
            raise ValueError('journal requires a persistent file path')
        self.path = str(Path(path).absolute())
        with self._connection() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError('incompatible sync journal schema version')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('BEGIN IMMEDIATE')
            # Check again under the writer lock for concurrent initialization.
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if version == 0:
                if tables:
                    raise ValueError('refusing to initialize an unrelated database')
                connection.execute('CREATE TABLE sync_rounds ('
                    'session_code TEXT NOT NULL, round_number INTEGER NOT NULL, '
                    'record TEXT NOT NULL, PRIMARY KEY (session_code, round_number))')
                connection.execute('CREATE TABLE sync_shells ('
                    'shell_token TEXT NOT NULL, session_code TEXT NOT NULL, '
                    'round_number INTEGER NOT NULL, participant_code TEXT NOT NULL, '
                    'page_index INTEGER NOT NULL, '
                    'PRIMARY KEY (session_code, round_number, participant_code, shell_token))')
                connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            elif version != SCHEMA_VERSION or not {'sync_rounds', 'sync_shells'} <= tables:
                raise ValueError('incompatible sync journal schema')

    @contextmanager
    def _connection(self, *, write=False):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute('PRAGMA synchronous=FULL')
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _encode(record):
        return json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)

    def _load(self, connection, session_code, round_number):
        row = connection.execute('SELECT record FROM sync_rounds '
            'WHERE session_code=? AND round_number=?', (session_code, round_number)).fetchone()
        if row is None:
            raise ValueError('unknown sync round')
        return json.loads(row[0]), row[0]

    def _save(self, connection, session_code, round_number, record, original):
        encoded = self._encode(record)
        if encoded != original:
            connection.execute('UPDATE sync_rounds SET record=? '
                'WHERE session_code=? AND round_number=?', (encoded, session_code, round_number))

    @staticmethod
    def _anomaly(record, reason):
        if record['state']['settled'] or record['state']['technical_anomaly']:
            return
        record['state']['technical_anomaly'] = str(reason)
        record['state']['phase'] = 'TECHNICAL_ANOMALY'

    def ensure_round(self, session_code, round_number, *, expected_codes, now_ts,
                     decision_page_index, drain_limit_seconds, information_condition,
                     allowed_minutes, actual_capacity_display=None,
                     capacity_level_label=None, boot_id=None):
        with self._connection(write=True) as connection:
            row = connection.execute('SELECT record FROM sync_rounds '
                'WHERE session_code=? AND round_number=?', (session_code, round_number)).fetchone()
            if row is None:
                state = make_round_state(round_number=round_number, now_ts=now_ts,
                                         expected_codes=expected_codes)
                state.update(protocol=SYNC_PROTOCOL_VERSION,
                             decision_page_index=decision_page_index,
                             drain_limit_seconds=drain_limit_seconds,
                             process_boot_id=boot_id, settled=False)
                record = {'state': state, 'private': {
                    'information_condition': information_condition,
                    'allowed_minutes': list(allowed_minutes),
                    'actual_capacity_display': actual_capacity_display,
                    'capacity_level_label': capacity_level_label}, 'participants': {}}
                connection.execute('INSERT INTO sync_rounds VALUES (?, ?, ?)',
                    (session_code, round_number, self._encode(record)))
            else:
                record, original = json.loads(row[0]), row[0]
                old_boot = record['state'].get('process_boot_id')
                if old_boot and boot_id and old_boot != boot_id:
                    self._anomaly(record, 'worker_restarted_during_round')
                self._save(connection, session_code, round_number, record, original)
            return deepcopy(record['state'])

    def ensure_boot(self, boot_id):
        if not boot_id:
            raise ValueError('boot_id must be nonempty')
        with self._connection(write=True) as connection:
            rows = connection.execute('SELECT session_code, round_number, record FROM sync_rounds').fetchall()
            for session_code, round_number, original in rows:
                record = json.loads(original)
                old_boot = record['state'].get('process_boot_id')
                if old_boot and old_boot != boot_id:
                    self._anomaly(record, 'worker_restarted_during_round')
                    self._save(connection, session_code, round_number, record, original)

    @staticmethod
    def _participant(record, code):
        return record['participants'].setdefault(code, {
            'operative_ts': None, 'receipt': None, 'shell_token': None})

    def register_shell(self, session_code, round_number, participant_code,
                       shell_token, *, now_ts, ingress_ts=None, page_index=None):
        if not isinstance(shell_token, str) or not re.fullmatch(r'[0-9a-f]{24}', shell_token):
            raise ValueError('invalid shell token')
        with self._connection(write=True) as connection:
            record, original = self._load(connection, session_code, round_number)
            state = record['state']
            if participant_code not in state['expected_codes']:
                raise ValueError('unknown participant')
            if page_index is None:
                page_index = state['decision_page_index']
            if page_index != state['decision_page_index']:
                raise ValueError('shell belongs to a different page')
            identity = (session_code, round_number, participant_code, page_index)
            row = connection.execute('SELECT session_code, round_number, participant_code, page_index '
                'FROM sync_shells WHERE shell_token=? AND session_code=? AND round_number=? '
                'AND participant_code=?', (shell_token, session_code, round_number, participant_code)).fetchone()
            if row is not None and row != identity:
                raise ValueError('shell token already belongs to another identity')
            if row is None:
                connection.execute('INSERT INTO sync_shells VALUES (?, ?, ?, ?, ?)',
                                   (shell_token, *identity))
            if not state['settled']:
                mark_shell_rendered(state, participant_code, now_ts=now_ts, ingress_ts=ingress_ts)
                participant = self._participant(record, participant_code)
                if participant['shell_token'] is None:
                    participant['shell_token'] = shell_token
            self._save(connection, session_code, round_number, record, original)
            return deepcopy(state)

    def _advance(self, record, now_ts, pending_codes=(), failed_codes=(), crossed_codes=(),
                 *, ack_processing_overrun=False):
        state = record['state']
        if state['settled'] or state['technical_anomaly']:
            return
        if state['release_ts'] is None:
            expected = set(state['expected_codes'])
            missing = expected - set(state['ack_ts_by_code'])
            if expected & set(failed_codes):
                self._anomaly(record, 'participant_page_http_failure')
            elif ack_processing_overrun:
                self._anomaly(record, 'shell_ack_queue_overrun')
            elif not release_if_ready(state, now_ts=now_ts):
                if now_ts >= state['prepare_deadline_ts'] and missing & set(crossed_codes):
                    self._anomaly(record, 'participant_page_crossed_prepare_deadline')
                else:
                    expire_preparation(state, now_ts=now_ts, pending_codes=pending_codes,
                                       drain_limit_seconds=state['drain_limit_seconds'])
        if not state['technical_anomaly'] and state['release_ts'] is not None:
            if now_ts >= state['deadline_ts']:
                state['phase'] = 'CLOSED'
            elif now_ts >= state['release_ts']:
                state['phase'] = 'RELEASED'

    def state(self, session_code, round_number, *, now_ts=None,
              pending_codes=(), failed_codes=(), crossed_codes=()):
        with self._connection(write=now_ts is not None) as connection:
            record, original = self._load(connection, session_code, round_number)
            if now_ts is not None:
                self._advance(record, float(now_ts), pending_codes, failed_codes, crossed_codes)
                self._save(connection, session_code, round_number, record, original)
            return deepcopy(record['state'])

    @staticmethod
    def _private_value(record, key, participant_code):
        value = record['private'][key]
        return value.get(participant_code) if isinstance(value, Mapping) else value

    @staticmethod
    def _snapshot(record, participant_code, now_ts):
        state = record['state']
        snapshot = {key: state[key] for key in (
            'phase', 'prepare_deadline_ts', 'release_ts', 'deadline_ts', 'technical_anomaly')}
        snapshot.update(acknowledged=participant_code in state['ack_ts_by_code'],
                        server_now_ts=now_ts)
        if (SyncJournal._private_value(record, 'information_condition', participant_code) == 'I1'
                and state['release_ts'] is not None and now_ts >= state['release_ts']
                and not state['technical_anomaly']):
            for key in ('actual_capacity_display', 'capacity_level_label'):
                snapshot[key] = SyncJournal._private_value(record, key, participant_code)
        return snapshot

    def exchange(self, session_code, round_number, participant_code, shell_token,
                 *, kind, now_ts, departure_minute=None, processing_ts=None,
                 pending_codes=(), failed_codes=(), crossed_codes=(),
                 _include_manual_overrun_limit=False):
        if kind not in {'ack', 'status', 'operative', 'decision'}:
            raise SyncRequestRejected('unknown exchange kind')
        arrival = float(now_ts)
        processing = arrival if processing_ts is None else float(processing_ts)
        rejection = None
        manual_overrun_limit = None
        with self._connection(write=True) as connection:
            record, original = self._load(connection, session_code, round_number)
            state = record['state']
            identity = connection.execute('SELECT session_code, round_number, participant_code, page_index '
                'FROM sync_shells WHERE shell_token=? AND session_code=? AND round_number=? '
                'AND participant_code=?', (shell_token, session_code, round_number, participant_code)).fetchone()
            if identity != (session_code, round_number, participant_code, state['decision_page_index']):
                raise SyncRequestRejected('unregistered shell or identity/page mismatch')
            if kind == 'decision' and (type(departure_minute) is not int or
                    departure_minute not in record['private']['allowed_minutes']):
                raise SyncRequestRejected('invalid departure minute')
            ack_processing_overrun = False
            if kind == 'ack' and not state['settled'] and not state['technical_anomaly']:
                first_ack = participant_code not in state['ack_ts_by_code']
                accepted = acknowledge_shell(state, participant_code, now_ts=arrival)
                if accepted and first_ack:
                    self._participant(record, participant_code)['shell_token'] = shell_token
                    ack_processing_overrun = (state['release_ts'] is None
                        and state['drain_limit_seconds'] is not None
                        and processing > state['prepare_deadline_ts'] + state['drain_limit_seconds'])
            self._advance(record, processing, pending_codes, failed_codes, crossed_codes,
                          ack_processing_overrun=ack_processing_overrun)
            if kind == 'operative' and not state['settled'] and admission_kind(
                    state, participant_code, arrival) == 'manual':
                participant = self._participant(record, participant_code)
                if participant['operative_ts'] is None:
                    participant['operative_ts'] = arrival
            receipt = record['participants'].get(participant_code, {}).get('receipt')
            admission = admission_kind(state, participant_code, arrival) if kind == 'decision' else None
            replace_late = (receipt is not None and receipt['kind'] == 'late'
                            and admission == 'manual' and not state['settled'])
            if kind == 'decision' and (receipt is None or replace_late):
                if state['settled']:
                    rejection = 'round already settled'
                elif admission not in {'manual', 'late', 'excluded'}:
                    rejection = 'decision rejected: ' + admission
                else:
                    # Excluded participants may move on, but never create a manual choice.
                    receipt = {'kind': admission,
                        'departure_minute': departure_minute if admission == 'manual' else None,
                        'ingress_ts': arrival}
                    self._participant(record, participant_code)['receipt'] = receipt
                    if (admission == 'manual' and not state['technical_anomaly']
                            and state['drain_limit_seconds'] is not None):
                        manual_overrun_limit = state['deadline_ts'] + state['drain_limit_seconds']
                        if processing > manual_overrun_limit:
                            self._anomaly(record, 'decision_control_queue_overrun')
            self._save(connection, session_code, round_number, record, original)
            snapshot = self._snapshot(record, participant_code, processing)
            if kind == 'decision' and rejection is None:
                snapshot.update(decision_received=True, decision_kind=receipt['kind'])
                snapshot['receipt'] = deepcopy(receipt)
        # Admission rejection must not roll back independently observed failures.
        if rejection is not None:
            raise SyncRequestRejected(rejection)
        if _include_manual_overrun_limit:
            return snapshot, manual_overrun_limit
        return snapshot

    def receipt(self, session_code, round_number, participant_code):
        with self._connection() as connection:
            record, _ = self._load(connection, session_code, round_number)
            return deepcopy(record['participants'].get(participant_code, {}).get('receipt'))

    def audit(self, session_code, round_number, participant_code):
        with self._connection() as connection:
            record, _ = self._load(connection, session_code, round_number)
            state = record['state']
            participant = record['participants'].get(participant_code, {})
            return {'shell_get_ingress_ts': state['shell_get_ingress_ts_by_code'].get(participant_code),
                'shell_rendered_ts': state['shell_rendered_ts_by_code'].get(participant_code),
                'ack_ts': state['ack_ts_by_code'].get(participant_code),
                'operative_ts': participant.get('operative_ts'),
                'shell_token': participant.get('shell_token'),
                'receipt': deepcopy(participant.get('receipt')),
                'page_index': state['decision_page_index']}

    def mark_anomaly(self, session_code, round_number, reason):
        if not reason:
            raise ValueError('anomaly reason must be nonempty')
        with self._connection(write=True) as connection:
            record, original = self._load(connection, session_code, round_number)
            self._anomaly(record, reason)
            self._save(connection, session_code, round_number, record, original)

    def mark_settled(self, session_code, round_number):
        with self._connection(write=True) as connection:
            record, original = self._load(connection, session_code, round_number)
            record['state']['settled'] = True
            self._save(connection, session_code, round_number, record, original)
