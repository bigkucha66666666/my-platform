"""Focused, database-free checks for the new per-Session long export."""
import copy
import csv
from decimal import Decimal
import importlib
import importlib.util
import io
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest


LLM_STORE = 'dynamic_bottleneck_round_agent_decisions_by_round_v1'
RL_STORE = 'dynamic_bottleneck_round_independent_rl_decisions_by_round_v1'
PROTOCOL = 'dynamic_bottleneck_room_release_protocol_v1'
INITIALIZED = 'dynamic_room_sync_initialized_rounds_v2'


class ReadOnlyVars(SimpleNamespace):
    @property
    def vars(self):
        raise AssertionError('Reading .vars would dirty the ORM; use _vars')


def make_session(code='target'):
    return ReadOnlyVars(code=code, config={}, _vars={})


def make_group(session, number, group_id=1, settled=True):
    group = SimpleNamespace(session=session, round_number=number,
                            id_in_subsession=group_id, results_ready=settled,
                            players=[])
    group.get_players = lambda: group.players
    return group


def make_player(group, code='human1', chosen=True):
    participant = ReadOnlyVars(code=code, label='P001', _vars={})
    player = SimpleNamespace(session=group.session, group=group,
        round_number=group.round_number, participant=participant,
        actor_composition='HA', information_condition='I1',
        dynamic_capacity=2.665, capacity_distribution='truncated_normal',
        capacity_level='中', departure_slot=1 if chosen else None,
        departure_minute=474 if chosen else 0,
        departure_time_label='07:54' if chosen else '',
        decision_source='manual' if chosen else '', total_cost=0,
        payoff_unrounded=140, queue_delay_minutes=0,
        arrival_minute=483, arrival_time_label='08:03',
        early_minutes=0, late_minutes=3)
    player.field_maybe_none = lambda name: getattr(player, name, None)
    group.players.append(player)
    return player


