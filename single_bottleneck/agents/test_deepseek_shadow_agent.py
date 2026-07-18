import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deepseek_shadow_agent import (
    AgentChoiceSet,
    DeepSeekAgentConfig,
    build_chat_completion_payload,
    choose_shadow_departure,
    parse_deepseek_choice,
)


class DeepSeekShadowAgentTests(unittest.TestCase):
    def test_config_uses_v4_flash_model_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_AGENT_API_KEY": "",
                "DEEPSEEK_AGENT_BASE_URL": "https://api.deepseek.com",
                "DEEPSEEK_AGENT_MODEL": "deepseek-v4-flash",
                "DEEPSEEK_AGENT_TIMEOUT_SECONDS": "7",
                "DEEPSEEK_AGENT_TEMPERATURE": "0",
            },
            clear=False,
        ):
            config = DeepSeekAgentConfig.from_env()

        self.assertEqual(config.model, "deepseek-v4-flash")
        self.assertEqual(config.base_url, "https://api.deepseek.com")
        self.assertEqual(config.timeout_seconds, 7)
        self.assertEqual(config.temperature, 0)

    def test_payload_requires_json_departure_slot(self):
        choice_set = AgentChoiceSet(
            round_number=1,
            total_rounds=10,
            agent_id="G01_API_01",
            persona={
                "persona_id": "queue_averse_v1",
                "persona_version": "bottleneck_persona_v1",
                "label": "queue_averse",
                "traits": {"queue_aversion": 9, "choice_inertia": 5},
            },
            available_slots=[
                {"slot": 10, "departure_minute": 473, "departure_time": "07:53"},
                {"slot": 11, "departure_minute": 474, "departure_time": "07:54"},
            ],
            cost_parameters={
                "queue_cost_per_minute": 2,
                "early_cost_per_minute": 1,
                "late_cost_per_minute": 3,
            },
            tolls=[],
            rewards=[],
            history={},
        )

        payload = build_chat_completion_payload(DeepSeekAgentConfig(api_key="x"), choice_set)
        body = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertIn("departure_slot", body)
        self.assertIn("JSON", body)
        self.assertIn("07:54", body)
        self.assertIn("G01_API_01", body)
        self.assertIn("queue_averse_v1", body)
        self.assertIn("queue_aversion", body)
        self.assertIn("stable behavioral preferences", body)

    def test_parse_valid_deepseek_response(self):
        raw_response = {
            "choices": [
                {
                    "message": {
                        "content": '{"departure_slot": 11, "reason": "avoid queue and arrive on time"}'
                    }
                }
            ]
        }

        choice = parse_deepseek_choice(raw_response, valid_slots={10, 11, 12})

        self.assertEqual(choice.departure_slot, 11)
        self.assertEqual(choice.decision_source, "deepseek_api")
        self.assertFalse(choice.fallback_used)
        self.assertIn("avoid queue", choice.reason)

    def test_choose_shadow_departure_falls_back_for_invalid_response(self):
        choice_set = AgentChoiceSet(
            round_number=1,
            total_rounds=10,
            available_slots=[
                {"slot": 10, "departure_minute": 473, "departure_time": "07:53"},
                {"slot": 11, "departure_minute": 474, "departure_time": "07:54"},
                {"slot": 12, "departure_minute": 475, "departure_time": "07:55"},
            ],
            cost_parameters={
                "queue_cost_per_minute": 2,
                "early_cost_per_minute": 1,
                "late_cost_per_minute": 3,
            },
            tolls=[],
            rewards=[],
            history={},
        )

        def fake_post(_config, _payload):
            return {"choices": [{"message": {"content": '{"departure_slot": 99}'}}]}

        choice = choose_shadow_departure(
            config=DeepSeekAgentConfig(api_key="x"),
            choice_set=choice_set,
            http_post=fake_post,
        )

        self.assertEqual(choice.departure_slot, 11)
        self.assertEqual(choice.decision_source, "fallback_lowest_schedule_cost")
        self.assertTrue(choice.fallback_used)

    def test_choose_shadow_departure_uses_valid_api_response(self):
        choice_set = AgentChoiceSet(
            round_number=2,
            total_rounds=10,
            agent_id="G01_API_01",
            persona={
                "persona_id": "adaptive_v1",
                "persona_version": "bottleneck_persona_v1",
                "label": "adaptive",
                "traits": {"adaptation_speed": 9},
            },
            available_slots=[
                {"slot": 10, "departure_minute": 473, "departure_time": "07:53"},
                {"slot": 11, "departure_minute": 474, "departure_time": "07:54"},
                {"slot": 12, "departure_minute": 475, "departure_time": "07:55"},
            ],
            cost_parameters={
                "queue_cost_per_minute": 2,
                "early_cost_per_minute": 1,
                "late_cost_per_minute": 3,
            },
            tolls=[],
            rewards=[],
            history={},
        )

        def fake_post(_config, _payload):
            return {"choices": [{"message": {"content": '{"departure_slot": 12, "reason": "test"}'}}]}

        choice = choose_shadow_departure(
            config=DeepSeekAgentConfig(api_key="x"),
            choice_set=choice_set,
            http_post=fake_post,
        )

        self.assertEqual(choice.departure_slot, 12)
        self.assertEqual(choice.decision_source, "deepseek_api")
        self.assertFalse(choice.fallback_used)
        self.assertEqual(choice.reason, "test")
        recorded_context = json.loads(choice.context_json)
        self.assertEqual(recorded_context["agent_id"], "G01_API_01")
        self.assertEqual(recorded_context["persona"], choice_set.persona)


if __name__ == "__main__":
    unittest.main()
