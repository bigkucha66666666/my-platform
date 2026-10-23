"""Prospective hybrid cash rewards; no paid API or existing database."""
from copy import deepcopy
from decimal import Decimal
import inspect
import unittest

from payment_info import PaymentInfo, custom_export
from payment_info.cash_payment import calculate_cash_payment, cash_rule_for_version
from payment_info.test_cash_payment import FakeParticipant, FakePlayer
from dynamic_bottleneck_round.result_ranking import (
    RANKING_RESULTS_VAR, RANKING_RULE_VAR, RANKING_RULE_VERSION,
)

HYBRID = 'dynamic_cost_rank_v3'


def snapshot(cost='600', rank=1, tied=1, count=9):
    return dict(rule_version=RANKING_RULE_VERSION, completed_rounds=30,
                actor_count=count, rank=rank, tied_count=tied,
                total_cost=str(cost), includes_agents=True)


class HybridRankFormulaTests(unittest.TestCase):
    def calculate(self, cost='600', rank=1, tied=1, count=9):
        self.assertIn('ranking', inspect.signature(calculate_cash_payment).parameters,
                      'rank rewards require the complete final snapshot')
        return calculate_cash_payment(total_cost=cost, formal_rounds=30,
            rule_version=HYBRID, ranking=snapshot(cost, rank, tied, count))

    def test_cost_and_rank_components_and_thirty_yuan_cap(self):
        best = self.calculate('0')
        self.assertEqual((best.base_cents, best.cost_bonus_cents,
                          best.rank_bonus_cents, best.total_cents), (1500, 1200, 300, 3000))
        self.assertEqual([self.calculate(rank=r).total_cents for r in (1, 5, 9)],
                         [2400, 2250, 2100])
        self.assertEqual(self.calculate('1200', rank=9).total_cents, 1500)
        rule = cash_rule_for_version(HYBRID)
        self.assertEqual(rule.budget_total_cents, 240000)

    def test_ties_share_occupied_prizes_and_all_tied_get_half(self):
        payment = self.calculate(rank=2, tied=2)
        self.assertEqual(payment.rank_midpoint, Decimal('2.5'))
        self.assertEqual(payment.rank_bonus_cents, 244)
        self.assertEqual(self.calculate(rank=1, tied=9).rank_bonus_cents, 150)
        self.assertEqual(self.calculate(count=1).rank_bonus_cents, 150)

    def test_percentile_scales_with_actual_market_size(self):
        self.assertEqual(self.calculate(rank=2, count=3).rank_bonus_cents, 150)
        self.assertEqual(self.calculate(rank=15, count=29).rank_bonus_cents, 150)
        self.assertEqual(self.calculate(rank=30, count=30).rank_bonus_cents, 0)

    def test_each_component_rounds_half_up_at_cent_and_sums_exactly(self):
        for cost, expected in (('600.499999', 600), ('600.5', 600), ('600.500001', 599)):
            with self.subTest(cost=cost):
                pay = self.calculate(cost, rank=5)
                self.assertEqual(pay.cost_bonus_cents, expected)
                self.assertEqual(pay.bonus_cents, pay.cost_bonus_cents + pay.rank_bonus_cents)
                self.assertEqual(pay.total_cents, 1500 + pay.bonus_cents)

    def test_incomplete_or_inconsistent_final_rank_cannot_be_paid(self):
        self.assertIn('ranking', inspect.signature(calculate_cash_payment).parameters)
        bad = [None, {}, snapshot('599'), snapshot(rank=0), snapshot(tied=10),
               snapshot(rank=9, tied=2), snapshot(count=True)]
        for field, value in (('completed_rounds', 29), ('rule_version', 'unknown'),
                             ('rank', True), ('tied_count', 0), ('total_cost', 'NaN')):
            item = snapshot()
            item[field] = value
            bad.append(item)
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValueError):
                calculate_cash_payment(total_cost='600', formal_rounds=30,
                                       rule_version=HYBRID, ranking=row)

    def test_historical_versions_keep_their_original_formula_without_rank(self):
        self.assertEqual(calculate_cash_payment(total_cost=600, formal_rounds=30).total_cents, 2500)
        self.assertEqual(calculate_cash_payment(total_cost=600, formal_rounds=30,
            rule_version='dynamic_cost_budget_v2').total_cents, 2000)