class SessionDataExportTests(unittest.TestCase):
    def test_prompt_version_is_record_based_and_missing_history_stays_blank(self):
        module = self.module()
        session = make_session()
        session.config['api_agent_prompt_version'] = 'dynamic_bottleneck_prompt_v2'
        player = make_player(make_group(session, 4))
        player.participant._vars[LLM_STORE] = {'4': [
            dict(actor_type='deepseek_api_agent', agent_id='legacy', departure_slot=9),
            dict(actor_type='deepseek_api_agent', agent_id='new', departure_slot=9,
                 prompt_version='dynamic_bottleneck_prompt_v2')]}
        before = copy.deepcopy(player.participant._vars)
        rows = list(module.all_round_rows(session, [player], warmup_rounds=3))
        self.assertIn('agent_prompt_version', module.HEADERS)
        by_agent = {row['agent_id']: row for row in rows if row['actor_type'] != 'human'}
        self.assertEqual(by_agent['legacy']['agent_prompt_version'], '')
        self.assertEqual(by_agent['new']['agent_prompt_version'], 'dynamic_bottleneck_prompt_v2')
        self.assertEqual(rows[0]['agent_prompt_version'], '')
        self.assertEqual(player.participant._vars, before)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('session_data_export'),
                             'per-Session export module has not been implemented')
        return importlib.import_module('session_data_export')

    def test_human_export_contains_warmup_formal_and_all_stored_rounds(self):
        module = self.module()
        session = make_session()
        players = [make_player(make_group(session, number), code)
                   for number in (1, 3, 4, 35) for code in ('a', 'b')]
        rows = list(module.all_round_rows(session, reversed(players), warmup_rounds=3))
        self.assertEqual(len(rows), 8)
        self.assertEqual([r['round_number'] for r in rows], [1, 1, 3, 3, 4, 4, 35, 35])
        self.assertEqual(rows[0]['round_phase'], 'warmup')
        self.assertEqual(rows[0]['formal_round_number'], '')
        self.assertEqual(rows[4]['round_phase'], 'formal')
        self.assertEqual(rows[4]['phase_round_number'], 1)
        self.assertEqual(rows[6]['formal_round_number'], 32)
        self.assertEqual({r['session_code'] for r in rows}, {'target'})

    def test_unselected_and_unsettled_fields_are_blank_not_zero(self):
        module = self.module()
        session = make_session()
        unselected = make_player(make_group(session, 1, settled=False), chosen=False)
        chosen = make_player(make_group(session, 2, settled=False))
        rows = list(module.all_round_rows(session, [unselected, chosen], warmup_rounds=3))
        for name in ('departure_slot', 'departure_minute', 'departure_time', 'total_cost',
                     'queue_delay_minutes', 'arrival_minute', 'payoff'):
            self.assertEqual(rows[0][name], '', name)
        self.assertEqual(rows[1]['departure_minute'], 474)
        self.assertFalse(rows[1]['results_ready'])
        self.assertEqual(rows[1]['total_cost'], '')

    def test_real_zero_result_is_preserved(self):
        module = self.module()
        session = make_session()
        player = make_player(make_group(session, 4))
        row, = module.all_round_rows(session, [player], warmup_rounds=3)
        self.assertEqual(row['total_cost'], 0)
        self.assertEqual(row['queue_delay_minutes'], 0)
        self.assertTrue(row['results_ready'])

    def test_missing_session_code_never_falls_back_to_global_export(self):
        module = self.module()
        for code in ('', None, '../other', 'target\r\nInjected: yes'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                list(module.all_round_rows(make_session(code), [], warmup_rounds=3))

    def test_mixed_session_input_is_rejected(self):
        module = self.module()
        session = make_session()
        foreign = make_player(make_group(make_session('foreign'), 4))
        with self.assertRaises(ValueError):
            list(module.all_round_rows(session, [foreign], warmup_rounds=3))

    def test_agents_include_warmup_and_are_deduplicated_per_group(self):
        module = self.module()
        session = make_session()
        players = []
        for number in (1, 4):
            group = make_group(session, number)
            first = make_player(group, 'a')
            second = make_player(group, 'b')
            llm = dict(actor_type='deepseek_api_agent', agent_id='LLM01', group_id=1,
                       departure_minute=473, departure_slot=2, total_cost=0,
                       payoff_unrounded=140, reason='整数,中文\n换行', fallback_used=True)
            rl = dict(actor_type='rl_agent', agent_id='RL01', group_id=1,
                      departure_minute=472, departure_slot=3, total_cost=4,
                      policy_version='frozen-v3', rounds_observed=0)
            first.participant._vars[LLM_STORE] = {str(number): [llm, llm.copy()]}
            first.participant._vars[RL_STORE] = {str(number): [rl]}
            second.participant._vars = copy.deepcopy(first.participant._vars)
            players.extend([first, second])
        before = copy.deepcopy([p.participant._vars for p in players])
        rows = list(module.all_round_rows(session, players, warmup_rounds=3))
        self.assertEqual(len(rows), 8)
        agents = [r for r in rows if r['actor_type'] != 'human']
        self.assertEqual(len(agents), 4)
        self.assertEqual({r['round_number'] for r in agents}, {1, 4})
        self.assertEqual({r['agent_id'] for r in agents}, {'LLM01', 'RL01'})
        self.assertTrue(all(r['participant_code'] == '' for r in agents))
        self.assertTrue(all(r['participant_label'] == '' for r in agents))
        self.assertEqual([p.participant._vars for p in players], before)

    def test_agents_do_not_inherit_human_results_or_zero_defaults(self):
        module = self.module()
        session = make_session()
        player = make_player(make_group(session, 4, settled=False))
        player.participant._vars[RL_STORE] = {'4': [dict(actor_type='rl_agent',
            agent_id='RL01', departure_minute=474, departure_slot=1, total_cost=0)]}
        rows = list(module.all_round_rows(session, [player], warmup_rounds=3))
        rl, = [r for r in rows if r['actor_type'] == 'rl_agent']
        self.assertEqual(rl['total_cost'], '')
        self.assertEqual(rl['payoff'], '')
        self.assertEqual(rl['arrival_time'], '')
        self.assertEqual(rl['departure_minute'], 474)

    def test_foreign_group_agent_record_is_not_exported(self):
        module = self.module()
        session = make_session()
        player = make_player(make_group(session, 4))
        player.participant._vars[RL_STORE] = {'4': [dict(actor_type='rl_agent',
            agent_id='RL02', group_id=2, departure_minute=474)]}
        rows = list(module.all_round_rows(session, [player], warmup_rounds=3))
        self.assertEqual(len(rows), 1)

    def test_csv_bom_special_characters_and_formula_safety(self):
        module = self.module()
        values = ['中文,引号"\n换行', '=1+2', ' \t@SUM(A1)', '-2+3', -2, 0, None, True]
        encoded = module.encode_csv(['value'], [[value] for value in values])
        self.assertTrue(encoded.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(io.StringIO(encoded.decode('utf-8-sig'))))
        self.assertEqual(rows[1][0], values[0])
        self.assertEqual(rows[2][0], "'=1+2")
        self.assertEqual(rows[3][0], "' \t@SUM(A1)")
        self.assertEqual(rows[4][0], "'-2+3")
        self.assertEqual([row[0] for row in rows[5:]], ['-2', '0', '', '1'])

    def test_sync_reader_never_creates_a_missing_file(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.sqlite3'
            self.assertEqual(module.read_sync_records(path, 'target'), {})
            self.assertFalse(path.exists())

    def test_sync_reader_is_readonly_and_session_scoped(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.sqlite3'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE sync_rounds (session_code TEXT, round_number INTEGER, record TEXT)')
                for code in ('target', 'foreign'):
                    connection.execute('INSERT INTO sync_rounds VALUES (?, 4, ?)',
                        (code, json.dumps({'state': {'phase': 'CLOSED', 'technical_anomaly': code}})))
            before = path.read_bytes()
            records = module.read_sync_records(path, 'target')
            self.assertEqual(set(records), {4})
            self.assertEqual(records[4]['state']['technical_anomaly'], 'target')
            self.assertEqual(path.read_bytes(), before)

    def test_missing_initialized_sync_audit_is_not_silently_valid(self):
        module = self.module()
        session = make_session()
        session._vars.update({PROTOCOL: 2, INITIALIZED: [4]})
        player = make_player(make_group(session, 4, settled=False))
        row, = module.all_round_rows(session, [player], warmup_rounds=3, sync_records={})
        self.assertEqual(row['room_release_export_audit_status'], 'unavailable')
        self.assertTrue(row['room_release_exclude_behavior'])
        self.assertEqual(row['room_release_technical_anomaly'], '')

    def test_saved_sync_anomaly_is_read_without_mutating_it(self):
        module = self.module()
        session = make_session()
        session._vars.update({PROTOCOL: 2, INITIALIZED: [4]})
        player = make_player(make_group(session, 4, settled=False))
        records = {4: {'state': {'phase': 'TECHNICAL_ANOMALY',
                    'technical_anomaly': 'existing_fault', 'release_ts': None}}}
        before = copy.deepcopy(records)
        row, = module.all_round_rows(session, [player], warmup_rounds=3, sync_records=records)
        self.assertEqual(row['room_release_technical_anomaly'], 'existing_fault')
        self.assertTrue(row['room_release_exclude_behavior'])
        self.assertEqual(records, before)

    def test_old_session_warmup_count_uses_saved_formal_round_metadata(self):
        module = self.module()
        self.assertTrue(callable(getattr(module, 'warmup_round_count', None)))
        session = make_session()
        session.config['payoff_rounds'] = 60
        players = [make_player(make_group(session, number)) for number in range(1, 63)]
        self.assertEqual(module.warmup_round_count(session, players, default=3), 2)
        session.config['warmup_rounds'] = 5
        self.assertEqual(module.warmup_round_count(session, players, default=3), 5)

    def test_current_or_missing_round_metadata_has_safe_warmup_fallback(self):
        module = self.module()
        self.assertTrue(callable(getattr(module, 'warmup_round_count', None)))
        session = make_session()
        players = [make_player(make_group(session, number)) for number in range(1, 34)]
        self.assertEqual(module.warmup_round_count(session, players, default=3), 3)
        session.config['payoff_rounds'] = 30
        self.assertEqual(module.warmup_round_count(session, players, default=3), 3)

    def test_agent_store_is_read_from_first_id_in_group_not_code_order(self):
        module = self.module()
        session = make_session()
        group = make_group(session, 4)
        second = make_player(group, 'aaa')
        second.id_in_group = 2
        first = make_player(group, 'zzz')
        first.id_in_group = 1
        first.participant._vars[RL_STORE] = {'4': [dict(actor_type='rl_agent',
            agent_id='RL01', departure_minute=474)]}
        rows = list(module.all_round_rows(session, [second, first], warmup_rounds=3))
        self.assertEqual(len([row for row in rows if row['actor_type'] == 'rl_agent']), 1)

    def test_runtime_fault_is_displayed_without_persisting_new_history(self):
        module = self.module()
        self.assertIn('runtime_faults', inspect.signature(module.all_round_rows).parameters)
        session = make_session()
        session._vars.update({PROTOCOL: 2, INITIALIZED: [4]})
        player = make_player(make_group(session, 4, settled=False))
        records = {4: {'state': {'phase': 'RELEASED', 'technical_anomaly': '', 'settled': False}}}
        before = copy.deepcopy(records)
        row, = module.all_round_rows(session, [player], warmup_rounds=3,
            sync_records=records, runtime_faults={4: 'existing_latched_fault'})
        self.assertEqual(row['room_release_technical_anomaly'], 'existing_latched_fault')
        self.assertTrue(row['room_release_exclude_behavior'])
        self.assertEqual(records, before)

    def test_decimal_currency_is_exported_as_numeric_not_points_text(self):
        module = self.module()
        class CurrencyLike(Decimal):
            def __str__(self):
                return super().__str__() + ' points'
        encoded = module.encode_csv(['cost'], [[CurrencyLike('0')], [CurrencyLike('3.50')]])
        values = [row[0] for row in csv.reader(io.StringIO(encoded.decode('utf-8-sig')))][1:]
        self.assertTrue(all('points' not in value for value in values), values)
        self.assertEqual([float(value) for value in values], [0.0, 3.5])

    def test_legacy_agent_payoff_field_is_preserved_without_inventing_zero(self):
        module = self.module()
        session = make_session()
        player = make_player(make_group(session, 4))
        player.participant._vars[RL_STORE] = {'4': [
            dict(actor_type='rl_agent', agent_id='legacy', departure_minute=474, payoff=130),
            dict(actor_type='rl_agent', agent_id='zero', departure_minute=474,
                 payoff_unrounded=0, payoff=130),
            dict(actor_type='rl_agent', agent_id='missing', departure_minute=474),
        ]}
        agents = {row['agent_id']: row for row in module.all_round_rows(session, [player], warmup_rounds=3)
                  if row['actor_type'] == 'rl_agent'}
        self.assertEqual(agents['legacy']['payoff'], 130)
        self.assertEqual(agents['zero']['payoff'], 0)
        self.assertEqual(agents['missing']['payoff'], '')


if __name__ == '__main__':
    unittest.main()
