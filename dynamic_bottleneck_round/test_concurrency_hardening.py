"""Concurrency regressions without a live database or paid Agent calls."""
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import dynamic_bottleneck_round as app


class GroupAgentIsolationTests(unittest.TestCase):
    def setUp(self):
        session = SimpleNamespace(
            code='local_concurrency_test', vars={},
            config={'name': 'dynamic_bottleneck_round_prod',
                    'group_treatment_spec': 'G01:H-I0;G02:HA-I0'},
        )
        app.configure_formal_treatments(session)
        self.group = SimpleNamespace(
            session=session, id_in_subsession=1, round_number=4,
            get_players=Mock(return_value=[]),
        )

    def test_zero_llm_group_does_not_start_shared_prefetch(self):
        self.assertEqual(app.api_agent_mode(self.group.session), 'active')
        self.assertEqual(app.api_agent_count_per_group(self.group.session, 1), 0)
        with (
            patch.object(app, 'active_agent_decisions_for_group', return_value=[]) as lookup,
            patch.object(app.prefetch_coordinator, 'read_result', return_value=None) as read,
            patch.object(app.prefetch_coordinator, 'try_claim', return_value=None) as claim,
        ):
            self.assertIsNone(app.start_api_agent_prefetch(self.group))
        lookup.assert_not_called()
        read.assert_not_called()
        claim.assert_not_called()

    def test_zero_llm_collector_returns_empty_not_pending(self):
        with (
            patch.object(app, 'active_agent_decisions_for_group', return_value=[]) as lookup,
            patch.object(app, 'start_api_agent_prefetch', return_value=None) as start,
            patch.object(app.prefetch_coordinator, 'read_result', return_value=None) as read,
            patch.object(app, '_API_AGENT_PREFETCH_TASKS', {}),
        ):
            self.assertEqual(app.collect_api_agent_prefetch(self.group), [])
        lookup.assert_not_called()
        start.assert_not_called()
        read.assert_not_called()

    def test_zero_llm_synchronous_wrapper_skips_all_agent_work(self):
        with (
            patch.object(app, 'active_agent_decisions_for_group', return_value=[]) as lookup,
            patch.object(app, 'start_api_agent_prefetch', return_value=None) as start,
            patch.object(app, 'collect_api_agent_prefetch', return_value=[]) as collect,
        ):
            self.assertEqual(app.create_api_agent_decisions_for_group(self.group), [])
        lookup.assert_not_called()
        start.assert_not_called()
        collect.assert_not_called()

    def test_zero_rl_group_skips_player_and_state_queries(self):
        self.assertTrue(app.rl_agent_enabled(self.group.session))
        self.assertEqual(app.rl_agent_count_per_group(self.group.session, 1), 0)
        with patch.object(app, 'independent_rl_records_for_group', return_value=[]) as lookup:
            self.assertEqual(app.prepare_independent_rl_decisions_for_group(self.group), [])
        lookup.assert_not_called()
        self.group.get_players.assert_not_called()

    def test_human_group_settles_beside_ha_for_both_information_conditions(self):
        for condition in ('I0', 'I1'):
            with self.subTest(condition=condition):
                session = SimpleNamespace(
                    code='local_h_' + condition, vars={},
                    config={'name': 'dynamic_bottleneck_round_prod',
                            'group_treatment_spec': f'G01:H-{condition};G02:HA-{condition}'},
                )
                app.configure_formal_treatments(session)
                players = []
                for _ in range(30):
                    player = SimpleNamespace(
                        participant=SimpleNamespace(vars={}), departure_minute=474,
                    )
                    player.field_maybe_none = lambda name, p=player: getattr(p, name, None)
                    players.append(player)
                group = SimpleNamespace(
                    session=session, id_in_subsession=1, round_number=4,
                    dynamic_capacity=3, information_condition=condition,
                    results_ready=False, get_players=lambda: players,
                )
                with patch.object(app, 'choose_api_agent_departures') as api:
                    self.assertTrue(app._set_results_locked(group))
                    first_costs = [p.total_cost for p in players]
                    self.assertTrue(app._set_results_locked(group))
                api.assert_not_called()
                self.assertTrue(group.results_ready)
                # 30/3 = 10 minutes queued; arrive08:10;2*10+3*10=50.
                self.assertEqual(first_costs, [50.0] * 30)
                self.assertEqual([p.total_cost for p in players], first_costs)
                snapshot = app.current_public_feedback_for_group(group)
                self.assertEqual(sum(r['participant_count'] for r in snapshot['departure_outcomes']), 30)
                self.assertEqual(snapshot['group_average_cost'], 50.0)
                self.assertEqual(app.active_virtual_decisions_for_group(group), [])


