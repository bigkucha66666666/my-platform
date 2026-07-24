import unittest
from types import SimpleNamespace
from unittest.mock import patch

import single_bottleneck as app
from single_bottleneck.agents.personas import API_AGENT_PERSONA_SESSION_VAR


class PersonaIntegrationTests(unittest.TestCase):
    def make_group_and_player(self, mode="active"):
        session = SimpleNamespace(
            code="SESSION_A",
            vars={},
            config={
                "api_agent_mode": mode,
                "api_agent_count_per_group": 1,
            },
        )
        group = SimpleNamespace(
            session=session,
            round_number=1,
            id_in_subsession=1,
        )
        player = SimpleNamespace(
            participant=SimpleNamespace(
                vars={"assigned_group_label": "G01"},
            )
        )
        return group, player

    def test_active_mode_assigns_persona_and_passes_it_to_choice_set(self):
        group, player = self.make_group_and_player()
        captured = []

        def fake_choice_set(_group, _player, *, agent_id="", persona=None):
            captured.append((agent_id, persona))
            return SimpleNamespace()

        choice = SimpleNamespace(
            departure_slot=11,
            decision_source="deepseek_api",
            fallback_used=False,
            latency_ms=12,
            reason="test",
            raw_response_json="{}",
            context_json='{"agent_id": "G01_API_01", "persona": {"persona_id": "adaptive_v1"}}',
        )

        with (
            patch.object(app, "api_agent_choice_set_for_group", side_effect=fake_choice_set),
            patch.object(app, "choose_shadow_departure", return_value=choice),
            patch.object(
                app.AgentDecision,
                "create",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch.object(app, "departure_minute_for_slot", return_value=474),
            patch.object(app, "minute_to_clock", return_value="07:54"),
        ):
            decisions = app.create_api_agent_decisions_for_group(
                group,
                [player],
                schedule={},
            )

        self.assertEqual(len(decisions), 1)
        self.assertIn(API_AGENT_PERSONA_SESSION_VAR, group.session.vars)
        self.assertEqual(captured[0][0], "G01_API_01")
        self.assertIn("persona_id", captured[0][1])
        self.assertEqual(decisions[0].context_json, choice.context_json)

    def test_off_mode_does_not_initialize_persona_store(self):
        group, player = self.make_group_and_player(mode="off")

        decisions = app.create_api_agent_decisions_for_group(
            group,
            [player],
            schedule={},
        )

        self.assertEqual(decisions, [])
        self.assertNotIn(API_AGENT_PERSONA_SESSION_VAR, group.session.vars)

    def test_first_active_group_initializes_personas_for_every_group(self):
        group, player = self.make_group_and_player()
        other_group, other_player = self.make_group_and_player()
        other_group.session = group.session
        other_group.id_in_subsession = 2
        other_player.participant.vars["assigned_group_label"] = "G02"

        subsession = SimpleNamespace(get_groups=lambda: [group, other_group])
        group.subsession = subsession
        other_group.subsession = subsession
        group.get_players = lambda: [player]
        other_group.get_players = lambda: [other_player]
        group.in_round = lambda _round_number: group
        other_group.in_round = lambda _round_number: other_group

        choice = SimpleNamespace(
            departure_slot=11,
            decision_source="deepseek_api",
            fallback_used=False,
            latency_ms=12,
            reason="test",
            raw_response_json="{}",
            context_json="{}",
        )

        with (
            patch.object(app, "api_agent_choice_set_for_group", return_value=SimpleNamespace()),
            patch.object(app, "choose_shadow_departure", return_value=choice),
            patch.object(
                app.AgentDecision,
                "create",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch.object(app, "departure_minute_for_slot", return_value=474),
            patch.object(app, "minute_to_clock", return_value="07:54"),
        ):
            app.create_api_agent_decisions_for_group(group, [player], schedule={})

        stored = group.session.vars[API_AGENT_PERSONA_SESSION_VAR]
        self.assertIn("G01_API_01", stored)
        self.assertIn("G02_API_01", stored)


if __name__ == "__main__":
    unittest.main()
