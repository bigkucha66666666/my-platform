import json
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
    choose_agent_departure,
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
            self.assertIn(str(config['rl_agent_enabled']).lower(), {'0', 'false', 'off'})
            self.assertEqual(config['rl_agent_count_per_group'], '1')
            self.assertEqual(
                config['rl_agent_policy_version'],
                'dynamic_liu_rel_incident_v1',
            )
            self.assertEqual(config['rel_lambda'], 0.25)
            self.assertEqual(config['rel_eta'], 14.7445)
            self.assertEqual(config['rel_capacity_bandwidth'], 0.560924)
            self.assertEqual(config['rel_random_seed'], 2026090901)
            self.assertEqual(config['rel_initial_uniform_rounds'], 2)
            self.assertIn(
                str(config['api_agent_limited_memory_enabled']).lower(),
                {'1', 'true', 'on'},
            )
            self.assertEqual(config['api_agent_limited_memory_max_chars'], 400)
        prod = next(
            config for config in dynamic_configs
            if config['name'] == 'dynamic_bottleneck_round_prod'
        )
        demo = next(
            config for config in dynamic_configs
            if config['name'] == 'dynamic_bottleneck_round_demo'
        )
        self.assertEqual(str(prod['rel_parameters_frozen']), '0')
        self.assertEqual(str(demo['rel_parameters_frozen']), '0')

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

    def test_independent_rl_count_validation(self):
        for count in (1, 5, '3'):
            with self.subTest(count=count):
                session = self.make_session('off', 1)
                session.config.update({
                    'rl_agent_enabled': '1',
                    'rl_agent_count_per_group': count,
                })
                self.assertEqual(app.validate_rl_agent_count(session), int(count))

        for count in (0, 6, '2.5', True):
            with self.subTest(count=count):
                session = self.make_session('off', 1)
                session.config.update({
                    'rl_agent_enabled': '1',
                    'rl_agent_count_per_group': count,
                })
                with self.assertRaisesRegex(ValueError, '1 到 5'):
                    app.validate_rl_agent_count(session)

    def test_liu_rel_parameter_validation_rejects_invalid_values(self):
        valid = {
            'name': 'dynamic_bottleneck_round_demo',
            'rl_agent_enabled': '1',
            'rel_policy_version': 'dynamic_liu_rel_incident_v1',
            'rel_lambda': 0.25,
            'rel_eta': 14.7445,
            'rel_capacity_bandwidth': 0.560924,
            'rel_random_seed': 2026090901,
            'rel_initial_uniform_rounds': 2,
            'rel_parameters_frozen': '0',
        }
        invalid = (
            ('rel_lambda', -0.1),
            ('rel_eta', 0),
            ('rel_capacity_bandwidth', 0),
            ('rel_random_seed', 1.5),
            ('rel_initial_uniform_rounds', 3),
            ('rel_policy_version', 'old'),
        )
        for field_name, value in invalid:
            with self.subTest(field_name=field_name):
                session = SimpleNamespace(config={**valid, field_name: value})
                with self.assertRaisesRegex(ValueError, field_name):
                    app.validate_liu_rel_session_config(session)

    def test_rl_enabled_production_requires_frozen_parameters(self):
        session = SimpleNamespace(
            config={
                'name': 'dynamic_bottleneck_round_prod',
                'rl_agent_enabled': '1',
                'rel_policy_version': 'dynamic_liu_rel_incident_v1',
                'rel_lambda': 0.25,
                'rel_eta': 14.7445,
                'rel_capacity_bandwidth': 0.560924,
                'rel_random_seed': 2026090901,
                'rel_initial_uniform_rounds': 2,
                'rel_parameters_frozen': '0',
            }
        )

        with self.assertRaisesRegex(ValueError, '冻结'):
            app.validate_liu_rel_session_config(session)

        session.config['rel_parameters_frozen'] = '1'
        validated = app.validate_liu_rel_session_config(session)
        self.assertTrue(validated['rel_parameters_frozen'])

    def test_effective_actor_count_includes_each_actor_once(self):
        session = self.make_session('active', 2)
        session.config.update({
            'rl_fallback_enabled': '1',
            'rl_agent_enabled': '1',
            'rl_agent_count_per_group': 3,
        })

        self.assertEqual(app.effective_group_actor_count(session, 20), 25)

    def test_rl_personas_are_stable_and_namespaced(self):
        session = SimpleNamespace(code='SESSION_PERSONA', vars={})

        first = app.initialize_rl_agent_personas(session, ['G01'], 2)
        second = app.initialize_rl_agent_personas(session, ['G01'], 2)

        self.assertEqual(first, second)
        self.assertEqual(set(first), {'G01_RL_01', 'G01_RL_02'})
        self.assertTrue(
            app.get_or_create_rl_agent_persona(
                session,
                'G01',
                'G01_RL_01',
            )['persona_id']
        )