class SettledGroupFastPathTests(unittest.TestCase):
    def test_settled_group_never_restarts_or_fills_choices(self):
        group = SimpleNamespace(results_ready=True)
        with patch.object(app, 'maybe_start_round', return_value=True) as start:
            app.maybe_prepare_results(group)
        start.assert_not_called()

    def test_ready_page_queries_waiting_players_once(self):
        waiting = [SimpleNamespace(round_start_ready=True),
                   SimpleNamespace(round_start_ready=False)]
        group = SimpleNamespace(round_started=False, round_start_deadline_ts=200)
        player = SimpleNamespace(group=group, round_number=4)
        with (
            patch.object(app, 'mark_round_ready', return_value=False) as mark,
            patch.object(app, 'maybe_start_round', return_value=False) as start,
            patch.object(app, 'round_waiting_players', return_value=waiting) as lookup,
            patch.object(app.time, 'time', return_value=100),
        ):
            context = app.RoundStartSync.vars_for_template(player)
        self.assertEqual((context['ready_count'], context['group_size']), (1, 2))
        mark.assert_called_once_with(player)
        start.assert_not_called()
        lookup.assert_called_once_with(group)


class ResultSnapshotReuseTests(unittest.TestCase):
    def setUp(self):
        self.schedule = {
            'enabled': True, 'num_slots': 3, 'slot_size_minutes': 1,
            'first_departure_minute': 473, 'last_departure_minute': 475,
        }
        self.snapshot = {
            'departure_outcomes': [
                {'slot': 1, 'departure_time': '07:53', 'participant_count': 3,
                 'average_cost': 16},
                {'slot': 2, 'departure_time': '07:54', 'participant_count': 1,
                 'average_cost': 8},
                {'slot': 3, 'departure_time': '07:55', 'participant_count': 0,
                 'average_cost': None},
            ],
            'group_average_cost': 14,
        }
        participant = SimpleNamespace(vars={app.PUBLIC_FEEDBACK_PARTICIPANT_VAR: {
            '4': self.snapshot,
        }})
        self.player = SimpleNamespace(
            participant=participant, departure_slot=1, departure_minute=473,
            round_number=4, total_cost=16, session=SimpleNamespace(vars={}),
        )
        self.player.field_maybe_none = lambda field: getattr(self.player, field, None)
        self.group = SimpleNamespace(
            results_ready=True, round_number=4,
            get_players=Mock(return_value=[self.player]),
        )
        self.player.group = self.group

    def test_departure_chart_uses_settled_anonymous_snapshot(self):
        with (
            patch.object(app, 'departure_schedule_for_player', return_value=self.schedule),
            patch.object(app, 'active_virtual_decisions_for_group', return_value=[]) as virtual,
        ):
            chart = app.group_departure_distribution(self.group)
        self.assertEqual(chart, [
            {'slot': 1, 'time': '07:53', 'count': 3, 'height_pct': 100.0},
            {'slot': 2, 'time': '07:54', 'count': 1, 'height_pct': 33.33},
            {'slot': 3, 'time': '07:55', 'count': 0, 'height_pct': 0.0},
        ])
        virtual.assert_not_called()
        self.group.get_players.assert_called_once_with()

    def test_old_session_without_snapshot_keeps_human_and_virtual_distribution(self):
        self.player.participant.vars.clear()
        with (
            patch.object(app, 'departure_schedule_for_player', return_value=self.schedule),
            patch.object(app, 'active_virtual_decisions_for_group', return_value=[
                {'departure_slot': 1}, {'departure_slot': 1}, {'departure_slot': 2},
            ]),
        ):
            chart = app.group_departure_distribution(self.group)
        self.assertEqual([row['count'] for row in chart], [3, 1, 0])

    def test_cost_chart_accepts_shared_snapshot_without_second_lookup(self):
        with (
            patch.object(app, 'departure_schedule_for_player', return_value=self.schedule),
            patch.object(app, 'current_public_feedback_for_group') as lookup,
        ):
            chart = app.result_current_round_cost_snapshot(
                self.player, public_snapshot=self.snapshot,
            )
        self.assertEqual(chart['average_cost_label'], '14')
        self.assertEqual(chart['bars'][0]['average_cost_label'], '16')
        self.assertEqual([row['is_current'] for row in chart['bars']], [True, False, False])
        lookup.assert_not_called()
        self.group.get_players.assert_not_called()

    def test_results_page_reads_public_snapshot_once_for_both_charts(self):
        self.player.dynamic_capacity = 3
        self.player.capacity_level = 'medium'
        self.player.previous_round_capacity = 2
        self.player.departure_time_label = '07:53'
        self.player.arrival_time_label = '08:03'
        self.player.queue_delay_minutes = 4
        self.player.travel_time_minutes = 10
        self.player.early_minutes = 0
        self.player.late_minutes = 3
        self.player.reward_bonus = 0
        self.player.payoff = 123
        self.player.slot_load = 3
        components = {name: 0 for name in (
            'fixed_cost', 'queue_cost', 'early_cost', 'late_cost', 'toll_cost', 'total_cost',
        )}
        with (
            patch.object(app, 'departure_schedule_for_player', return_value=self.schedule),
            patch.object(app, 'result_cost_components', return_value=components),
            patch.object(app, 'coarse_toll_description_for_player', return_value='无'),
            patch.object(app, 'active_virtual_decisions_for_group', return_value=[]) as virtual,
            patch.object(app, 'current_public_feedback_for_group',
                         wraps=app.current_public_feedback_for_group) as lookup,
        ):
            context = app.Results.vars_for_template(self.player)
        self.assertEqual([row['count'] for row in context['departure_distribution']], [3, 1, 0])
        self.assertEqual(context['cost_snapshot_average_cost_label'], '14')
        self.assertEqual(context['auto_advance_seconds'], 50)
        lookup.assert_called_once_with(self.group)
        virtual.assert_not_called()
        self.group.get_players.assert_called_once_with()


