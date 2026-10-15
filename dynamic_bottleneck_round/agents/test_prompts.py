"""Prompt contracts: public arithmetic, legacy compatibility, and one API call."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import importlib
import importlib.util
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .deepseek_agent import (
    AgentChoiceSet, DeepSeekAgentConfig, build_chat_completion_payload,
    choose_agent_departure, config_from_session,
)


def choice_set():
    return AgentChoiceSet(
        round_number=2, total_rounds=30, agent_id='test-seat',
        available_slots=[{'slot': slot, 'departure_minute': minute,
                          'departure_time': clock}
                         for slot, minute, clock in
                         ((9, 474, '07:54'), (12, 477, '07:57'), (14, 479, '07:59'))],
        cost_parameters=dict(free_flow_travel_minutes=6,
            preferred_arrival_minute=480, preferred_arrival_time='08:00',
            fixed_travel_time_cost=0, queue_cost_per_minute=2,
            early_cost_per_minute=1, late_cost_per_minute=3),
        capacity_context={'information_condition': 'I0',
                          'current_capacity_revealed': False},
        persona={'persona_id': 'test', 'traits': {'adaptation_speed': 9}},
        history={'public_feedback': None, 'own_previous_result': None},
        limited_memory='上一轮印象可能有误。',
    )


class DynamicPromptTests(unittest.TestCase):
    def prompts(self):
        name = 'dynamic_bottleneck_round.agents.prompts'
        self.assertIsNotNone(importlib.util.find_spec(name), 'versioned prompt module missing')
        return importlib.import_module(name)

    def config(self, **changes):
        return DeepSeekAgentConfig(prompt_version=self.prompts().CURRENT_PROMPT_VERSION,
                                   limited_memory_enabled=True, **changes)

    def context(self, config, choices):
        payload = build_chat_completion_payload(config, choices)
        return payload, json.loads(payload['messages'][1]['content'].split('\n\n', 1)[1])

    def test_public_baseline_uses_runtime_travel_and_keeps_all_slots(self):
        choices = choice_set()
        before = asdict(choices)
        _, context = self.context(self.config(), choices)
        slots = context['available_slots']
        self.assertEqual([s['slot'] for s in slots], [9, 12, 14])
        self.assertEqual([s['fixed_travel_arrival_minute'] for s in slots], [480, 483, 485])
        self.assertEqual([s['fixed_travel_arrival_time'] for s in slots],
                         ['08:00', '08:03', '08:05'])
        self.assertEqual(context['persona'], before['persona'])
        self.assertEqual(asdict(choices), before, 'must not mutate caller data')
        costs = {**choices.cost_parameters, 'free_flow_travel_minutes': 4.5}
        _, altered = self.context(self.config(), replace(choices, cost_parameters=costs))
        self.assertEqual(altered['available_slots'][0]['fixed_travel_arrival_time'], '07:58:30')
        self.assertEqual(altered['available_slots'][0]['fixed_travel_arrival_minute'], 478.5)

    def test_v2_instructions_cover_rules_memory_and_output_without_hidden_estimates(self):
        payload, context = self.context(self.config(), choice_set())
        instructions = payload['messages'][0]['content']
        for text in ('非负', '下界', '不是实际到达预测', 'cost_parameters',
                     '空档', '未知', '人格', '客观成本', '最终', 'memory_summary',
                     '尚未结算', 'I0', 'I1', '时刻', '400'):
            self.assertIn(text, instructions)
        self.assertIn('max(', instructions)
        self.assertEqual(context['prompt_version'], self.prompts().CURRENT_PROMPT_VERSION)
        self.assertEqual(payload['max_tokens'], 512)
        self.assertEqual(payload['temperature'], 0)
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        for slot in context['available_slots']:
            self.assertFalse({'predicted_queue', 'predicted_cost', 'actual_capacity'} & slot.keys())

    def test_i0_never_gains_capacity_i1_keeps_exact_revealed_value(self):
        original = choice_set()
        _, i0 = self.context(self.config(), original)
        self.assertNotIn('actual_capacity', json.dumps(i0, ensure_ascii=False))
        reveal = {'information_condition': 'I1', 'current_capacity_revealed': True,
                  'actual_capacity': 2.51}
        _, i1 = self.context(self.config(), replace(original, capacity_context=reveal))
        self.assertEqual(i1['capacity_context'], reveal)
        self.assertEqual(i0['history'], i1['history'])

    def test_memory_disabled_or_empty_is_omitted(self):
        config = self.config()
        for config, choices in ((replace(config, limited_memory_enabled=False), choice_set()),
                                (config, replace(choice_set(), limited_memory=''))):
            payload, context = self.context(config, choices)
            self.assertNotIn('limited_memory', context)
            if not config.limited_memory_enabled:
                self.assertNotIn('memory_summary', payload['messages'][0]['content'])

    def test_v2_preserves_production_i0_i1_public_context(self):
        import dynamic_bottleneck_round as app
        from .test_dynamic_agent_integration import DynamicAgentInformationParityTests
        for condition in ('I0', 'I1'):
            player, group = DynamicAgentInformationParityTests.make_round(condition)
            preview = [dict(slot=9, minute=474, time='07:54', toll=0,
                            toll_active=False, reward=0)]
            with patch.object(app, 'choice_preview', return_value=preview):
                choices = app.api_agent_choice_set_for_group(group, player, 'test', {})
            _, context = self.context(self.config(), choices)
            self.assertEqual(context['capacity_context'], choices.capacity_context)
            self.assertNotIn('capacity_sequence_id', json.dumps(context))
            self.assertNotIn('capacity_sequence_seed', json.dumps(context))
            self.assertEqual(context['history'], {'public_feedback': None, 'own_previous_result': None})
            self.assertEqual('actual_capacity' in context['capacity_context'], condition == 'I1')

    def test_v2_bad_response_uses_existing_fallback_once(self):
        calls = []
        def malformed(config, payload):
            calls.append(True)
            return {'choices': [{'message': {'content': '{broken'}}]}
        result = choose_agent_departure(config=self.config(), choice_set=choice_set(), http_post=malformed)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.decision_source, 'fallback_lowest_schedule_cost')
        self.assertEqual(result.departure_slot, 9)

    def test_missing_saved_version_uses_legacy_and_unknown_version_is_rejected(self):
        prompts = self.prompts()
        self.assertEqual(config_from_session({}).prompt_version, prompts.LEGACY_PROMPT_VERSION)
        for version in ('unknown', '', None):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'prompt_version'):
                config_from_session({'api_agent_prompt_version': version})
        self.assertEqual(config_from_session({'api_agent_prompt_version': prompts.CURRENT_PROMPT_VERSION})
                         .prompt_version, prompts.CURRENT_PROMPT_VERSION)

    def test_legacy_payload_matches_pre_rewrite_snapshot(self):
        payload = build_chat_completion_payload(
            DeepSeekAgentConfig(limited_memory_enabled=True), choice_set())
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         'c81d80ab655997621411909d00792f6f91fe715c419613e3c9f728c68cae9f27')

    def test_single_call_and_audit_context_match_actual_v2_request(self):
        calls = []
        def post(config, payload):
            calls.append(deepcopy(payload))
            return {'choices': [{'message': {'content': json.dumps({
                'departure_slot': 12, 'reason': '07:57 的固定行程基准是 08:03。',
                'memory_summary': '仅是上一轮的印象。'})}}]}
        result = choose_agent_departure(config=self.config(), choice_set=choice_set(), http_post=post)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.departure_slot, 12, 'do not replace a legal but late action')
        self.assertEqual(json.loads(result.context_json),
                         json.loads(calls[0]['messages'][1]['content'].split('\n\n', 1)[1]))
        self.assertEqual(result.memory_summary, '仅是上一轮的印象。')

    def test_new_dynamic_configs_select_v2_only_for_dynamic_scene(self):
        import settings
        for config in settings.SESSION_CONFIGS:
            if config['name'].startswith('dynamic_bottleneck_round'):
                self.assertEqual(config.get('api_agent_prompt_version'),
                                 self.prompts().CURRENT_PROMPT_VERSION)
            else:
                self.assertNotIn('api_agent_prompt_version', config)

    def test_new_api_records_record_saved_prompt_version_including_legacy(self):
        import dynamic_bottleneck_round as app
        prompts = self.prompts()
        player = SimpleNamespace()
        group = SimpleNamespace(session=SimpleNamespace(config={}),
            id_in_subsession=1, round_number=4, dynamic_capacity=2.5,
            get_players=lambda: [player])
        capacity = dict(capacity_level='middle', distribution='truncated_normal',
            capacity_mu=2.665, capacity_sigma=0.8, capacity_truncated_mean=2.665,
            capacity_truncated_sd=0.64, capacity_min=1.33, capacity_max=4,
            sequence_id='test-only', sequence_seed=42)
        from .deepseek_agent import AgentChoice
        with patch.object(app, 'departure_schedule_for_player', return_value={}), \
             patch.object(app, 'departure_minute_for_slot', return_value=474), \
             patch.object(app, 'capacity_record_for_group', return_value=capacity), \
             patch.object(app, 'information_condition_for_group', return_value='I0'):
            for saved in ({}, {'api_agent_prompt_version': prompts.CURRENT_PROMPT_VERSION}):
                group.session.config = deepcopy(saved)
                expected = saved.get('api_agent_prompt_version', prompts.LEGACY_PROMPT_VERSION)
                records = app.build_api_agent_records_for_group(group,
                    [('test-seat', choice_set())], [AgentChoice(9, 'deepseek_api', False)])
                self.assertEqual(records[0].get('prompt_version'), expected)
                self.assertEqual(group.session.config, saved)

    def test_creation_rejects_invalid_prompt_version_before_other_setup(self):
        import dynamic_bottleneck_round as app
        subsession = SimpleNamespace(round_number=1,
            session=SimpleNamespace(config={'api_agent_prompt_version': 'typo'}))
        with self.assertRaisesRegex(ValueError, 'api_agent_prompt_version'):
            app.creating_session(subsession)

    def test_formal_custom_export_keeps_stored_version_without_backfilling(self):
        import dynamic_bottleneck_round as app
        reference = SimpleNamespace(group=SimpleNamespace(id_in_subsession=1),
                                    round_number=4, dynamic_capacity=2)
        self.assertIn('agent_prompt_version', app.EXPORT_HEADERS)
        with patch.object(app, 'export_row_for_player',
                          side_effect=lambda _: [''] * len(app.EXPORT_HEADERS)):
            for record, expected in (({}, ''),
                ({'prompt_version': self.prompts().CURRENT_PROMPT_VERSION},
                 self.prompts().CURRENT_PROMPT_VERSION)):
                row = app.export_row_for_agent_record(record, reference)
                self.assertEqual(row[app.EXPORT_HEADERS.index('agent_prompt_version')], expected)


if __name__ == '__main__':
    unittest.main()