class DynamicAgentRevealTests(unittest.TestCase):
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


    def test_chat_payload_uses_fast_bounded_json_output(self):
        choice_set = AgentChoiceSet(
            round_number=1,
            total_rounds=60,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={'capacity_revealed': False},
        )

        payload = build_chat_completion_payload(
            DeepSeekAgentConfig(model='test-model'),
            choice_set,
        )

        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(payload['max_tokens'], 512)

    def test_first_round_prompt_omits_empty_limited_memory(self):
        choice_set = AgentChoiceSet(
            round_number=1,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={'capacity_revealed': False},
        )

        payload = build_chat_completion_payload(
            DeepSeekAgentConfig(
                model='test-model',
                limited_memory_enabled=True,
                limited_memory_max_chars=400,
            ),
            choice_set,
        )

        self.assertNotIn('limited_memory', payload['messages'][1]['content'])

    def test_prompt_contains_only_supplied_limited_memory(self):
        choice_set = AgentChoiceSet(
            round_number=4,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={'capacity_revealed': False},
            limited_memory='较高服务率可能连续出现，但证据有限。',
        )

        payload = build_chat_completion_payload(
            DeepSeekAgentConfig(
                model='test-model',
                limited_memory_enabled=True,
                limited_memory_max_chars=400,
            ),
            choice_set,
        )
        prompt = payload['messages'][1]['content']

        self.assertIn('较高服务率可能连续出现，但证据有限。', prompt)
        self.assertNotIn('previous_rounds', prompt)

    def test_api_returns_bounded_memory_without_extra_call(self):
        choice_set = AgentChoiceSet(
            round_number=2,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={'capacity_revealed': False},
            limited_memory='旧记忆',
        )
        calls = []

        def fake_post(_config, payload):
            calls.append(payload)
            return {
                'choices': [
                    {
                        'message': {
                            'content': json.dumps(
                                {
                                    'departure_slot': 1,
                                    'reason': 'test',
                                    'memory_summary': '新\n记忆' + ('很' * 20),
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

        choice = choose_agent_departure(
            config=DeepSeekAgentConfig(
                model='test-model',
                limited_memory_enabled=True,
                limited_memory_max_chars=8,
            ),
            choice_set=choice_set,
            http_post=fake_post,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(choice.memory_summary, '新 记忆很很很很')
        self.assertEqual(len(choice.memory_summary), 8)

    def test_old_response_format_keeps_memory_update_empty(self):
        choice_set = AgentChoiceSet(
            round_number=2,
            total_rounds=10,
            available_slots=({'slot': 1, 'departure_minute': 474},),
            cost_parameters={},
            capacity_context={'capacity_revealed': False},
            limited_memory='旧记忆',
        )

        choice = choose_agent_departure(
            config=DeepSeekAgentConfig(
                model='test-model',
                limited_memory_enabled=True,
                limited_memory_max_chars=400,
            ),
            choice_set=choice_set,
            http_post=lambda *_args: {
                'choices': [
                    {'message': {'content': '{"departure_slot": 1, "reason": "ok"}'}}
                ]
            },
        )

        self.assertIsNone(choice.memory_summary)


class DynamicAgentInformationParityTests(unittest.TestCase):
    PUBLIC_FIELDS = {
        'information_condition',
        'normal_capacity',
        'incident_probability',
        'loss_distribution',
        'expected_incident_capacity',
        'expected_unconditional_capacity',
        'capacity_revealed',
        'incident_occurred',
        'actual_capacity',
    }

    @staticmethod
    def make_round(condition):
        session = SimpleNamespace(
            config={
                'name': 'dynamic_bottleneck_round_demo',
                'accident_normal_capacity': 4.0,
                'accident_probability': 0.20,
                'accident_loss_alpha': 6.83057,
                'accident_loss_beta': 4.05907,
                'accident_sequence_seed': 2026090801,
                'accident_information_condition': condition,
                'api_agent_mode': 'off',
                'rl_agent_enabled': '0',
                'reward_treatment_enabled': 0,
            },
            vars={},
        )
        schedule = app.static_departure_schedule()
        participant = SimpleNamespace(
            vars={
                app.DEPARTURE_SCHEDULE_VAR: schedule,
                'assigned_group_label': 'G01',
            }
        )
        player = SimpleNamespace(
            session=session,
            participant=participant,
            round_number=app.C.WARMUP_ROUNDS + 1,
            dynamic_capacity=1.5,
            incident_occurred=True,
            capacity_loss_ratio=0.625,
            remaining_capacity_ratio=0.375,
            information_condition=condition,
            accident_sequence_id='S01',
            accident_sequence_seed=2026090801,
            previous_round_capacity=0,
            coarse_toll_enabled=False,
            coarse_toll_points=0,
        )
        player.field_maybe_none = lambda field_name: getattr(
            player,
            field_name,
            None,
        )
        group = SimpleNamespace(
            session=session,
            round_number=player.round_number,
            id_in_subsession=1,
            dynamic_capacity=1.5,
            incident_occurred=True,
            capacity_loss_ratio=0.625,
            remaining_capacity_ratio=0.375,
            information_condition=condition,
            accident_sequence_id='S01',
            accident_sequence_seed=2026090801,
            get_players=lambda: [player],
        )
        player.group = group
        return player, group

    def test_human_and_agent_receive_identical_condition_limited_context(self):
        for condition in ('I0', 'I1', 'I2'):
            with self.subTest(condition=condition):
                player, group = self.make_round(condition)
                preview = [
                    {
                        'slot': 1,
                        'minute': 466,
                        'time': '07:46',
                        'toll': 0,
                        'toll_active': False,
                        'reward': 0,
                    }
                ]
                with (
                    patch.object(app, 'choice_preview', return_value=preview),
                    patch.object(app.Decision, 'get_timeout_seconds', return_value=60),
                    patch.object(app, 'agent_history_for_group', return_value={}),
                    patch.object(app, 'limited_memory_for_agent', return_value=''),
                ):
                    human_template = app.Decision.vars_for_template(player)
                    choice_set = app.api_agent_choice_set_for_group(
                        group,
                        player,
                        'G01_API_01',
                        {},
                    )

                human_context = {
                    key: human_template[key]
                    for key in self.PUBLIC_FIELDS
                    if key in human_template
                }
                agent_context = {
                    key: choice_set.capacity_context[key]
                    for key in self.PUBLIC_FIELDS
                    if key in choice_set.capacity_context
                }
                self.assertEqual(human_context, agent_context)
                self.assertNotIn('capacity_loss_ratio', choice_set.capacity_context)
                self.assertNotIn('accident_sequence_id', choice_set.capacity_context)
                self.assertNotIn('accident_sequence_seed', choice_set.capacity_context)
                if condition == 'I0':
                    self.assertNotIn('incident_occurred', agent_context)
                    self.assertNotIn('actual_capacity', agent_context)
                elif condition == 'I1':
                    self.assertTrue(agent_context['incident_occurred'])
                    self.assertNotIn('actual_capacity', agent_context)
                else:
                    self.assertEqual(agent_context['actual_capacity'], 1.5)

    def test_rl_fallback_receives_i1_incident_signal_without_hidden_capacity(self):
        player, group = self.make_round('I1')
        group.session.config.update(
            {'api_agent_mode': 'active', 'rl_fallback_enabled': '1'}
        )
        choice_set = AgentChoiceSet(
            round_number=1,
            total_rounds=60,
            available_slots=({'slot': 1, 'departure_minute': 466},),
            cost_parameters={},
            capacity_context={
                'information_condition': 'I1',
                'incident_occurred': True,
                'capacity_revealed': False,
                'capacity_states': [
                    {'state': 'normal', 'capacity': 4.0, 'probability': 0.8},
                    {
                        'state': 'incident_expected',
                        'capacity': 1.490853959841,
                        'probability': 0.2,
                    },
                ],
            },
            agent_id='G01_API_01',
            persona={},
        )
        selected = {
            'departure_slot': 1,
            'decision_source': 'deepseek_fallback_rl',
            'reason': '',
            'belief': {'1.490853959841': 1.0},
        }

        with patch.object(app, 'choose_rl_departure', return_value=selected) as choose:
            app.rl_candidate_for_choice_set(group, choice_set)

        self.assertTrue(choose.call_args.kwargs['known_incident_status'])
        self.assertIsNone(choose.call_args.kwargs['known_current_capacity'])


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

    @staticmethod
    def make_record_group():
        participant = SimpleNamespace(vars={})
        player = SimpleNamespace(participant=participant)
        group = SimpleNamespace(
            session=SimpleNamespace(
                code='SESSION_RECORD',
                config={'api_agent_mode': 'active'},
            ),
            id_in_subsession=1,
            round_number=2,
            get_players=lambda: [player],
        )
        return group, participant

    def test_virtual_record_storage_merges_api_and_rl_records(self):
        group, _participant = self.make_record_group()
        rl_record = {'actor_type': 'rl_agent', 'agent_id': 'G01_RL_01'}
        api_record = {
            'actor_type': 'deepseek_api_agent',
            'agent_id': 'G01_API_01',
        }

        app.save_virtual_decisions_for_group(group, [rl_record])
        app.save_virtual_decisions_for_group(group, [api_record])

        records = app.active_virtual_decisions_for_group(group)
        self.assertEqual(
            {(row['actor_type'], row['agent_id']) for row in records},
            {
                ('rl_agent', 'G01_RL_01'),
                ('deepseek_api_agent', 'G01_API_01'),
            },
        )

    def test_api_and_independent_rl_records_use_separate_stores(self):
        group, participant = self.make_record_group()
        api_record = {
            'actor_type': 'deepseek_api_agent',
            'agent_id': 'G01_API_01',
        }
        rl_record = {'actor_type': 'rl_agent', 'agent_id': 'G01_RL_01'}

        app.save_virtual_decisions_for_group(group, [api_record])
        app.save_virtual_decisions_for_group(group, [rl_record])

        api_store = participant.vars[app.AGENT_DECISIONS_PARTICIPANT_VAR]
        rl_store = participant.vars[app.INDEPENDENT_RL_DECISIONS_PARTICIPANT_VAR]
        self.assertEqual(api_store['2'], [api_record])
        self.assertEqual(rl_store['2'], [rl_record])
        self.assertEqual(
            {row['agent_id'] for row in app.active_virtual_decisions_for_group(group)},
            {'G01_API_01', 'G01_RL_01'},
        )

    def test_agent_history_exposes_only_previous_public_feedback(self):
        group, participant = self.make_record_group()
        group.round_number = 7
        participant.vars[app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '5': {
                'round_number': 5,
                'dynamic_capacity': 1,
                'departure_outcomes': [],
                'group_average_cost': 18,
            },
            '6': {
                'round_number': 1,
                'dynamic_capacity': 3,
                'departure_outcomes': [
                    {'slot': 2, 'participant_count': 2, 'average_cost': 10},
                ],
                'group_average_cost': 10,
            },
        }
        participant.vars[app.AGENT_DECISIONS_PARTICIPANT_VAR] = {
            '5': [{'agent_id': 'G01_API_01', 'round_number': 5, 'total_cost': 18}],
            '6': [
                {
                    'agent_id': 'G01_API_01',
                    'round_number': 1,
                    'departure_slot': 2,
                    'departure_minute': 474,
                    'departure_time_label': '07:54',
                    'queue_delay_minutes': 1,
                    'arrival_minute': 481,
                    'arrival_time_label': '08:01',
                    'early_minutes': 0,
                    'late_minutes': 1,
                    'total_cost': 10,
                    'payoff': 90,
                    'coarse_toll_charge': 0,
                    'reward_bonus': 0,
                }
            ],
        }

        history = app.agent_history_for_group(group, 'G01_API_01')

        self.assertEqual(history['public_feedback']['round_number'], 1)
        self.assertEqual(history['own_previous_result']['round_number'], 1)
        self.assertNotIn('previous_rounds', history)
        self.assertNotIn('"round_number": 2', json.dumps(history))
        self.assertNotIn('payoff', history['own_previous_result'])
        self.assertNotIn('reward_bonus', history['own_previous_result'])
        self.assertNotIn('early_minutes', history['own_previous_result'])
        self.assertNotIn('late_minutes', history['own_previous_result'])

    def test_missing_public_snapshot_does_not_expose_private_personal_record(self):
        group, participant = self.make_record_group()
        group.round_number = 7
        participant.vars[app.AGENT_DECISIONS_PARTICIPANT_VAR] = {
            '6': [
                {
                    'agent_id': 'G01_API_01',
                    'round_number': 2,
                    'departure_time_label': '07:54',
                    'total_cost': 10,
                }
            ]
        }

        history = app.agent_history_for_group(group, 'G01_API_01')

        self.assertEqual(
            history,
            {'public_feedback': None, 'own_previous_result': None},
        )

    def test_first_round_agent_history_has_no_previous_feedback(self):
        group, _participant = self.make_record_group()
        group.round_number = 1

        history = app.agent_history_for_group(group, 'G01_API_01')

        self.assertEqual(
            history,
            {'public_feedback': None, 'own_previous_result': None},
        )

    def test_first_formal_round_does_not_receive_warmup_history(self):
        group, participant = self.make_record_group()
        group.round_number = 6
        participant.vars[app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '5': {'round_number': 5, 'dynamic_capacity': 2},
        }
        participant.vars[app.AGENT_DECISIONS_PARTICIPANT_VAR] = {
            '5': [{'agent_id': 'G01_API_01', 'round_number': 5}],
        }

        self.assertEqual(
            app.agent_history_for_group(group, 'G01_API_01'),
            {'public_feedback': None, 'own_previous_result': None},
        )

    def test_limited_memory_is_isolated_by_agent(self):
        group, participant = self.make_record_group()
        group.session.config.update({
            'api_agent_limited_memory_enabled': 1,
            'api_agent_limited_memory_max_chars': 400,
        })
        participant.vars[app.API_AGENT_MEMORY_PARTICIPANT_VAR] = {
            'G01_API_01': '一号记忆',
            'G01_API_02': '二号记忆',
        }

        self.assertEqual(
            app.limited_memory_for_agent(group, 'G01_API_01'),
            '一号记忆',
        )
        self.assertEqual(
            app.limited_memory_for_agent(group, 'G01_API_02'),
            '二号记忆',
        )

    def test_memory_update_preserves_old_value_on_missing_or_fallback_output(self):
        group, participant = self.make_record_group()
        group.session.config.update({
            'api_agent_limited_memory_enabled': 1,
            'api_agent_limited_memory_max_chars': 400,
        })
        participant.vars[app.API_AGENT_MEMORY_PARTICIPANT_VAR] = {
            'G01_API_01': '已有判断',
        }

        app.save_api_agent_memory_updates(
            group,
            [
                {
                    'agent_id': 'G01_API_01',
                    'decision_source': 'fallback_lowest_schedule_cost',
                    'memory_output': None,
                }
            ],
        )

        self.assertEqual(
            app.limited_memory_for_agent(group, 'G01_API_01'),
            '已有判断',
        )

    def test_valid_memory_update_replaces_only_its_agent_memory(self):
        group, participant = self.make_record_group()
        group.round_number = 6
        group.session.config.update({
            'api_agent_limited_memory_enabled': 1,
            'api_agent_limited_memory_max_chars': 400,
        })
        participant.vars[app.API_AGENT_MEMORY_PARTICIPANT_VAR] = {
            'G01_API_01': '旧判断',
            'G01_API_02': '另一人的判断',
        }

        app.save_api_agent_memory_updates(
            group,
            [
                {
                    'agent_id': 'G01_API_01',
                    'decision_source': 'deepseek_api',
                    'memory_output': '新判断',
                }
            ],
        )

        self.assertEqual(
            participant.vars[app.API_AGENT_MEMORY_PARTICIPANT_VAR],
            {
                'G01_API_01': '新判断',
                'G01_API_02': '另一人的判断',
            },
        )

    def test_warmup_does_not_update_limited_memory(self):
        group, participant = self.make_record_group()
        group.round_number = 1
        group.session.config.update({
            'api_agent_limited_memory_enabled': 1,
            'api_agent_limited_memory_max_chars': 400,
        })

        app.save_api_agent_memory_updates(
            group,
            [{
                'agent_id': 'G01_API_01',
                'decision_source': 'deepseek_api',
                'memory_output': '不应保存的热身记忆',
            }],
        )

        self.assertNotIn(app.API_AGENT_MEMORY_PARTICIPANT_VAR, participant.vars)

    def test_agent_export_contains_memory_audit_fields(self):
        reference_player = SimpleNamespace(
            group=SimpleNamespace(id_in_subsession=1),
            round_number=2,
            dynamic_capacity=2,
            dynamic_capacity_state='capacity_2',
        )
        record = {
            'actor_type': 'deepseek_api_agent',
            'agent_id': 'G01_API_01',
            'memory_input': '上一轮判断',
            'memory_output': '更新后的判断',
        }

        with patch.object(
            app,
            'export_row_for_player',
            return_value=[''] * len(app.EXPORT_HEADERS),
        ):
            row = app.export_row_for_agent_record(record, reference_player)

        self.assertEqual(
            row[app.EXPORT_HEADERS.index('agent_memory_input')],
            '上一轮判断',
        )
        self.assertEqual(
            row[app.EXPORT_HEADERS.index('agent_memory_output')],
            '更新后的判断',
        )

    def test_api_lookup_ignores_existing_rl_records(self):
        group, _participant = self.make_record_group()
        app.save_virtual_decisions_for_group(
            group,
            [{'actor_type': 'rl_agent', 'agent_id': 'G01_RL_01'}],
        )

        self.assertEqual(app.active_agent_decisions_for_group(group), [])

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

    def test_settlement_combines_human_api_and_independent_rl(self):
        participant = SimpleNamespace(vars={})
        player = SimpleNamespace(
            participant=participant,
            departure_minute=474,
        )
        api_record = {
            'actor_type': 'deepseek_api_agent',
            'agent_id': 'G01_API_01',
            'departure_slot': 2,
            'departure_minute': 474,
        }
        rl_record = {
            'actor_type': 'rl_agent',
            'agent_id': 'G01_RL_01',
            'departure_slot': 2,
            'departure_minute': 474,
        }
        group = SimpleNamespace(
            results_ready=False,
            dynamic_capacity=2,
            round_number=6,
            session=SimpleNamespace(config={'reward_treatment_enabled': 0}),
            get_players=lambda: [player],
        )
        schedule = {
            'enabled': True,
            'num_slots': 3,
            'slot_size_minutes': 1,
            'first_departure_minute': 473,
            'last_departure_minute': 475,
        }

        with (
            patch.object(app, 'all_players_have_choice', return_value=True),
            patch.object(app, 'collect_api_agent_prefetch', return_value=[api_record]),
            patch.object(
                app,
                'prepare_independent_rl_decisions_for_group',
                return_value=[rl_record],
            ),
            patch.object(app, 'departure_schedule_for_player', return_value=schedule),
            patch.object(app, 'player_departure_slot', return_value=2),
            patch.object(app, 'set_player_departure_choice'),
            patch.object(app, 'departure_minute_for_slot', return_value=474),
            patch.object(app, 'update_rl_shadow_states') as update_shadow,
            patch.object(app, 'update_independent_rl_states') as update_independent,
            patch.object(app, 'save_virtual_decisions_for_group') as save_virtual,
        ):
            result = app._set_results_locked(group)

        self.assertTrue(result)
        self.assertEqual(player.slot_load, 3)
        self.assertEqual(api_record['slot_load'], 3)
        self.assertEqual(rl_record['slot_load'], 3)
        update_shadow.assert_called_once_with(group, [api_record])
        update_independent.assert_called_once_with(group, [rl_record])
        save_virtual.assert_called_once_with(group, [api_record, rl_record])

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
            patch.object(
                app,
                'prepare_independent_rl_decisions_for_group',
            ) as prepare_rl,
        ):
            app.RoundStartSync.vars_for_template(player)

        start.assert_called_once_with(group)
        prepare_rl.assert_called_once_with(group)

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
            round_number=6,
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
        participant.vars[app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '6': {
                'round_number': 1,
                'dynamic_capacity': 3,
                'departure_outcomes': [
                    {'slot': 2, 'participant_count': 2, 'average_cost': 9},
                ],
                'group_average_cost': 9,
            }
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

    def test_api_shadow_learning_uses_public_feedback_snapshot(self):
        group, participant = self.make_independent_rl_group()
        group.session.config.update({
            'api_agent_mode': 'active',
            'rl_fallback_enabled': 1,
        })
        participant.vars[app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '6': {
                'round_number': 1,
                'dynamic_capacity': 2,
                'departure_outcomes': [
                    {'slot': 1, 'participant_count': 0, 'average_cost': None},
                    {'slot': 2, 'participant_count': 3, 'average_cost': 9},
                    {'slot': 3, 'participant_count': 0, 'average_cost': None},
                ],
                'group_average_cost': 9,
            }
        }
        record = {
            'actor_type': 'deepseek_api_agent',
            'agent_id': 'G01_API_01',
            'departure_slot': 2,
            'total_cost': 9,
        }

        app.update_rl_shadow_states(group, [record])

        state = participant.vars[app.RL_AGENT_STATE_PARTICIPANT_VAR]['G01_API_01']
        self.assertEqual(state['last_anonymous_slot_counts'], {'1': 0, '2': 3, '3': 0})

    def test_api_shadow_learning_counts_independent_rl_actors(self):
        group, participant = self.make_independent_rl_group()
        group.session.config.update({
            'api_agent_mode': 'active',
            'rl_fallback_enabled': 1,
        })
        api_record = {
            'actor_type': 'deepseek_api_agent',
            'agent_id': 'G01_API_01',
            'departure_slot': 2,
            'total_cost': 9,
        }
        rl_record = {
            'actor_type': 'rl_agent',
            'agent_id': 'G01_RL_01',
            'departure_slot': 2,
        }
        participant.vars[app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '6': {
                'round_number': 1,
                'dynamic_capacity': 2,
                'departure_outcomes': [
                    {'slot': 2, 'participant_count': 3, 'average_cost': 9},
                ],
                'group_average_cost': 9,
            }
        }
        persona = app.get_or_create_api_agent_persona(
            group.session,
            'G01',
            'G01_API_01',
        )

        with patch.object(
            app,
            'active_virtual_decisions_for_group',
            return_value=[api_record, rl_record],
        ):
            app.update_rl_shadow_states(group, [api_record])

        state = participant.vars[app.RL_AGENT_STATE_PARTICIPANT_VAR]['G01_API_01']
        self.assertEqual(state['last_anonymous_slot_counts']['2'], 3)
        self.assertEqual(persona['persona_id'], state.get('persona_id', persona['persona_id']))

    @staticmethod
    def make_independent_rl_group():
        session = SimpleNamespace(
            code='SESSION_INDEPENDENT_RL',
            vars={},
            config={
                'api_agent_mode': 'off',
                'rl_agent_enabled': '1',
                'rl_agent_count_per_group': 2,
                'accident_information_condition': 'I0',
                'reward_treatment_enabled': 0,
                'rel_policy_version': 'dynamic_liu_rel_incident_v1',
                'rel_lambda': 0.25,
                'rel_eta': 14.7445,
                'rel_capacity_bandwidth': 0.560924,
                'rel_random_seed': 2026090901,
                'rel_initial_uniform_rounds': 2,
                'rel_parameters_frozen': '0',
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
            departure_slot=2,
            departure_minute=474,
        )
        player.field_maybe_none = lambda field_name: getattr(player, field_name, None)
        group = SimpleNamespace(
            session=session,
            round_number=6,
            id_in_subsession=1,
            dynamic_capacity=2,
            dynamic_capacity_state='incident',
            capacity_probability=0.2,
            incident_occurred=True,
            capacity_loss_ratio=0.5,
            remaining_capacity_ratio=0.5,
            information_condition='I0',
            accident_sequence_id='S01',
            accident_sequence_seed=2026090801,
            get_players=lambda: [player],
        )
        return group, participant

    def test_warmup_does_not_update_api_shadow_or_independent_rl(self):
        group, _participant = self.make_independent_rl_group()
        group.round_number = 1
        group.session.config.update({
            'api_agent_mode': 'active',
            'rl_fallback_enabled': 1,
        })
        api_record = {
            'actor_type': 'deepseek_api_agent',
            'agent_id': 'G01_API_01',
        }
        rl_record = {
            'actor_type': 'rl_agent',
            'agent_id': 'G01_RL_01',
        }

        with patch.object(app, 'current_public_feedback_for_group') as feedback:
            app.update_rl_shadow_states(group, [api_record])
            app.update_independent_rl_states(group, [rl_record])

        feedback.assert_not_called()

    def test_independent_rl_records_are_created_once_without_capacity_leak(self):
        group, _participant = self.make_independent_rl_group()

        first = app.prepare_independent_rl_decisions_for_group(group)
        second = app.prepare_independent_rl_decisions_for_group(group)

        self.assertEqual(first, second)
        self.assertEqual(
            {record['agent_id'] for record in first},
            {'G01_RL_01', 'G01_RL_02'},
        )
        self.assertTrue(
            all(record['actor_type'] == 'rl_agent' for record in first)
        )
        self.assertTrue(
            all(
                record['decision_source'] == 'liu_rel_uniform_initial'
                for record in first
            )
        )
        self.assertTrue(
            all(
                'current_actual_capacity'
                not in json.loads(record['context_json'])
                for record in first
            )
        )
        audit = json.loads(first[0]['context_json'])
        self.assertEqual(audit['policy_version'], 'dynamic_liu_rel_incident_v1')
        self.assertEqual(audit['information_condition'], 'I0')
        self.assertIn('choice_probabilities', audit)
        self.assertIn('random_seed_fingerprint', audit)

    def test_independent_rl_states_update_once_per_agent(self):
        group, participant = self.make_independent_rl_group()
        records = app.prepare_independent_rl_decisions_for_group(group)
        records[0]['total_cost'] = 5
        records[1]['total_cost'] = 17
        participant.vars[app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '6': {
                'round_number': 1,
                'dynamic_capacity': 2,
                'incident_occurred': True,
                'departure_outcomes': [
                    {'slot': 1, 'participant_count': 0, 'average_cost': None},
                    {'slot': 2, 'participant_count': 3, 'average_cost': 11},
                    {'slot': 3, 'participant_count': 0, 'average_cost': None},
                ],
                'group_average_cost': 11,
            }
        }

        app.update_independent_rl_states(group, records)
        app.update_independent_rl_states(group, records)

        states = participant.vars[app.INDEPENDENT_RL_STATE_PARTICIPANT_VAR]
        self.assertEqual(states['G01_RL_01']['rounds_observed'], 1)
        self.assertEqual(states['G01_RL_02']['rounds_observed'], 1)
        self.assertNotEqual(
            states['G01_RL_01']['experiences'][0]['total_cost'],
            states['G01_RL_02']['experiences'][0]['total_cost'],
        )

    def test_independent_rl_learning_uses_public_feedback_snapshot(self):
        group, participant = self.make_independent_rl_group()
        records = app.prepare_independent_rl_decisions_for_group(group)
        records[0]['total_cost'] = 5
        records[1]['total_cost'] = 17
        participant.vars[app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '6': {
                'round_number': 1,
                'dynamic_capacity': 2,
                'incident_occurred': True,
                'departure_outcomes': [
                    {'slot': 1, 'participant_count': 1, 'average_cost': 8},
                    {'slot': 2, 'participant_count': 2, 'average_cost': 11},
                    {'slot': 3, 'participant_count': 0, 'average_cost': None},
                ],
                'group_average_cost': 10,
            }
        }

        app.update_independent_rl_states(group, records)

        states = participant.vars[app.INDEPENDENT_RL_STATE_PARTICIPANT_VAR]
        experience = states['G01_RL_01']['experiences'][0]
        self.assertEqual(experience['actual_capacity'], 2)
        self.assertEqual(experience['incident_occurred'], True)
        self.assertNotIn('last_anonymous_slot_counts', states['G01_RL_01'])

    def test_independent_rl_passes_only_condition_permitted_current_information(self):
        expected = {
            'I0': (False, False),
            'I1': (True, False),
            'I2': (True, True),
        }
        for condition, (has_incident, has_capacity) in expected.items():
            with self.subTest(condition=condition):
                group, _participant = self.make_independent_rl_group()
                group.session.config['accident_information_condition'] = condition
                group.information_condition = condition
                fake_choice = {
                    'departure_slot': 1,
                    'decision_source': 'liu_rel_uniform_initial',
                    'reason': 'test',
                    'policy_version': 'dynamic_liu_rel_incident_v1',
                    'rounds_observed': 0,
                    'information_condition': condition,
                    'context_level': 'initial',
                    'propensities': {},
                    'choice_probabilities': {'1': 1.0},
                    'selected_probability': 1.0,
                    'distinct_experienced_slots': 0,
                    'effective_observation_count': 0.0,
                    'rel_lambda': 0.25,
                    'rel_eta': 14.7445,
                    'rel_capacity_bandwidth': 0.560924,
                    'random_seed_fingerprint': 'abcdef123456',
                }

                with patch.object(
                    app,
                    'choose_independent_rl_departure',
                    return_value=fake_choice,
                ) as choose:
                    records = app.prepare_independent_rl_decisions_for_group(group)

                kwargs = choose.call_args_list[0].kwargs
                self.assertEqual(
                    'current_incident_occurred' in kwargs,
                    has_incident,
                )
                self.assertEqual(
                    'current_actual_capacity' in kwargs,
                    has_capacity,
                )
                self.assertNotIn('persona', kwargs)
                self.assertNotIn('cost_parameters', kwargs)
                audit = json.loads(records[0]['context_json'])
                self.assertEqual(
                    'current_incident_occurred' in audit,
                    has_incident,
                )
                self.assertEqual(
                    'current_actual_capacity' in audit,
                    has_capacity,
                )

    def test_warmup_choice_does_not_pollute_first_formal_state(self):
        group, participant = self.make_independent_rl_group()
        group.round_number = 1

        warmup = app.prepare_independent_rl_decisions_for_group(group)
        app.update_independent_rl_states(group, warmup)

        self.assertTrue(
            all(
                record['decision_source'] == 'liu_rel_uniform_warmup'
                for record in warmup
            )
        )
        self.assertNotIn(app.INDEPENDENT_RL_STATE_PARTICIPANT_VAR, participant.vars)

    def test_export_uses_virtual_record_actor_type(self):
        reference_player = SimpleNamespace(
            group=SimpleNamespace(id_in_subsession=1),
            round_number=1,
            dynamic_capacity=2,
            dynamic_capacity_state='capacity_2',
        )
        record = {
            'actor_type': 'rl_agent',
            'agent_id': 'G01_RL_01',
            'agent_type': 'rl_agent',
            'policy_version': 'dynamic_liu_rel_incident_v1',
            'persona_id': 'balanced_v1',
            'persona_label': 'balanced',
            'decision_source': 'rl_policy',
        }
        base_row = [''] * len(app.EXPORT_HEADERS)

        with patch.object(app, 'export_row_for_player', return_value=base_row):
            row = app.export_row_for_agent_record(record, reference_player)

        self.assertEqual(
            row[app.EXPORT_HEADERS.index('actor_type')],
            'rl_agent',
        )
        self.assertEqual(
            row[app.EXPORT_HEADERS.index('rl_policy_version')],
            'dynamic_liu_rel_incident_v1',
        )

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
        self.assertEqual(first[0]['persona_id'], 'balanced_v1')
        self.assertEqual(chooser.call_count, 1)


class DynamicAgentAdminTemplateTests(unittest.TestCase):
    def test_shared_create_session_form_includes_dynamic_controls(self):
        html = Path('_templates/otree/includes/CreateSessionForm.html').read_text(
            encoding='utf-8'
        )

        self.assertIn(
            '{% include "otree/includes/DynamicSessionControls.html" %}',
            html,
        )

    def test_dynamic_controls_define_four_presets_and_exact_b_values(self):
        html = Path('_templates/otree/includes/DynamicSessionControls.html').read_text(
            encoding='utf-8'
        )

        for preset in ('A', 'B', 'C', 'D'):
            with self.subTest(preset=preset):
                self.assertIn(f'data-preset="{preset}"', html)

        for value in (
            'num_participants=35',
            'cohort_size=20',
            'grouping_enabled=0',
            "manual_grouping_spec=''",
            "group_agent_spec='G01:api=0,rl=0;G02:api=5,rl=0'",
            "api_agent_mode='active'",
            'api_agent_count_per_group=5',
            'rl_fallback_enabled=0',
            'rl_agent_enabled=0',
            'rl_agent_count_per_group=0',
            "dynamic_capacity_sequence_scope='session'",
        ):
            with self.subTest(value=value):
                self.assertIn(value, html)

    def test_create_session_page_has_dynamic_agent_toggle(self):
        html = Path(
            '_templates/otree/includes/DynamicSessionControls.html'
        ).read_text(encoding='utf-8')

        self.assertIn('是否加入 Agent', html)
        self.assertIn('dynamic_bottleneck_round_prod', html)
        self.assertIn('dynamic_bottleneck_round_demo', html)
        self.assertIn('api_agent_mode', html)
        self.assertIn('api_agent_count_per_group', html)
        self.assertIn('rl_fallback_enabled', html)
        self.assertIn('强化学习备用策略', html)
        self.assertIn('DeepSeek 不可用时', html)

    def test_create_session_page_has_independent_rl_controls(self):
        html = Path(
            '_templates/otree/includes/DynamicSessionControls.html'
        ).read_text(encoding='utf-8')

        self.assertIn('是否加入独立 RL 参与者', html)
        self.assertIn('独立 RL 参与者数量', html)
        self.assertIn('rl_agent_enabled', html)
        self.assertIn('rl_agent_count_per_group', html)
        self.assertIn('不会增加 API 等待时间', html)

    def test_create_session_page_has_capacity_sequence_selector(self):
        html = Path(
            '_templates/otree/includes/DynamicSessionControls.html'
        ).read_text(encoding='utf-8')

        self.assertIn('id="dynamic-capacity-sequence-preset"', html)
        self.assertIn('dynamic_capacity_sequence_preset', html)
        self.assertIn('value="auto"', html)
        for sequence_id in ('S01', 'S02', 'S03', 'S04', 'S05'):
            with self.subTest(sequence_id=sequence_id):
                self.assertIn(f'value="{sequence_id}"', html)

    def test_admin_report_separates_deepseek_rl_and_fallback_metrics(self):
        html = Path('dynamic_bottleneck_round/admin_report.html').read_text(
            encoding='utf-8'
        )

        self.assertIn('DeepSeek Agent', html)
        self.assertIn('独立 RL 参与者', html)
        self.assertIn('RL 备用接管', html)
        self.assertIn('independent_rl_agent_count', html)


if __name__ == '__main__':
    unittest.main()
