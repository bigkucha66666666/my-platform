"""Durable journal tests use only disposable SQLite files."""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

try:
    from dynamic_bottleneck_round.sync_journal import SyncJournal
except ModuleNotFoundError:
    SyncJournal = None


class SyncJournalTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sync.sqlite3'
        self.token = 'a' * 24
        self.journal = SyncJournal(self.path)

    def ensure(self, codes=('p',), session='s', round_number=1, **kwargs):
        return self.journal.ensure_round(
            session, round_number, expected_codes=codes, now_ts=100,
            decision_page_index=9, drain_limit_seconds=10,
            information_condition=kwargs.pop('information_condition', 'I0'),
            allowed_minutes=[474, 475], actual_capacity_display='3.14',
            capacity_level_label='high', boot_id=kwargs.pop('boot_id', 'boot-a'),
            **kwargs)

    def shell(self, code='p', token=None, session='s', round_number=1, **kwargs):
        return self.journal.register_shell(
            session, round_number, code, token or self.token,
            now_ts=kwargs.pop('now_ts', 101), page_index=9, **kwargs)

    def armed_journal(self, information_condition='I0'):
        self.ensure(information_condition=information_condition)
        self.shell()
        result = self.journal.exchange('s', 1, 'p', self.token,
                                       kind='ack', now_ts=102)
        self.release_ts = result['release_ts']
        return self.journal

    def test_received_before_deadline_survives_reopen(self):
        journal = self.armed_journal()
        result = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=self.release_ts + 59, departure_minute=474)
        self.assertTrue(result['decision_received'])
        reopened = SyncJournal(self.path)
        self.assertEqual(reopened.receipt('s', 1, 'p'),
                         dict(kind='manual', departure_minute=474, ingress_ts=166))

    def test_forged_server_admission_cannot_retain_anomaly_audit(self):
        journal = self.armed_journal()
        journal.mark_anomaly('s', 1, 'decision_control_queue_overrun')
        payload = dict(session_code='s', round_number=1, participant_code='p',
                       shell_token=self.token, page_index=9, journal_id='a' * 32)
        data = dict(kind='decision', departure_minute=474, admitted_before_fault=True)
        with self.assertRaises(ValueError):
            journal.retain_admitted_decision(payload, data, self.release_ts+1,
                                            admission_ticket=object())
        self.assertIsNone(journal.audit('s', 1, 'p').get('admitted_decision'))
        self.assertIsNone(journal.receipt('s', 1, 'p'))

    def test_novel_manual_processing_overrun_retains_receipt_and_faults(self):
        journal = self.armed_journal()
        result = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=self.release_ts + 59.99,
                                  processing_ts=self.release_ts + 71,
                                  departure_minute=474)
        self.assertEqual(result['receipt'], dict(kind='manual', departure_minute=474,
                                                 ingress_ts=self.release_ts + 59.99))
        self.assertEqual(journal.state('s', 1)['technical_anomaly'],
                         'decision_control_queue_overrun')

    def test_replayed_manual_and_fresh_late_do_not_fault_processing_overrun(self):
        journal = self.armed_journal()
        journal.exchange('s', 1, 'p', self.token, kind='decision',
                         now_ts=self.release_ts + 59, departure_minute=474)
        replay = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=self.release_ts + 59.5,
                                  processing_ts=self.release_ts + 71,
                                  departure_minute=475)
        self.assertEqual(replay['receipt']['departure_minute'], 474)
        self.assertFalse(journal.state('s', 1)['technical_anomaly'])
        self.ensure(session='late')
        self.shell(session='late')
        armed = journal.exchange('late', 1, 'p', self.token, kind='ack', now_ts=102)
        result = journal.exchange('late', 1, 'p', self.token, kind='decision',
                                  now_ts=armed['deadline_ts'] + .01,
                                  processing_ts=armed['deadline_ts'] + 11,
                                  departure_minute=474)
        self.assertEqual(result['receipt']['kind'], 'late')
        self.assertFalse(journal.state('late', 1)['technical_anomaly'])

    def test_sessions_and_40_or_80_clients_share_exactly_one_release(self):
        for session, size in [('s', 40), ('other', 80)]:
            codes = [f'p{i}' for i in range(size)]
            self.ensure(codes, session=session)
            for index, code in enumerate(codes):
                token = f'{index:024x}'
                self.shell(code, token, session=session)
                result = self.journal.exchange(session, 1, code, token,
                                               kind='ack', now_ts=102 + index)
                if index < size - 1:
                    self.assertIsNone(result['release_ts'])
            state = self.journal.state(session, 1)
            self.assertEqual(state['release_ts'], 106 + size)
            self.assertEqual(state['deadline_ts'] - state['release_ts'], 60)
        self.assertNotEqual(self.journal.state('s', 1)['release_ts'],
                            self.journal.state('other', 1)['release_ts'])

    def test_i0_never_and_i1_only_after_release_expose_capacity(self):
        for condition in ['I0', 'I1']:
            self.setUp()
            journal = self.armed_journal(condition)
            self.assertNotIn('3.14', json.dumps(journal.state('s', 1)))
            early = journal.exchange('s', 1, 'p', self.token,
                                     kind='status', now_ts=self.release_ts - .001)
            self.assertNotIn('actual_capacity_display', early)
            released = journal.exchange('s', 1, 'p', self.token,
                                        kind='status', now_ts=self.release_ts)
            self.assertEqual('actual_capacity_display' in released, condition == 'I1')
            if condition == 'I1':
                self.assertEqual(released['actual_capacity_display'], '3.14')
                self.assertEqual(released['capacity_level_label'], 'high')

    def test_boundary_early_rejected_start_accepted_and_deadline_late(self):
        journal = self.armed_journal()
        with self.assertRaises(ValueError):
            journal.exchange('s', 1, 'p', self.token, kind='decision',
                             now_ts=self.release_ts - .001, departure_minute=474)
        result = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=self.release_ts, departure_minute=474)
        self.assertEqual(result['decision_kind'], 'manual')
        self.ensure(session='late')
        self.shell(session='late')
        armed = journal.exchange('late', 1, 'p', self.token, kind='ack', now_ts=102)
        late = journal.exchange('late', 1, 'p', self.token, kind='decision',
                                now_ts=armed['deadline_ts'], departure_minute=474)
        self.assertTrue(late['decision_received'])
        self.assertEqual(late['decision_kind'], 'late')
        self.assertIsNone(journal.receipt('late', 1, 'p')['departure_minute'])

    def test_unacknowledged_decision_and_wrong_identity_round_or_page_rejected(self):
        self.ensure(('p', 'q'))
        self.shell()
        for code, round_number, token in [('p', 1, 'b' * 24), ('q', 1, self.token),
                                           ('p', 2, self.token)]:
            with self.assertRaises(ValueError):
                self.journal.exchange('s', round_number, code, token,
                                      kind='ack', now_ts=102)
        with self.assertRaises(ValueError):
            self.journal.register_shell('s', 1, 'q', 'b' * 24,
                                        now_ts=102, page_index=10)
        with self.assertRaises(ValueError):
            self.journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=110, departure_minute=474)
        self.assertIsNone(self.journal.receipt('s', 1, 'p'))

    def test_invalid_choices_raise_and_valid_replay_preserves_first_receipt(self):
        journal = self.armed_journal()
        for minute in [None, 476, '474', True, 474.0]:
            with self.assertRaises(ValueError):
                journal.exchange('s', 1, 'p', self.token, kind='decision',
                                 now_ts=self.release_ts, departure_minute=minute)
        first = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                 now_ts=self.release_ts, departure_minute=474)
        retry = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                 now_ts=self.release_ts + 100, departure_minute=475)
        self.assertTrue(first['decision_received'] and retry['decision_received'])
        self.assertEqual(retry['decision_kind'], 'manual')
        self.assertEqual(journal.receipt('s', 1, 'p')['departure_minute'], 474)

    def test_reload_token_does_not_reset_ack_or_release_or_first_shell_times(self):
        journal = self.armed_journal()
        self.shell(token='b' * 24, now_ts=104, ingress_ts=103)
        retry = journal.exchange('s', 1, 'p', 'b' * 24, kind='ack', now_ts=105)
        self.assertEqual(retry['release_ts'], self.release_ts)
        audit = journal.audit('s', 1, 'p')
        self.assertEqual(audit['ack_ts'], 102)
        self.assertEqual(audit['shell_rendered_ts'], 101)
        self.assertEqual(audit['shell_get_ingress_ts'], 101)

    def test_operative_records_only_first_effective_time(self):
        journal = self.armed_journal()
        journal.exchange('s', 1, 'p', self.token, kind='operative', now_ts=106)
        self.assertIsNone(journal.audit('s', 1, 'p')['operative_ts'])
        journal.exchange('s', 1, 'p', self.token, kind='operative', now_ts=107)
        journal.exchange('s', 1, 'p', self.token, kind='operative', now_ts=108)
        self.assertEqual(journal.audit('s', 1, 'p')['operative_ts'], 107)

    def test_absence_excludes_and_late_shell_can_advance_without_manual(self):
        self.ensure(('p', 'q'), round_number=4)
        self.shell(round_number=4)
        self.journal.exchange('s', 4, 'p', self.token, kind='ack', now_ts=102)
        state = self.journal.state('s', 4, now_ts=160)
        self.assertEqual(state['excluded_codes'], ['q'])
        self.shell('q', 'b' * 24, round_number=4, now_ts=161)
        result = self.journal.exchange('s', 4, 'q', 'b' * 24, kind='decision',
                                       now_ts=165, departure_minute=474)
        self.assertEqual(result['decision_kind'], 'excluded')
        self.assertIsNone(self.journal.receipt('s', 4, 'q')['departure_minute'])

    def test_preparation_failures_and_crossed_pages_are_anomalies(self):
        cases = [('failed_codes', 'participant_page_http_failure'),
                 ('crossed_codes', 'participant_page_crossed_prepare_deadline')]
        for field, reason in cases:
            self.ensure(('p', 'q'), session=field, round_number=4)
            state = self.journal.state(field, 4, now_ts=160, **{field: ['q']})
            self.assertEqual(state['technical_anomaly'], reason)
            self.assertEqual(state['phase'], 'TECHNICAL_ANOMALY')

    def test_rendered_missing_ack_is_anomaly_and_pending_drains(self):
        self.ensure(round_number=4)
        self.shell(round_number=4)
        state = self.journal.state('s', 4, now_ts=160)
        self.assertEqual(state['technical_anomaly'], 'shell_not_acknowledged_or_page_queued')
        self.ensure(session='pending', round_number=4)
        state = self.journal.state('pending', 4, now_ts=160, pending_codes=['p'])
        self.assertEqual(state['phase'], 'ACK_DRAINING')
        state = self.journal.state('pending', 4, now_ts=170.001, pending_codes=['p'])
        self.assertEqual(state['technical_anomaly'], 'shell_ack_queue_overrun')

    def test_delayed_ack_uses_arrival_for_admission_processing_for_release(self):
        self.ensure(round_number=4)
        self.shell(round_number=4)
        result = self.journal.exchange('s', 4, 'p', self.token, kind='ack',
                                       now_ts=159.9, processing_ts=162)
        self.assertEqual(result['release_ts'], 167)
        self.assertEqual(result['server_now_ts'], 162)
        self.assertEqual(self.journal.audit('s', 4, 'p')['ack_ts'], 159.9)
        self.ensure(session='overrun', round_number=4)
        self.shell(session='overrun', round_number=4)
        result = self.journal.exchange('overrun', 4, 'p', self.token, kind='ack',
                                       now_ts=159.9, processing_ts=170.001)
        self.assertEqual(result['technical_anomaly'], 'shell_ack_queue_overrun')
        self.assertIsNone(result['release_ts'])

    def test_queued_manual_uses_trusted_arrival_before_deadline(self):
        journal = self.armed_journal()
        result = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=166.99, processing_ts=200, departure_minute=474)
        self.assertEqual(result['decision_kind'], 'manual')
        self.assertEqual(journal.receipt('s', 1, 'p')['ingress_ts'], 166.99)

    def test_queued_on_time_manual_supersedes_late_receipt_before_settlement(self):
        journal = self.armed_journal()
        late = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                now_ts=168, departure_minute=475)
        self.assertEqual(late['decision_kind'], 'late')
        queued = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=166, processing_ts=169, departure_minute=474)
        self.assertEqual(queued['decision_kind'], 'manual')
        self.assertEqual(journal.receipt('s', 1, 'p'),
                         dict(kind='manual', departure_minute=474, ingress_ts=166))

    def test_settled_late_receipt_cannot_be_superseded_by_queued_manual(self):
        journal = self.armed_journal()
        journal.exchange('s', 1, 'p', self.token, kind='decision',
                         now_ts=168, departure_minute=475)
        journal.mark_settled('s', 1)
        result = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=166, processing_ts=169, departure_minute=474)
        self.assertEqual(result['decision_kind'], 'late')

    def test_settled_replays_accepted_choice_but_refuses_new_choice(self):
        journal = self.armed_journal()
        journal.exchange('s', 1, 'p', self.token, kind='decision',
                         now_ts=107, departure_minute=474)
        journal.mark_settled('s', 1)
        result = journal.exchange('s', 1, 'p', self.token, kind='decision',
                                  now_ts=200, departure_minute=475)
        self.assertEqual(result['decision_kind'], 'manual')
        self.ensure(session='no-choice')
        self.shell(session='no-choice')
        journal.exchange('no-choice', 1, 'p', self.token, kind='ack', now_ts=102)
        journal.mark_settled('no-choice', 1)
        with self.assertRaises(ValueError):
            journal.exchange('no-choice', 1, 'p', self.token, kind='decision',
                             now_ts=107, departure_minute=474)

    def test_boot_change_marks_unsettled_and_preserves_settled(self):
        self.armed_journal()
        self.ensure(session='settled')
        self.journal.mark_settled('settled', 1)
        reopened = SyncJournal(self.path)
        reopened.ensure_boot('boot-b')
        self.assertEqual(reopened.state('s', 1)['technical_anomaly'],
                         'worker_restarted_during_round')
        self.assertEqual(reopened.state('settled', 1)['technical_anomaly'], '')
        self.ensure(session='new')
        self.ensure(session='new', boot_id='boot-b')
        self.assertEqual(self.journal.state('new', 1)['technical_anomaly'],
                         'worker_restarted_during_round')

    def test_mark_anomaly_blocks_manual_and_capacity(self):
        journal = self.armed_journal('I1')
        journal.mark_anomaly('s', 1, 'test_failure')
        result = journal.exchange('s', 1, 'p', self.token, kind='status', now_ts=107)
        self.assertNotIn('actual_capacity_display', result)
        with self.assertRaises(ValueError):
            journal.exchange('s', 1, 'p', self.token, kind='decision',
                             now_ts=107, departure_minute=474)

    def test_pure_peek_and_unchanged_status_do_not_update_any_table(self):
        journal = self.armed_journal()
        with sqlite3.connect(self.path) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            connection.execute('CREATE TABLE update_counter (n INTEGER)')
            connection.execute('INSERT INTO update_counter VALUES (0)')
            for index, table in enumerate(tables):
                connection.execute(f'CREATE TRIGGER count_update_{index} AFTER UPDATE ON "{table}" '
                                   'BEGIN UPDATE update_counter SET n=n+1; END')
        self.assertEqual(journal.state('s', 1)['phase'], 'ARMED')
        journal.exchange('s', 1, 'p', self.token, kind='status', now_ts=104)
        journal.exchange('s', 1, 'p', self.token, kind='ack', now_ts=105)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT n FROM update_counter').fetchone()[0], 0)

    def test_transaction_exception_does_not_advance_phase(self):
        journal = self.armed_journal()
        with self.assertRaises(ValueError):
            journal.exchange('s', 1, 'p', self.token, kind='decision',
                             now_ts=108, departure_minute=999)
        self.assertEqual(journal.state('s', 1)['phase'], 'ARMED')

    def test_file_only_wal_full_and_schema_mismatch_fail_closed(self):
        with self.assertRaises(ValueError):
            SyncJournal(':memory:')
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            connection.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):
            SyncJournal(self.path)

    def test_threaded_reopen_keeps_first_choice_atomic(self):
        self.armed_journal()
        def choose(minute):
            return SyncJournal(self.path).exchange('s', 1, 'p', self.token,
                kind='decision', now_ts=110, departure_minute=minute)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(choose, [474, 475] * 8))
        self.assertTrue(all(result['decision_received'] for result in results))
        self.assertIn(self.journal.receipt('s', 1, 'p')['departure_minute'], [474, 475])
        canonical = self.journal.receipt('s', 1, 'p')
        self.assertTrue(all(result['receipt'] == canonical for result in results))

    def test_rejected_decision_preserves_observed_anomaly_and_cannot_later_arm(self):
        for session, signals, reason, processing in [
                ('failure', {'failed_codes': ['q']}, 'participant_page_http_failure', 160),
                ('crossed', {'crossed_codes': ['q']}, 'participant_page_crossed_prepare_deadline', 160),
                ('timeout', {'pending_codes': ['q']}, 'shell_ack_queue_overrun', 171)]:
            self.ensure(('p', 'q'), session=session, round_number=4)
            self.shell(session=session, round_number=4)
            self.shell('q', 'b' * 24, session=session, round_number=4)
            self.journal.exchange(session, 4, 'p', self.token, kind='ack', now_ts=102)
            with self.assertRaisesRegex(ValueError, 'technical_anomaly'):
                self.journal.exchange(session, 4, 'p', self.token, kind='decision',
                    now_ts=159, processing_ts=processing, departure_minute=474, **signals)
            self.assertIsNone(self.journal.receipt(session, 4, 'p'))
            self.assertEqual(self.journal.state(session, 4)['technical_anomaly'], reason)
            later = self.journal.exchange(session, 4, 'q', 'b' * 24, kind='ack',
                now_ts=159.9, processing_ts=processing + 1)
            self.assertEqual(later['phase'], 'TECHNICAL_ANOMALY')
            self.assertIsNone(later['release_ts'])

    def test_late_status_excludes_genuine_absence_even_beyond_drain(self):
        self.ensure(('p', 'q'), round_number=4)
        self.shell(round_number=4)
        self.journal.exchange('s', 4, 'p', self.token, kind='ack', now_ts=102)
        result = self.journal.exchange('s', 4, 'p', self.token, kind='status', now_ts=171)
        self.assertEqual(result['technical_anomaly'], '')
        self.assertEqual(result['release_ts'], 176)
        state = self.journal.state('s', 4)
        self.assertEqual(state['excluded_codes'], ['q'])
        self.assertEqual(state['phase'], 'ARMED')

    def test_journal_state_identifies_durable_protocol_v2(self):
        created = self.ensure()
        self.assertEqual(created['protocol'], 2)
        reopened = SyncJournal(self.path)
        self.assertEqual(reopened.state('s', 1)['protocol'], 2)

    def test_mixed_condition_session_reveals_only_each_participants_capacity(self):
        journal = self.journal
        journal.ensure_round('s', 1, expected_codes=['p', 'q', 'r'], now_ts=100,
            decision_page_index=9, drain_limit_seconds=10,
            information_condition={'p': 'I0', 'q': 'I1', 'r': 'I1'},
            allowed_minutes=[474, 475], actual_capacity_display={'p': '1', 'q': '2', 'r': '3'},
            capacity_level_label={'p': 'low', 'q': 'medium', 'r': 'high'}, boot_id='boot-a')
        for code, token in [('p', self.token), ('q', 'b' * 24), ('r', 'c' * 24)]:
            self.shell(code, token)
            journal.exchange('s', 1, code, token, kind='ack', now_ts=102)
        for code, token, capacity, label in [('p', self.token, None, None),
                                            ('q', 'b' * 24, '2', 'medium'),
                                            ('r', 'c' * 24, '3', 'high')]:
            result = journal.exchange('s', 1, code, token, kind='status', now_ts=107)
            self.assertEqual(result.get('actual_capacity_display'), capacity)
            self.assertEqual(result.get('capacity_level_label'), label)
        self.assertNotIn('actual_capacity_display', journal.state('s', 1))

    def test_audit_token_prefers_first_ack_over_first_render_and_stays_private(self):
        self.ensure()
        self.shell()
        self.assertEqual(self.journal.audit('s', 1, 'p').get('shell_token'), self.token)
        self.shell(token='b' * 24, now_ts=102)
        self.journal.exchange('s', 1, 'p', 'b' * 24, kind='ack', now_ts=103)
        self.shell(token='c' * 24, now_ts=104)
        self.journal.exchange('s', 1, 'p', 'c' * 24, kind='ack', now_ts=105)
        self.assertEqual(self.journal.audit('s', 1, 'p').get('shell_token'), 'b' * 24)
        self.assertNotIn('shell_token', self.journal.state('s', 1))
        result = self.journal.exchange('s', 1, 'p', 'b' * 24, kind='status', now_ts=106)
        self.assertNotIn('shell_token', result)


if __name__ == '__main__':
    unittest.main()
