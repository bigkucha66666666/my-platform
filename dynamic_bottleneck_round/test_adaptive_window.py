"""Window/cost contracts use temporary Session fixtures, never a live database."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dynamic_bottleneck_round as app


class AdaptiveWindowTests(unittest.TestCase):
    def factory(self):
        factory = getattr(app, 'new_experiment_parameters', None)
        self.assertIsNotNone(factory, 'new Sessions need versioned cost/window parameters')
        return factory()

    def session(self):
        return SimpleNamespace(config={}, vars={
            'dynamic_bottleneck_experiment_parameters_v2': self.factory(),
        })

    def test_window_uses_0800_arrival_minus_free_flow_and_actual_actor_count(self):
        for count, first, last in ((1, 473, 475), (6, 472, 475),
                                    (9, 471, 475), (12, 470, 475), (30, 464, 477)):
            with self.subTest(actors=count):
                schedule = app.static_departure_schedule(actor_count=count)
                self.assertEqual(schedule['first_departure_minute'], first)
                self.assertEqual(schedule['last_departure_minute'], last)
                self.assertEqual(schedule['num_slots'], last - first + 1)
                self.assertLess(schedule['first_departure_minute'], 480)
                self.assertGreater(schedule['last_departure_minute'], 474)
                self.assertAlmostEqual(schedule['raw_end_minute'] - schedule['raw_start_minute'],
                                       count / 2.665)

    def test_new_late_rate_is_four_and_free_flow_still_six(self):
        self.assertEqual(app.C.LATE_COST_PER_MINUTE, 4)
        self.assertEqual(app.C.PREFERRED_ARRIVAL_MINUTE, 480)
        self.assertEqual(app.C.FREE_FLOW_TRAVEL_MINUTES, 6)
        components = app.calculate_cost_components(queue_delay=2, early_minutes=0, late_minutes=3)
        self.assertEqual(components['late_cost'], 12)
        self.assertEqual(components['total_cost'], 16)

    def test_actual_group_actor_count_includes_llm_and_rl(self):
        session = self.session()
        h = [SimpleNamespace(participant=SimpleNamespace(vars={})) for _ in range(9)]
        ha = [SimpleNamespace(participant=SimpleNamespace(vars={})) for _ in range(3)]
        with patch.object(app, 'effective_group_actor_count', side_effect=(9, 9)) as count:
            app.apply_fixed_departure_schedules(session, [h, ha])
        self.assertEqual([call.args[1] for call in count.call_args_list], [9, 3])
        for player in h + ha:
            schedule = player.participant.vars[app.DEPARTURE_SCHEDULE_VAR]
            self.assertEqual(schedule['players_count'], 9)
            self.assertEqual((schedule['first_departure_time'], schedule['last_departure_time']),
                             ('07:51', '07:55'))

    def test_invalid_population_and_capacity_are_rejected(self):
        calculator = getattr(app, 'calculate_departure_window', None)
        self.assertIsNotNone(calculator)
        for count in (0, -1, 2.5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                calculator(actor_count=count, mean_capacity=2.665)
        for capacity in (0, -1, float('nan'), float('inf')):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                calculator(actor_count=9, mean_capacity=capacity)

    def test_saved_legacy_window_and_costs_are_preserved(self):
        schedule = dict(enabled=True, first_departure_minute=466, last_departure_minute=481,
                        first_departure_time='07:46', last_departure_time='08:01', num_slots=16)
        player = SimpleNamespace(session=SimpleNamespace(vars={}),
            participant=SimpleNamespace(vars={app.DEPARTURE_SCHEDULE_VAR: schedule}),
            queue_delay_minutes=0, early_minutes=0, late_minutes=2, coarse_toll_charge=0)
        self.assertEqual(app.departure_schedule_for_player(player), schedule)
        self.assertEqual(app.result_cost_components(player)['late_cost'], 6)

    def test_new_result_display_uses_saved_costs(self):
        player = SimpleNamespace(session=self.session(), queue_delay_minutes=0,
                                 early_minutes=0, late_minutes=2, coarse_toll_charge=0)
        self.assertEqual(app.result_cost_components(player)['late_cost'], 8)


def check_adaptive_market_orm():
    """Build genuine pilot markets; use no real provider or live database."""
    import os
    import json
    import time
    from otree.main import setup
    setup()
    from otree.database import db
    from otree.session import create_session
    from .experiment_parameters import ADAPTIVE_BANK_FINGERPRINTS_VAR

    def provider(*, config, choice_set):
        return app.AgentChoice(departure_slot=choice_set.available_slots[0]['slot'],
                               decision_source='deepseek_api', fallback_used=False)

    with patch.dict(os.environ, {'DYNAMIC_ROOM_RELEASE_PROTOCOL': ''}):
        sessions = [create_session('dynamic_bottleneck_round_prod', num_participants=12,
            modified_session_config_fields={
                'pilot_mode_enabled': 1,
                'pilot_group_spec': (f'G01:H-{condition},human=9,api=0,rl=0;'
                                    f'G02:HA-{condition},human=3,api=3,rl=3'),
                'api_agent_mode': 'active', 'rl_agent_enabled': 1,
            }) for condition in ('I0', 'I1')]
    for session in sessions:
        assert '2' in session.vars[ADAPTIVE_BANK_FINGERPRINTS_VAR]
        for raw in (1, 4, 5, 33):
            groups = app.Group.objects_filter(session=session, round_number=raw)
            for group in groups:
                players = group.get_players()
                for player in players:
                    schedule = app.departure_schedule_for_player(player)
                    assert schedule['players_count'] == 9
                    assert (schedule['first_departure_minute'], schedule['last_departure_minute']) == (471, 475)
                    assert app.departure_slot_for_minute(470, schedule) is None
                    assert app.departure_slot_for_minute(474, schedule) is not None
                reference = players[0]
                introduction = app.Introduction.vars_for_template(reference)
                assert introduction['late_cost_per_minute'] == '4'
                assert introduction['preferred_arrival_time'] == '08:00'
                choice_set = app.api_agent_choice_set_for_group(group, reference, 'fixture', {})
                assert choice_set.cost_parameters['late_cost_per_minute'] == 4
                assert [item['departure_minute'] for item in choice_set.available_slots] == list(range(471, 476))
                if raw not in (4, 5):
                    continue
                for player in players:
                    app.set_player_departure_choice(player, 474)
                with patch.object(app, 'choose_agent_departure', side_effect=provider):
                    for _ in range(100):
                        if app._set_results_locked(group):
                            break
                        time.sleep(.02)
                    else:
                        raise AssertionError('pilot settlement did not complete')
                assert group.results_ready
                for player in players:
                    expected = (2 * player.queue_delay_minutes + player.early_minutes
                                + 4 * player.late_minutes)
                    assert abs(float(player.total_cost) - expected) < 1e-8
                if group.id_in_subsession == 2:
                    records = app.independent_rl_records_for_group(group)
                    assert len(records) == 3
                    assert all(471 <= record['departure_minute'] <= 475 for record in records)
                    assert all(json.loads(record['context_json'])['pretraining_bank_id'].startswith('adaptive_')
                               for record in records)
                    states, _ = app.independent_rl_state_store_for_group(group)
                    expected_bank = session.vars[ADAPTIVE_BANK_FINGERPRINTS_VAR]['2']['bank_id']
                    assert len(states) == 3
                    assert all(state['pretraining_bank_id'] == expected_bank
                               for state in states.values()), 'formal feedback must retain the adaptive bank'
                    assert all(state['rounds_observed'] == raw - 3
                               for state in states.values()), 'formal feedback must accumulate across rounds'
        db.commit()
    print('ADAPTIVE_MARKET_ORM I0/I1; H9 and HA(3+3+3); '
          'warmup/formal window freeze; human/LLM/RL costs4; two formal settlements and RL continuity OK', flush=True)


if __name__ == '__main__':
    unittest.main()
