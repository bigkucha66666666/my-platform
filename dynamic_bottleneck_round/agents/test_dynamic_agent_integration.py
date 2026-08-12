import unittest
import threading
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import dynamic_bottleneck_round as app
import settings
from dynamic_bottleneck_round.agents.deepseek_agent import (
    AgentChoice,
    AgentChoiceSet,
    DeepSeekAgentConfig,
    build_chat_completion_payload,
)


class DynamicAgentConfigTests(unittest.TestCase):
    @staticmethod
    def make_session(mode='off', count=1):
        return SimpleNamespace(
            config={
                'api_agent_mode': mode,
                'api_agent_count_per_group': count,
            }
        )

    def test_dynamic_configs_default_to_agent_off_without_extra_scenarios(self):
        dynamic_configs = [
            config
            for config in settings.SESSION_CONFIGS
            if config['name'].startswith('dynamic_bottleneck_round')
        ]

        self.assertEqual(
            {config['name'] for config in dynamic_configs},
            {'dynamic_bottleneck_round_demo', 'dynamic_bottleneck_round_prod'},
        )
        for config in dynamic_configs:
            self.assertEqual(config['api_agent_mode'], 'off')
            self.assertEqual(config['api_agent_count_per_group'], '1')
            self.assertIn(str(config['rl_fallback_enabled']).lower(), {'0', 'false', 'off'})

    def test_rl_fallback_flag_is_boolean_config(self):
        enabled = self.make_session('active', 1)
        enabled.config['rl_fallback_enabled'] = '1'
        disabled = self.make_session('active', 1)
        disabled.config['rl_fallback_enabled'] = '0'

        self.assertTrue(app.rl_fallback_enabled(enabled))
        self.assertFalse(app.rl_fallback_enabled(disabled))

    def test_active_mode_validates_agent_count(self):
        for count in (1, 5, '3'):
            with self.subTest(count=count):
                self.assertEqual(
                    app.validate_api_agent_count(self.make_session('active', count)),
                    int(count),
                )

        for count in (0, 6, '2.5', True):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, '1 到 5'):
                    app.validate_api_agent_count(self.make_session('active', count))

    def test_agent_count_is_added_to_group_calibration_population(self):
        session = self.make_session('active', 3)

        self.assertEqual(app.effective_group_actor_count(session, 20), 23)
        self.assertEqual(
            app.effective_group_actor_count(self.make_session('off', 3), 20),
            20,
        )


class DynamicAgentRevealTests(unittest.TestCase):
    def test_after_decision_context_does_not_expose_actual_capacity(self):
        config = app.parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'capacity_reveal_timing': 'after_decision',
            }
        )

        context = app.agent_capacity_context(
            config,
            actual_capacity=2,
            previous_capacity=1,
        )

        self.assertNotIn('actual_capacity', context)
        self.assertEqual(context['previous_capacity'], 1)
        self.assertEqual(
            [state['capacity'] for state in context['capacity_states']],
            [1, 2, 3],
        )

    def test_before_decision_context_exposes_actual_capacity(self):
        config = app.parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'capacity_reveal_timing': 'before_decision',
            }
        )

        context = app.agent_capacity_context(
            config,
            actual_capacity=3,
            previous_capacity=None,
        )

        self.assertEqual(context['actual_capacity'], 3)
        self.assertIsNone(context['previous_capacity'])

    def test_serialized_after_decision_prompt_does_not_leak_capacity(self):
        choice_set = AgentChoiceSet(
            round_number=1,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={
                'capacity_revealed': False,
                'capacity_reveal_timing': 'after_decision',
                'capacity_states': [
                    {'capacity': 1, 'probability': 0.5},
                    {'capacity': 2, 'probability': 0.5},
                ],
                'previous_capacity': None,
            },
        )

        payload = build_chat_completion_payload(
            DeepSeekAgentConfig(model='test-model'),
            choice_set,
        )
        prompt = payload['messages'][1]['content']

        self.assertNotIn('actual_capacity', prompt)
        self.assertNotIn('dynamic_capacity', prompt)


