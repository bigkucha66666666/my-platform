import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from personas import (
    API_AGENT_PERSONA_SESSION_VAR,
    PERSONA_LIBRARY,
    PERSONA_LIBRARY_VERSION,
    get_or_create_api_agent_persona,
    initialize_api_agent_personas,
)


class PersonaStorageTests(unittest.TestCase):
    def make_session(self):
        return SimpleNamespace(code="SESSION_A", vars={})

    def test_same_agent_keeps_same_persona_snapshot(self):
        session = self.make_session()

        first = get_or_create_api_agent_persona(session, "G01", "G01_API_01")
        second = get_or_create_api_agent_persona(session, "G01", "G01_API_01")

        self.assertEqual(first, second)
        self.assertEqual(
            session.vars[API_AGENT_PERSONA_SESSION_VAR]["G01_API_01"],
            first,
        )

    def test_returned_persona_cannot_mutate_stored_snapshot(self):
        session = self.make_session()
        first = get_or_create_api_agent_persona(session, "G01", "G01_API_01")
        original_queue_aversion = first["traits"]["queue_aversion"]

        first["traits"]["queue_aversion"] = 99
        second = get_or_create_api_agent_persona(session, "G01", "G01_API_01")

        self.assertEqual(second["traits"]["queue_aversion"], original_queue_aversion)

    def test_persona_library_is_json_serializable_and_bounded(self):
        json.dumps(PERSONA_LIBRARY)

        self.assertEqual(PERSONA_LIBRARY_VERSION, "bottleneck_persona_v1")
        self.assertEqual(len(PERSONA_LIBRARY), 5)
        for persona in PERSONA_LIBRARY:
            self.assertEqual(persona["persona_version"], PERSONA_LIBRARY_VERSION)
            self.assertTrue(persona["persona_id"].endswith("_v1"))
            for value in persona["traits"].values():
                self.assertIsInstance(value, int)
                self.assertGreaterEqual(value, 1)
                self.assertLessEqual(value, 10)

    def test_malformed_session_store_is_reinitialized(self):
        session = self.make_session()
        session.vars[API_AGENT_PERSONA_SESSION_VAR] = "invalid"

        persona = get_or_create_api_agent_persona(session, "G01", "G01_API_01")

        self.assertIsInstance(session.vars[API_AGENT_PERSONA_SESSION_VAR], dict)
        self.assertEqual(
            session.vars[API_AGENT_PERSONA_SESSION_VAR]["G01_API_01"],
            persona,
        )

    def test_full_initialization_is_deterministic_for_all_groups_and_agents(self):
        first_session = self.make_session()
        second_session = self.make_session()

        first = initialize_api_agent_personas(
            first_session,
            group_labels=["G01", "G02"],
            agent_count=2,
        )
        second = initialize_api_agent_personas(
            second_session,
            group_labels=["G01", "G02"],
            agent_count=2,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            sorted(first),
            ["G01_API_01", "G01_API_02", "G02_API_01", "G02_API_02"],
        )

    def test_incomplete_existing_snapshot_is_replaced(self):
        session = self.make_session()
        session.vars[API_AGENT_PERSONA_SESSION_VAR] = {
            "G01_API_01": {"persona_version": PERSONA_LIBRARY_VERSION}
        }

        persona = get_or_create_api_agent_persona(session, "G01", "G01_API_01")

        self.assertIn("persona_id", persona)
        self.assertIn("label", persona)
        self.assertIn("traits", persona)


if __name__ == "__main__":
    unittest.main()