class HybridRankReceiptTests(unittest.TestCase):
    def make_player(self):
        participant = FakeParticipant(vars={
            'dynamic_bottleneck_round_total_cost_decimal': '600',
            'dynamic_bottleneck_round_total_cost': 600,
            'dynamic_bottleneck_round_mean_cost': 20,
            'dynamic_bottleneck_round_settled_formal_rounds': 30,
            'dynamic_bottleneck_round_total_payoff': 3600,
            RANKING_RESULTS_VAR: {'33': snapshot(rank=2, tied=2)},
            'dynamic_bottleneck_round_departure_schedule': {'players_count': 9},
        }, payoff=3600)
        player = FakePlayer(participant, cash_rule=HYBRID)
        player.session.vars = {RANKING_RULE_VAR: RANKING_RULE_VERSION}
        player.session.code = 'HYBRID_TEST'
        return player

    def test_receipt_native_and_export_agree_and_repeated_calls_are_safe(self):
        player = self.make_player()
        original = deepcopy(player.participant.vars[RANKING_RESULTS_VAR])
        for _ in range(2):
            context = PaymentInfo.vars_for_template(player)
            self.assertTrue(context['cash_settlement_ready'])
            self.assertTrue(context['cash_is_rank_based'])
            self.assertEqual(context['cash_total_label'], '23.44')
            self.assertEqual(context['cash_cost_bonus_label'], '6.00')
            self.assertEqual(context['cash_rank_bonus_label'], '2.44')
            self.assertIn('最高 30 元', context['cash_payment_description'])
        PaymentInfo.before_next_page(player, False)
        self.assertAlmostEqual(player.participant.payoff_plus_participation_fee(), 23.44)
        self.assertEqual(player.participant.vars[RANKING_RESULTS_VAR], original)
        headers, row = list(custom_export([player]))
        exported = dict(zip(headers, row))
        self.assertEqual(exported['cost_bonus_cny'], '6.00')
        self.assertEqual(exported['rank_bonus_cny'], '2.44')
        self.assertEqual(exported['rank_midpoint'], '2.5')
        self.assertEqual(exported['rank_actor_count'], 9)
        self.assertEqual(exported['total_cny'], exported['native_cash_total_cny'])
        self.assertEqual(exported['max_total_cny'], '30.00')
        self.assertEqual(exported['budget_total_cny'], '2400.00')

    def test_missing_final_rank_or_marker_requires_review_not_zero_cost_first_prize(self):
        for missing in ('snapshot', 'marker'):
            player = self.make_player()
            if missing == 'snapshot':
                player.participant.vars[RANKING_RESULTS_VAR] = {'32': snapshot()}
            else:
                player.session.vars.clear()
            context = PaymentInfo.vars_for_template(player)
            self.assertFalse(context['cash_settlement_ready'])
            self.assertEqual(player.participant.vars['dynamic_bottleneck_cash_payment_status'], 'manual_review')
            self.assertEqual(player.participant.payoff_plus_participation_fee(), 15)

    def test_new_formal_defaults_and_disclosure_match_receipt(self):
        import settings
        import dynamic_bottleneck_round as app
        formal = next(c for c in settings.SESSION_CONFIGS if c['name'] == 'dynamic_bottleneck_round_prod')
        self.assertEqual(formal['cash_payment_rule'], HYBRID)
        player = self.make_player()
        description = app.FormalStart.vars_for_template(player)['cash_payment_description']
        self.assertEqual(description, PaymentInfo.vars_for_template(player)['cash_payment_description'])
        for text in ('0–12 元', '0–3 元', '并列', 'Agent', '最高 30 元'):
            self.assertIn(text, description)

    def test_saved_market_size_must_agree_with_final_ranking(self):
        player = self.make_player()
        player.participant.vars['dynamic_bottleneck_round_departure_schedule']['players_count'] = 30
        self.assertFalse(PaymentInfo.vars_for_template(player)['cash_settlement_ready'])

    def test_human_only_group_receipt_does_not_claim_agents_are_present(self):
        player = self.make_player()
        player.participant.vars[RANKING_RESULTS_VAR]['33']['includes_agents'] = False
        context = PaymentInfo.vars_for_template(player)
        self.assertFalse(context.get('cash_rank_includes_agents', True))


