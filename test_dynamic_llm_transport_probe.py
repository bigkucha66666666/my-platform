"""No paid calls: validate diagnosis phases and the conditional call budget."""
from copy import deepcopy
import importlib
import importlib.util
import json
import unittest
from unittest.mock import patch

from dynamic_bottleneck_round.agents.deepseek_agent import DeepSeekAgentConfig


def full_payload():
    return dict(model='test-model', temperature=0, max_tokens=512,
        thinking={'type': 'disabled'}, response_format={'type': 'json_object'},
        messages=[{'role': 'system', 'content': 'Current full JSON instructions'},
                  {'role': 'user', 'content': 'Public context\n\n' + json.dumps({
                      'available_slots': [{'slot': 9, 'departure_minute': 474,
                          'departure_time': '07:54', 'fixed_travel_arrival_minute': 480,
                          'fixed_travel_arrival_time': '08:00'}],
                      'cost_parameters': {'free_flow_travel_minutes': 6},
                      'capacity_context': {'information_condition': 'I0'},
                      'history': {'public_feedback': None, 'own_previous_result': None},
                      'persona': {'persona_id': 'test-persona'}})}])


class FakeResponse:
    status = 200
    headers = {'Content-Type': 'application/json'}
    def __init__(self, *, error=None):
        self.error = error
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        if self.error:
            raise self.error
        return b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'


class TransportProbeTests(unittest.TestCase):
    def module(self):
        name = 'scripts.diagnose_dynamic_llm_transport'
        self.assertIsNotNone(importlib.util.find_spec(name), 'phased diagnostic tool missing')
        return importlib.import_module(name)

    def test_prompt_variants_only_change_messages_and_keep_public_context(self):
        module = self.module()
        original = full_payload()
        before = deepcopy(original)
        variants = module.prompt_variants(original)
        self.assertEqual(variants['full'], before)
        for payload in variants.values():
            self.assertEqual({k: v for k, v in payload.items() if k != 'messages'},
                             {k: v for k, v in before.items() if k != 'messages'})
        self.assertEqual(variants['compact']['messages'][1], before['messages'][1])
        self.assertIn('非负', variants['compact']['messages'][0]['content'])
        self.assertIn('尚未', variants['compact']['messages'][0]['content'])
        self.assertEqual(original, before)

    def test_header_timeout_and_body_timeout_are_distinguished_without_secret(self):
        module = self.module()
        config = DeepSeekAgentConfig(api_key='never-print-this-secret')
        with patch('urllib.request.urlopen', side_effect=TimeoutError('header timed out')):
            result = module.probe_request(config, full_payload())
        self.assertEqual(result['failed_stage'], 'response_headers')
        self.assertIsNone(result['http_status'])
        with patch('urllib.request.urlopen', return_value=FakeResponse(error=TimeoutError('body timed out'))):
            result = module.probe_request(config, full_payload())
        self.assertEqual(result['failed_stage'], 'response_body')
        self.assertEqual(result['http_status'], 200)
        self.assertNotIn('never-print-this-secret', json.dumps(result))

    def test_success_records_completed_body_and_exact_payload(self):
        module = self.module()
        with patch('urllib.request.urlopen', return_value=FakeResponse()):
            result = module.probe_request(DeepSeekAgentConfig(api_key='secret'), full_payload())
        self.assertTrue(result['response_received'])
        self.assertEqual(result['failed_stage'], '')
        self.assertEqual(result['request_payload'], full_payload())
        self.assertIn('response_body_completed', [stage['stage'] for stage in result['events']])

    def test_two_failures_do_not_trigger_third_call_or_retries(self):
        module = self.module()
        calls = []
        def probe(config, payload):
            calls.append(payload)
            return {'response_received': False, 'raw_response': None}
        result = module.run_diagnosis(DeepSeekAgentConfig(), full_payload(), probe=probe)
        self.assertEqual(len(calls), 2)
        self.assertEqual([record['variant'] for record in result['records']], ['minimal', 'full'])

    def test_compact_only_runs_after_minimal_valid_success_and_full_failure(self):
        module = self.module()
        calls = []
        def probe(config, payload):
            calls.append(payload)
            return {'response_received': len(calls) != 2,
                    'raw_response': {'choices': [{'message': {'content': '{"ok":true}'}}]}}
        result = module.run_diagnosis(DeepSeekAgentConfig(), full_payload(), probe=probe)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result['records'][-1]['variant'], 'compact')
        self.assertEqual(result['max_http_calls'], 3)


if __name__ == '__main__':
    unittest.main()
