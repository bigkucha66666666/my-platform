"""Cumulative ranking contracts; no real provider or existing database."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import unittest

import dynamic_bottleneck_round as app


class CumulativeRankingTests(unittest.TestCase):
    def calculate(self, costs, **kwargs):
        function = getattr(app, 'accumulate_rankings', None)
        self.assertIsNotNone(function, 'formal results need a cumulative ranking calculator')
        return function(costs, **kwargs)

    def test_cumulative_ranking_differs_from_last_round_and_matches_mean_order(self):
        first = self.calculate({'H1': 10, 'H2': 2, 'LLM': 3, 'RL': 9}, completed_rounds=1)
        second = self.calculate({'H1': 2, 'H2': 12, 'LLM': 7, 'RL': 3},
            completed_rounds=2, previous_totals=first['totals'])
        self.assertEqual(second['totals'], {'H1': '12', 'H2': '14', 'LLM': '10', 'RL': '12'})
        self.assertEqual({key: row['rank'] for key, row in second['rankings'].items()},
                         {'H1': 2, 'H2': 4, 'LLM': 1, 'RL': 2})
        self.assertEqual(second['rankings']['H1']['mean_cost'], '6.0000')
        self.assertEqual(second['rankings']['H1']['tied_count'], 2)
        self.assertEqual(second['actor_count'], 4)
        means = {key: Decimal(row['mean_cost']) for key, row in second['rankings'].items()}
        for key, value in means.items():
            self.assertEqual(second['rankings'][key]['rank'], 1 + sum(x < value for x in means.values()))

    def test_all_equal_and_single_actor(self):
        for costs in ({'H': 0}, {'H': 3, 'LLM': 3, 'RL': 3}):
            result = self.calculate(costs, completed_rounds=1)
            self.assertTrue(all(row['rank'] == 1 and row['tied_count'] == len(costs)
                                for row in result['rankings'].values()))

    def test_float_tail_ties_at_six_decimals_without_rounding_running_totals(self):
        first = self.calculate({'H': .1 + .2, 'RL': .3}, completed_rounds=1)
        self.assertEqual([row['rank'] for row in first['rankings'].values()], [1, 1])
        self.assertEqual(first['totals']['H'], '0.30000000000000004')
        second = self.calculate({'H': .000001, 'RL': 0}, completed_rounds=2,
                                previous_totals=first['totals'])
        self.assertEqual(second['rankings']['H']['rank'], 2)

    def test_invalid_costs_and_incomplete_history_are_rejected(self):
        for value in (-1, True, None, 'nan', 'Infinity'):
            with self.subTest(cost=value), self.assertRaises(ValueError):
                self.calculate({'H': value}, completed_rounds=1)
        for kwargs in ({'completed_rounds': 0}, {'completed_rounds': True},
                       {'completed_rounds': 2},
                       {'completed_rounds': 1, 'previous_totals': {'H': 1}},
                       {'completed_rounds': 2, 'previous_totals': {'Other': 1}}):
            with self.subTest(arguments=kwargs), self.assertRaises(ValueError):
                self.calculate({'H': 1}, **kwargs)
        with self.assertRaises(ValueError):
            self.calculate({}, completed_rounds=1)

    def factory(self, *, raw_round=4, enabled=True, ready=True):
        rule = getattr(app, 'RANKING_RULE_VAR', 'ranking_not_implemented')
        version = getattr(app, 'RANKING_RULE_VERSION', 'ranking_not_implemented')
        session = SimpleNamespace(vars={rule: version} if enabled else {}, config={})
        players = [SimpleNamespace(participant=SimpleNamespace(code=code, vars={}),
            session=session, round_number=raw_round, total_cost=cost)
            for code, cost in (('A', 10), ('B', 2))]
        group = SimpleNamespace(session=session, round_number=raw_round,
            id_in_subsession=1, results_ready=ready, get_players=lambda: players)
        for player in players:
            player.group = group
            player.participant.vars[app.DEPARTURE_SCHEDULE_VAR] = {'enabled': True, 'players_count': 4}
        return group, players

    def save(self, group, players, virtual):
        function = getattr(app, 'save_cumulative_result_rankings', None)
        self.assertIsNotNone(function, 'settlement must freeze private ranking results')
        return function(group, players=players, virtual_records=virtual)

    def read(self, player):
        function = getattr(app, 'cumulative_ranking_for_player', None)
        self.assertIsNotNone(function, 'result pages need their own frozen ranking')
        return function(player)

    def virtual(self, llm, rl):
        return [{'actor_type': app.API_AGENT_TYPE_DEEPSEEK, 'agent_id': 'LLM', 'total_cost': llm},
                {'actor_type': app.RL_AGENT_TYPE, 'agent_id': 'RL', 'total_cost': rl}]

    def test_private_snapshots_survive_later_round_and_groups_are_isolated(self):
        group, players = self.factory()
        self.assertTrue(self.save(group, players, self.virtual(3, 9)))
        first = deepcopy(self.read(players[0]))
        self.assertEqual(first['rank'], 4)
        self.assertEqual(first['completed_rounds'], 1)
        self.assertNotIn('totals', first)
        group.round_number = 5
        for player, cost in zip(players, (2, 12)):
            player.round_number = 5
            player.total_cost = cost
        self.assertTrue(self.save(group, players, self.virtual(7, 3)))
        self.assertEqual(self.read(players[0])['rank'], 2)
        self.assertEqual(self.read(players[0])['mean_cost'], '6.0000')
        self.assertEqual(self.read(players[0])['tied_count'], 2)
        other_group, others = self.factory()
        self.save(other_group, others, self.virtual(20, 30))
        self.assertEqual(self.read(others[0])['rank'], 2)
        players[0].round_number = 4
        self.assertEqual(self.read(players[0]), first)

    def test_warmup_and_legacy_skip_rankings_and_unsettled_cannot_reveal_them(self):
        for raw, enabled in ((3, True), (4, False)):
            group, players = self.factory(raw_round=raw, enabled=enabled)
            before = deepcopy([p.participant.vars for p in players])
            self.assertFalse(self.save(group, players, self.virtual(3, 9)))
            self.assertEqual(self.read(players[0]), {'enabled': False})
            self.assertEqual([p.participant.vars for p in players], before)
        group, players = self.factory()
        self.save(group, players, self.virtual(3, 9))
        group.results_ready = False
        self.assertFalse(self.read(players[0])['available'])

    def test_missing_previous_round_or_actor_does_not_invent_zero_cost_rank(self):
        group, players = self.factory(raw_round=5)
        self.assertFalse(self.save(group, players, self.virtual(3, 9)))
        self.assertEqual(self.read(players[0]), {'enabled': True, 'available': False})
        group, players = self.factory()
        self.assertFalse(self.save(group, players, self.virtual(3, 9)[:1]))
        self.assertFalse(self.read(players[0])['available'])

    def test_repeated_snapshot_is_idempotent_and_does_not_change_costs_or_payment_vars(self):
        group, players = self.factory()
        for player in players:
            player.payoff = 123
            player.participant.vars['dynamic_bottleneck_cash_total_cents'] = 2345
        self.save(group, players, self.virtual(3, 9))
        before = deepcopy([p.participant.vars for p in players])
        self.assertTrue(self.save(group, players, self.virtual(3, 9)))
        self.assertEqual([p.participant.vars for p in players], before)
        self.assertEqual([p.total_cost for p in players], [10, 2])
        self.assertEqual([p.payoff for p in players], [123, 123])


def check_cumulative_ranking_orm(http_request):
    """Actual H9/HA9 settlements and result-page HTTP in a temporary database."""
    import asyncio
    import json
    import os
    from pathlib import Path
    import time
    from unittest.mock import patch
    from otree.main import setup
    setup()
    import project_asgi
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant
    from otree.session import create_session

    def provider(*, config, choice_set):
        return app.AgentChoice(departure_slot=choice_set.available_slots[-1]['slot'],
                               decision_source='deepseek_api', fallback_used=False)

    with patch.dict(os.environ, {'DYNAMIC_ROOM_RELEASE_PROTOCOL': ''}):
        session = create_session('dynamic_bottleneck_round_prod', num_participants=12,
            modified_session_config_fields={
                'pilot_mode_enabled': 1,
                'pilot_group_spec': 'G01:H-I1,human=9,api=0,rl=0;G02:HA-I1,human=3,api=3,rl=3',
                'api_agent_mode': 'active', 'rl_agent_enabled': 1,
            })
    assert session.vars[app.RANKING_RULE_VAR] == app.RANKING_RULE_VERSION
    totals_by_group, first_snapshots = {}, {}

    async def results_html(player):
        index = next(i for i, info in _get_session_lookups(session.code).items()
                     if info.round_number == player.round_number and info.page_class.__name__ == 'Results')
        code = player.participant.code
        db.commit()
        db.new_session()
        pp = Participant.objects_get(code=code)
        pp._index_in_pages = index
        pp.visited = True
        pp.vars['access_granted'] = True
        pp.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        pp._last_page_timestamp = int(time.time())
        db.commit()
        path = f'/p/{pp.code}/dynamic_bottleneck_round/Results/{index}'
        status, html, error = await http_request(project_asgi.app, path)
        current = Participant.objects_get(code=pp.code)
        assert status == 200, (status, error, path, current._index_in_pages, current._url_i_should_be_on())
        return html

    for raw in (1, 4, 5):
        for group in app.Group.objects_filter(session=session, round_number=raw):
            players = group.get_players()
            for i, player in enumerate(players):
                app.set_player_departure_choice(player, (472, 473, 474)[(i + raw) % 3])
            with patch.object(app, 'choose_agent_departure', side_effect=provider):
                for _ in range(100):
                    if app._set_results_locked(group):
                        break
                    time.sleep(.02)
                else:
                    raise AssertionError('mixed ranking fixture failed to settle')
            if raw == 1:
                assert app.cumulative_ranking_for_player(players[0]) == {'enabled': False}
                assert 'id="cumulative-ranking"' not in asyncio.run(results_html(players[0]))
                continue
            actual = {f'human:{p.participant.code}': Decimal(str(p.total_cost)) for p in players}
            virtual = app.active_virtual_decisions_for_group(group)
            actual.update({f"{r['actor_type']}:{r['agent_id']}": Decimal(str(r['total_cost'])) for r in virtual})
            assert len(actual) == 9
            previous = totals_by_group.get(group.id_in_subsession, dict.fromkeys(actual, Decimal(0)))
            totals = {key: previous[key] + value for key, value in actual.items()}
            totals_by_group[group.id_in_subsession] = totals
            comparison = {key: value.quantize(Decimal('.000001'), rounding='ROUND_HALF_UP') for key, value in totals.items()}
            protected_before = [(p.total_cost, float(p.payoff), float(p.reward_bonus), float(p.coarse_toll_charge)) for p in players]
            for player in players:
                key = f'human:{player.participant.code}'
                ranking = app.cumulative_ranking_for_player(player)
                assert ranking['available']
                assert ranking['rank'] == 1 + sum(value < comparison[key] for value in comparison.values())
                assert ranking['tied_count'] == sum(value == comparison[key] for value in comparison.values())
                assert ranking['completed_rounds'] == raw - 3
                assert ranking['actor_count'] == 9
                assert Decimal(ranking['mean_cost']) == (totals[key] / (raw - 3)).quantize(Decimal('.0001'), rounding='ROUND_HALF_UP')
            own = players[0]
            html = asyncio.run(results_html(own))
            assert 'id="cumulative-ranking"' in html, 'formal Results must render personal cumulative ranking'
            own_ranking = app.cumulative_ranking_for_player(own)
            assert f'<span class="rank-position">{own_ranking["rank"]}/{own_ranking["actor_count"]}</span>' in html
            assert '截至本轮平均成本' in html and '9 个主体' in html
            assert ('含真人和 Agent' in html) == bool(virtual)
            assert all(r['agent_id'] not in html for r in virtual), 'result page must not expose other identities'
            if raw == 4:
                first_snapshots[group.id_in_subsession] = deepcopy(app.cumulative_ranking_for_player(own))
            else:
                historical = own.in_round(4)
                assert app.cumulative_ranking_for_player(historical) == first_snapshots[group.id_in_subsession]
                preview_dir = os.environ.get('CUMULATIVE_RANKING_PREVIEW_DIR')
                if preview_dir:
                    target = Path(preview_dir)
                    target.mkdir(parents=True, exist_ok=True)
                    (target / f'results_group{group.id_in_subsession}.html').write_text(html, encoding='utf-8')
            assert app._set_results_locked(group), 'replay should reuse settled results'
            assert [(p.total_cost, float(p.payoff), float(p.reward_bonus), float(p.coarse_toll_charge)) for p in players] == protected_before
            assert 'totals' not in app.cumulative_ranking_for_player(own)
            public = json.dumps(app.current_public_feedback_for_group(group))
            assert 'rank' not in public, 'private ranking must not change public Agent feedback'
    print('CUMULATIVE_RANKING_ORM H9/HA9; warmup hidden; two formal rounds; '
          'actual HTTP; ties/means/history/privacy/payoffs OK', flush=True)


if __name__ == '__main__':
    unittest.main()