def check_hybrid_payment_orm():
    """Settle all formal rounds in H/HA markets, independently recompute prizes."""
    import os
    import time
    from unittest.mock import patch
    from otree.main import setup
    setup()
    from otree.database import db
    from otree.session import create_session
    from otree.templating.template import Template
    from pathlib import Path
    import dynamic_bottleneck_round as app
    import payment_info

    def provider(*, config, choice_set):
        return app.AgentChoice(departure_slot=choice_set.available_slots[-1]['slot'],
            decision_source='deepseek_api', fallback_used=False)

    root = Path(__file__).resolve().parents[1]
    receipt = Template((root / 'payment_info/PaymentInfo.html').read_text())
    formal_template = Template((root / 'dynamic_bottleneck_round/FormalStart.html').read_text())
    for condition in ('I0', 'I1'):
        with patch.dict(os.environ, {'DYNAMIC_ROOM_RELEASE_PROTOCOL': ''}):
            session = create_session('dynamic_bottleneck_round_prod', num_participants=12,
                modified_session_config_fields={
                    'pilot_mode_enabled': 1,
                    'pilot_group_spec': f'G01:H-{condition},human=9,api=0,rl=0;G02:HA-{condition},human=3,api=3,rl=3',
                    'api_agent_mode': 'active', 'rl_agent_enabled': 1,
                })
        running = {}
        for raw in range(1, 34):
            for group in app.Group.objects_filter(session=session, round_number=raw):
                players = group.get_players()
                schedule = app.departure_schedule_for_player(players[0])
                for index, player in enumerate(players):
                    minute = schedule['first_departure_minute'] + (index + raw) % schedule['num_slots']
                    app.set_player_departure_choice(player, minute)
                with patch.object(app, 'choose_agent_departure', side_effect=provider):
                    for _ in range(100):
                        if app._set_results_locked(group):
                            break
                        time.sleep(.02)
                    else:
                        raise AssertionError('hybrid reward fixture did not settle')
                if raw <= 3:
                    continue
                actual = {f'human:{p.participant.code}': Decimal(str(p.total_cost)) for p in players}
                actual.update({f"{r['actor_type']}:{r['agent_id']}": Decimal(str(r['total_cost']))
                    for r in app.active_virtual_decisions_for_group(group)})
                prior = running.setdefault(group.id_in_subsession, dict.fromkeys(actual, Decimal(0)))
                for key, value in actual.items():
                    prior[key] += value
                if raw != 33:
                    continue
                values = {key: total.quantize(Decimal('.000001'), rounding='ROUND_HALF_UP')
                          for key, total in prior.items()}
                for final in players:
                    total = prior[f'human:{final.participant.code}']
                    comparison = values[f'human:{final.participant.code}']
                    rank = 1 + sum(value < comparison for value in values.values())
                    tied = sum(value == comparison for value in values.values())
                    mid = Decimal(rank) + Decimal(tied - 1) / 2
                    rank_cents = int((300 * (9 - mid) / 8).quantize(Decimal('1'), rounding='ROUND_HALF_UP'))
                    cost_cents = int(max(Decimal(0), 1200 - total).quantize(Decimal('1'), rounding='ROUND_HALF_UP'))
                    expected = Decimal(1500 + rank_cents + cost_cents) / 100
                    paid = payment_info.Player.objects_get(participant=final.participant, round_number=1)
                    protected = (final.total_cost, float(final.payoff),
                                 final.participant.vars[app.TOTAL_COST_DECIMAL_VAR])
                    for _ in range(2):
                        context = PaymentInfo.vars_for_template(paid)
                        assert context['cash_settlement_ready'], (condition, group.id_in_subsession, final.participant.vars)
                        assert context['cash_total_label'] == f'{expected:.2f}'
                    html = receipt.render(context)
                    assert 'id="cash-rank-breakdown"' in html and '最高 30 元' in html
                    assert f'<strong>{rank}/9</strong>' in html
                    assert f'¥{expected:.2f}' in html
                    disclosure = formal_template.render(app.FormalStart.vars_for_template(final.in_round(4)))
                    assert '成本奖金 0–12 元' in disclosure and '排名奖金 0–3 元' in disclosure
                    PaymentInfo.before_next_page(paid, False)
                    assert Decimal(final.participant.payoff_plus_participation_fee()) == expected
                    headers, row = list(custom_export([paid]))
                    exported = dict(zip(headers, row))
                    assert exported['total_cny'] == exported['native_cash_total_cny'] == f'{expected:.2f}'
                    assert exported['final_rank'] == rank and exported['rank_tied_count'] == tied
                    assert exported['rank_actor_count'] == 9
                    assert exported['budget_total_cny'] == '2400.00'
                    assert protected == (final.total_cost, float(final.payoff), final.participant.vars[app.TOTAL_COST_DECIMAL_VAR])
                # No second cash receipt is created for any virtual actor.
                assert len(list(payment_info.Player.objects_filter(session=session))) == 12
            db.commit()
        print(f'HYBRID_PAYMENT_ORM {condition}: H9/HA(3+3+3), 30 formal rounds, '
              'independent cost/rank/ties/cash/native/export/template checks passed', flush=True)