class DynamicAgentDecisionTests(unittest.TestCase):
    @staticmethod
    def make_prefetch_group():
        return SimpleNamespace(
            session=SimpleNamespace(
                code='SESSION_PREFETCH',
                config={
                    'api_agent_mode': 'active',
                    'api_agent_count_per_group': 1,
                },
            ),
            id_in_subsession=1,
            round_number=2,
            get_players=lambda: [],
        )

    def test_agent_prefetch_starts_once_per_group_round(self):
        group = self.make_prefetch_group()
        future = Future()
        executor = Mock()
        executor.submit.return_value = future
        prepared = [('G01_API_01', SimpleNamespace(agent_id='G01_API_01'))]

        with (
            patch.object(app, '_API_AGENT_PREFETCH_TASKS', {}),
            patch.object(app, '_API_AGENT_PREFETCH_EXECUTOR', executor),
            patch.object(
                app,
                'prepare_api_agent_requests_for_group',
                return_value=('config', prepared),
            ) as prepare,
            patch.object(app, 'active_agent_decisions_for_group', return_value=[]),
        ):
            first = app.start_api_agent_prefetch(group)
            second = app.start_api_agent_prefetch(group)

        self.assertIs(first, future)
        self.assertIs(second, future)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(executor.submit.call_count, 1)

    def test_collect_returns_pending_without_waiting_for_api(self):
        group = self.make_prefetch_group()
        future = Future()
        key = app.api_agent_prefetch_key(group)
        task = {'future': future, 'prepared_agents': []}

        with (
            patch.object(app, '_API_AGENT_PREFETCH_TASKS', {key: task}),
            patch.object(app, 'active_agent_decisions_for_group', return_value=[]),
        ):
            result = app.collect_api_agent_prefetch(group)

        self.assertIs(result, app.API_AGENT_DECISIONS_PENDING)

    def test_collect_persists_completed_api_choices_once(self):
        group = self.make_prefetch_group()
        future = Future()
        future.set_result(['choice'])
        key = app.api_agent_prefetch_key(group)
        task = {'future': future, 'prepared_agents': ['prepared']}
        records = [{'agent_id': 'G01_API_01', 'departure_slot': 1}]

        with (
            patch.object(app, '_API_AGENT_PREFETCH_TASKS', {key: task}) as tasks,
            patch.object(app, 'active_agent_decisions_for_group', return_value=[]),
            patch.object(
                app,
                'build_api_agent_records_for_group',
                return_value=records,
            ) as build,
            patch.object(app, 'save_agent_decisions_for_group') as save,
        ):
            result = app.collect_api_agent_prefetch(group)

        self.assertEqual(result, records)
        build.assert_called_once_with(group, ['prepared'], ['choice'])
        save.assert_called_once_with(group, records)
        self.assertNotIn(key, tasks)

    def test_synchronous_wrapper_uses_collector_fallback_on_worker_exception(self):
        group = self.make_prefetch_group()
        future = Future()
        future.set_exception(RuntimeError('worker crashed'))

        with (
            patch.object(app, 'active_agent_decisions_for_group', return_value=[]),
            patch.object(app, 'start_api_agent_prefetch', return_value=future),
            patch.object(app, 'collect_api_agent_prefetch', return_value=[{'departure_slot': 2}]),
        ):
            records = app.create_api_agent_decisions_for_group(group)

        self.assertEqual(records, [{'departure_slot': 2}])

    def test_pending_agent_choices_do_not_finalize_results(self):
        group = SimpleNamespace(results_ready=False)

        with (
            patch.object(app, 'all_players_have_choice', return_value=True),
            patch.object(
                app,
                'collect_api_agent_prefetch',
                return_value=app.API_AGENT_DECISIONS_PENDING,
            ),
        ):
            result = app._set_results_locked(group)

        self.assertFalse(result)
        self.assertFalse(group.results_ready)

    def test_round_start_page_starts_agent_prefetch(self):
        group = SimpleNamespace(
            round_started=True,
            round_start_deadline_ts=200,
            get_players=lambda: [],
        )
        player = SimpleNamespace(group=group, round_number=1)

        with (
            patch.object(app, 'mark_round_ready'),
            patch.object(app, 'maybe_start_round', return_value=True),
            patch.object(app, 'start_api_agent_prefetch') as start,
        ):
            app.RoundStartSync.vars_for_template(player)

        start.assert_called_once_with(group)

    def test_agent_api_choices_run_in_parallel(self):
        choice_set = AgentChoiceSet(
            round_number=1,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={},
        )
        choice = SimpleNamespace(departure_slot=1)
        all_started = threading.Event()
        counter_lock = threading.Lock()
        started = []

        def choose_after_all_workers_start(**kwargs):
            with counter_lock:
                started.append(kwargs['choice_set'].agent_id)
                if len(started) == 3:
                    all_started.set()
            self.assertTrue(all_started.wait(1), 'Agent API calls did not run concurrently')
            return choice

        choice_sets = [
            AgentChoiceSet(
                **{
                    **choice_set.__dict__,
                    'agent_id': f'agent_{index}',
                }
            )
            for index in range(3)
        ]
        with patch.object(
            app,
            'choose_agent_departure',
            side_effect=choose_after_all_workers_start,
        ):
            choices = app.choose_api_agent_departures(
                DeepSeekAgentConfig(model='test-model'),
                choice_sets,
            )

        self.assertEqual(len(choices), 3)
        self.assertEqual(set(started), {'agent_0', 'agent_1', 'agent_2'})

    def test_api_failure_uses_prepared_rl_choice_when_enabled(self):
        choice_set = AgentChoiceSet(
            round_number=2,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={'capacity_revealed': False},
            agent_id='G01_API_01',
        )
        api_failure = AgentChoice(
            departure_slot=1,
            decision_source='fallback_lowest_schedule_cost',
            fallback_used=True,
            reason='timeout',
        )
        prepared_rl = {
            'departure_slot': 1,
            'reason': 'rl shadow policy',
            'context_json': '{"belief": [0.3, 0.4, 0.3]}',
        }

        converted = app.apply_rl_fallback_to_choice(
            api_failure,
            choice_set,
            prepared_rl,
        )

        self.assertEqual(converted.decision_source, 'deepseek_fallback_rl')
        self.assertTrue(converted.fallback_used)
        self.assertEqual(converted.departure_slot, 1)

    def test_successful_api_choice_is_not_replaced_by_rl(self):
        choice_set = AgentChoiceSet(
            round_number=2,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={'capacity_revealed': False},
        )
        api_choice = AgentChoice(
            departure_slot=1,
            decision_source='deepseek_api',
            fallback_used=False,
        )

        converted = app.apply_rl_fallback_to_choice(
            api_choice,
            choice_set,
            {'departure_slot': 1},
        )

        self.assertIs(converted, api_choice)

    def test_broken_rl_candidate_does_not_block_api_prefetch(self):
        group = self.make_prefetch_group()
        prepared = [
            ('G01_API_01', SimpleNamespace(agent_id='G01_API_01')),
        ]

        with patch.object(
            app,
            'rl_candidate_for_choice_set',
            side_effect=ValueError('broken RL state'),
        ):
            candidates = app.prepare_rl_candidates_for_agents(group, prepared)

        self.assertEqual(candidates, {'G01_API_01': None})

    def test_successful_api_round_updates_rl_shadow_state(self):
        participant = SimpleNamespace(vars={})
        session = SimpleNamespace(
            code='SESSION_RL',
            vars={},
            config={
                'api_agent_mode': 'active',
                'rl_fallback_enabled': 1,
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.333333,0.333333,0.333334',
                'capacity_reveal_timing': 'after_decision',
            },
        )
        player = SimpleNamespace(
            participant=participant,
            departure_slot=2,
        )
        group = SimpleNamespace(
            session=session,
            round_number=1,
            id_in_subsession=1,
            dynamic_capacity=3,
            get_players=lambda: [player],
        )
        record = {
            'agent_id': 'G01_API_01',
            'departure_slot': 2,
            'total_cost': 9,
            'decision_source': 'deepseek_api',
        }
        persona = {
            'persona_id': 'balanced_v1',
            'persona_version': 'dynamic_bottleneck_persona_v1',
            'label': 'balanced',
            'traits': {
                'queue_aversion': 5,
                'early_arrival_aversion': 5,
                'late_arrival_aversion': 7,
                'toll_sensitivity': 5,
                'reward_sensitivity': 5,
                'capacity_risk_aversion': 5,
                'choice_inertia': 5,
                'adaptation_speed': 5,
            },
        }

        with (
            patch.object(app, 'player_departure_slot', return_value=2),
            patch.object(app, 'get_or_create_api_agent_persona', return_value=persona),
        ):
            app.update_rl_shadow_states(group, [record])

        state = participant.vars[app.RL_AGENT_STATE_PARTICIPANT_VAR]['G01_API_01']
        self.assertEqual(state['observed_capacities'], [3])
        self.assertEqual(state['rounds_observed'], 1)
        self.assertTrue(record['rl_shadow_updated'])

    def test_group_result_lock_rejects_overlapping_generation(self):
        group = SimpleNamespace(
            session=SimpleNamespace(code='SESSION_LOCK'),
            id_in_subsession=1,
            round_number=2,
        )

        with app.try_group_results_lock(group) as first_acquired:
            with app.try_group_results_lock(group) as second_acquired:
                self.assertTrue(first_acquired)
                self.assertFalse(second_acquired)

    def test_group_result_lock_path_is_scoped_to_unix_user(self):
        group = SimpleNamespace(
            session=SimpleNamespace(code='SESSION_LOCK'),
            id_in_subsession=1,
            round_number=2,
        )

        with patch.object(app.os, 'getuid', return_value=1234):
            lock_path = app.group_results_lock_path(group)

        self.assertEqual(
            lock_path.name,
            'dynamic_bottleneck_round_uid1234_SESSION_LOCK_1_2.lock',
        )

    def test_agent_decision_is_generated_once_per_group_round(self):
        session = SimpleNamespace(
            code='SESSION_A',
            vars={},
            config={
                'api_agent_mode': 'active',
                'api_agent_count_per_group': 1,
                'capacity_reveal_timing': 'before_decision',
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
            },
        )
        participant = SimpleNamespace(
            vars={
                'assigned_group_label': 'G01',
                app.DEPARTURE_SCHEDULE_VAR: {
                    'enabled': True,
                    'num_slots': 3,
                    'slot_size_minutes': 1,
                    'first_departure_minute': 473,
                    'last_departure_minute': 475,
                },
            }
        )
        player = SimpleNamespace(
            session=session,
            participant=participant,
            previous_round_capacity=1,
            dynamic_capacity=2,
            coarse_toll_enabled=False,
            coarse_toll_points=0,
        )
        group = SimpleNamespace(
            session=session,
            round_number=2,
            id_in_subsession=1,
            dynamic_capacity=2,
            dynamic_capacity_state='capacity_2',
            capacity_probability=0.5,
            get_players=lambda: [player],
        )
        choice = SimpleNamespace(
            departure_slot=2,
            decision_source='deepseek_api',
            fallback_used=False,
            latency_ms=12,
            reason='test',
            raw_response_json='{}',
            context_json='{"capacity": 2}',
        )

        with (
            patch.object(app, 'choose_agent_departure', return_value=choice) as chooser,
            patch.object(
                app,
                'get_or_create_api_agent_persona',
                return_value={'persona_id': 'balanced_v1'},
            ),
        ):
            first = app.create_api_agent_decisions_for_group(group)
            second = app.create_api_agent_decisions_for_group(group)

        self.assertEqual(first, second)
        self.assertEqual(first[0]['departure_slot'], 2)
        self.assertEqual(first[0]['departure_minute'], 474)
        self.assertEqual(chooser.call_count, 1)


class DynamicAgentAdminTemplateTests(unittest.TestCase):
    def test_create_session_page_has_dynamic_agent_toggle(self):
        html = Path('_templates/otree/CreateSession.html').read_text(encoding='utf-8')

        self.assertIn('是否加入 Agent', html)
        self.assertIn('dynamic_bottleneck_round_prod', html)
        self.assertIn('dynamic_bottleneck_round_demo', html)
        self.assertIn('api_agent_mode', html)
        self.assertIn('api_agent_count_per_group', html)
        self.assertIn('rl_fallback_enabled', html)
        self.assertIn('强化学习备用策略', html)
        self.assertIn('DeepSeek 不可用时', html)


if __name__ == '__main__':
    unittest.main()