class DecisionDeadlineRegressionTests(unittest.TestCase):
    def test_submission_boundary_uses_unchanged_shared_deadline(self):
        group = SimpleNamespace(
            results_ready=False, round_started=True,
            round_started_at_ts=100, decision_deadline_ts=160,
        )
        for now, expected in ((159.999, False), (160, True), (160.001, True)):
            with self.subTest(now=now):
                self.assertEqual(app.decision_submission_closed(group, now), expected)
        self.assertEqual(app.C.DECISION_TIMEOUT_SECONDS, 60)
        self.assertEqual(group.decision_deadline_ts, 160)

    def test_pending_results_do_not_extend_decision_deadline(self):
        group = SimpleNamespace(results_ready=False, decision_deadline_ts=160)
        with (
            patch.object(app, 'maybe_start_round', return_value=True),
            patch.object(app, 'all_players_have_choice', return_value=False),
            patch.object(app, 'fill_missing_choices') as fill,
            patch.object(app, 'set_results', return_value=False) as settle,
            patch.object(app.time, 'time', return_value=160),
        ):
            app.maybe_prepare_results(group)
            app.maybe_prepare_results(group)
        self.assertFalse(group.results_ready)
        self.assertEqual(group.decision_deadline_ts, 160)
        self.assertEqual(fill.call_count, 2)
        self.assertEqual(settle.call_count, 2)


class SyncTemplateRenderingTests(unittest.TestCase):
    def test_sync_templates_render_shared_script_with_installed_otree(self):
        from otree.templating.template import Template

        directory = Path(__file__).resolve().parent
        context = {
            'round_label': '正式第 1 轮', 'display_round_number': 1,
            'display_total_rounds': 30, 'ready_count': 30, 'group_size': 30,
            'remaining_seconds': 60, 'decision_seconds': 60, 'poll_interval_ms': 1500,
            'auto_continue_delay_ms': 200,
            'technical_anomaly': '',
        }
        for name in ('RoundStartSync.html', 'ResultsSync.html'):
            for ready in (False, True):
                with self.subTest(template=name, ready=ready):
                    template = Template((directory / name).read_text(encoding='utf-8'))
                    html = template.render({**context, 'round_started': ready,
                                            'results_ready': ready})
                    self.assertIn('dynamic_bottleneck_round/sync_poll.js', html)
                    self.assertIn(f'data-ready="{int(ready)}"', html)
                    self.assertIn('DynamicBottleneckSync.start(', html)


if __name__ == '__main__':
    unittest.main()
