"""The opt-in real-API check is bounded and never uses formal Session data."""
import importlib
import importlib.util
import json
import unittest
from unittest.mock import patch

from dynamic_bottleneck_round.agents.deepseek_agent import DeepSeekAgentConfig
from dynamic_bottleneck_round.agents.liu_rel_pretraining import simulate_market
from dynamic_bottleneck_round.agents.prompts import CURRENT_PROMPT_VERSION, LEGACY_PROMPT_VERSION


class DynamicPromptCheckTests(unittest.TestCase):
    def module(self):
        name = 'scripts.check_dynamic_llm_prompt'
        self.assertIsNotNone(importlib.util.find_spec('scripts'), 'API check script missing')
        return importlib.import_module(name)

    def test_six_calls_shared_exogenous_market_and_separate_own_histories(self):
        module = self.module()
        calls = []
        def post(config, payload):
            context = json.loads(payload['messages'][1]['content'].split('\n\n', 1)[1])
            calls.append((config.prompt_version, context))
            selected = 9 if config.prompt_version == CURRENT_PROMPT_VERSION else 14
            return {'model': 'test-only', 'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps({'departure_slot': selected,
                    'reason': 'test', 'memory_summary': f'已知历史印象 {config.prompt_version}'})}}]}
        import dynamic_bottleneck_round as app
        with patch.object(app, 'load_stochastic_capacity_sequence_bank',
                          side_effect=AssertionError('must not load S01-S05')), \
             patch.object(app, 'load_pretraining_profile',
                          side_effect=AssertionError('no training history')):
            report = module.run_comparison(DeepSeekAgentConfig(
                api_key='test-secret-never-export', limited_memory_enabled=True), http_post=post)
        self.assertEqual(len(calls), 6)
        self.assertNotIn('test-secret-never-export', json.dumps(report))
        self.assertEqual(len(report['records']), 6)
        for version in (LEGACY_PROMPT_VERSION, CURRENT_PROMPT_VERSION):
            contexts = [context for actual, context in calls if actual == version]
            self.assertEqual(contexts[0]['history'],
                             {'public_feedback': None, 'own_previous_result': None})
            self.assertNotIn('actual_capacity', contexts[0]['capacity_context'])
            for context in contexts:
                self.assertNotIn('opponent_slots', json.dumps(context))
                self.assertNotIn('actual_capacity', context['capacity_context'])
                self.assertNotIn('sequence_seed', context['capacity_context'])
            first = next(r for r in report['records'] if r['prompt_version'] == version)
            expected_personal = app.public_personal_result_from_record(
                {'round_number': 1, **first['simulated_outcome']})
            self.assertEqual(contexts[1]['history']['own_previous_result'], expected_personal)
            self.assertIn(version, contexts[1]['limited_memory'])
            self.assertEqual(contexts[1]['history']['public_feedback']['actual_capacity'],
                             report['scenarios'][0]['capacity'])
        self.assertEqual(report['scenarios'], module.generate_scenarios())
        self.assertEqual(report['summary'][CURRENT_PROMPT_VERSION]['api_success_count'], 3)

    def test_timeout_is_recorded_without_retry_or_unbounded_call_count(self):
        module = self.module()
        calls = []
        def fail(config, payload):
            calls.append(True)
            raise TimeoutError('test timeout')
        report = module.run_comparison(DeepSeekAgentConfig(), http_post=fail)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(r['fallback_used'] for r in report['records']))
        self.assertEqual(report['summary'][CURRENT_PROMPT_VERSION]['api_success_count'], 0)

    def test_independent_settlement_matches_existing_training_queue_and_cost_rules(self):
        module = self.module()
        for scenario in module.generate_scenarios():
            slots = [9] + scenario['opponent_slots']
            settled = module.settle_market(scenario['capacity'], slots)
            expected = simulate_market(capacity=scenario['capacity'], departure_slots=slots)
            for result, cost in zip(settled, expected):
                self.assertAlmostEqual(result['total_cost'], cost)
                self.assertAlmostEqual(result['arrival_minute'],
                    result['departure_minute'] + 6 + result['queue_delay_minutes'])


if __name__ == '__main__':
    unittest.main()
