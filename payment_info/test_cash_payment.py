import math
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from payment_info import PaymentInfo, Player, custom_export
from payment_info.cash_payment import calculate_cash_payment


BUDGET_RULE = 'dynamic_cost_budget_v2'


class DynamicBudgetFormulaTests(unittest.TestCase):
    def calculate(self, mean_cost):
        self.assertIn('rule_version', inspect.signature(calculate_cash_payment).parameters,
                      'cash calculation must select a saved rule version')
        return calculate_cash_payment(total_cost=mean_cost * 30, formal_rounds=30,
                                      rule_version=BUDGET_RULE)

    def test_eighty_people_never_exceed_two_thousand_yuan(self):
        result = self.calculate(0)
        self.assertEqual(result.rule_version, BUDGET_RULE)
        self.assertEqual(result.base_cents, 1500)
        self.assertEqual(result.bonus_cents, 1000)
        self.assertEqual(result.total_cents * 80, 200000)

    def test_cost_sensitive_not_fixed_twenty_five(self):
        amounts = [self.calculate(cost).total_cents for cost in (0, 10, 20, 40, 60)]
        self.assertEqual(amounts, [2500, 2250, 2000, 1500, 1500])
        mixed = [self.calculate(i / 2).total_cents for i in range(80)]
        self.assertLessEqual(sum(mixed), 200000)
        self.assertEqual(mixed, sorted(mixed, reverse=True))

    def test_budget_rule_rounds_only_final_cent(self):
        result = self.calculate(20.09)
        self.assertEqual(result.total_cents, 1998)

    def test_budget_rule_exact_half_cent_rounds_up_and_neighbors_are_correct(self):
        # 604.2 / 30 / 4 leaves a 4.965-yuan bonus: half-up => 4.97,
        # whereas half-even would incorrectly give 4.96.
        for total_cost, expected in (('604.199999', 1997), ('604.2', 1997), ('604.200001', 1996)):
            with self.subTest(total_cost=total_cost):
                payment = calculate_cash_payment(total_cost=total_cost, formal_rounds=30,
                                                 rule_version=BUDGET_RULE)
                self.assertEqual(payment.total_cents, expected)

    def test_unknown_rule_is_not_silently_assigned_new_amount(self):
        self.assertIn('rule_version', inspect.signature(calculate_cash_payment).parameters)
        with self.assertRaises(ValueError):
            calculate_cash_payment(total_cost=0, formal_rounds=30, rule_version='unknown')

    def test_legacy_default_and_explicit_legacy_still_pay_old_amount(self):
        self.assertIn('rule_version', inspect.signature(calculate_cash_payment).parameters)
        old = calculate_cash_payment(total_cost=0, formal_rounds=30,
                                     rule_version='dynamic_cost_v1')
        self.assertEqual(old.total_cents, 3500)
        self.assertEqual(calculate_cash_payment(total_cost=0, formal_rounds=30).total_cents, 3500)


class DynamicCashFormulaTests(unittest.TestCase):
    def test_reference_cost_pays_twenty_five_yuan(self):
        result = calculate_cash_payment(total_cost=600, formal_rounds=30)
        self.assertEqual(result.total_cents, 2500)
        self.assertEqual(result.base_cents, 1500)
        self.assertEqual(result.bonus_cents, 1000)
        self.assertEqual(result.mean_cost, 20)

    def test_zero_cost_hits_thirty_five_yuan_cap(self):
        self.assertEqual(
            calculate_cash_payment(total_cost=0, formal_rounds=30).total_cents,
            3500,
        )

    def test_high_cost_hits_fifteen_yuan_floor(self):
        self.assertEqual(
            calculate_cash_payment(total_cost=1200, formal_rounds=30).total_cents,
            1500,
        )
        self.assertEqual(
            calculate_cash_payment(total_cost=9000, formal_rounds=30).total_cents,
            1500,
        )

    def test_cash_rounds_half_up_only_at_final_cent(self):
        self.assertEqual(
            calculate_cash_payment(total_cost=602.7, formal_rounds=30).total_cents,
            2496,
        )

    def test_lower_cost_never_pays_less(self):
        totals = [
            calculate_cash_payment(total_cost=cost, formal_rounds=30).total_cents
            for cost in (0, 300, 600, 900, 1200, 2000)
        ]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_invalid_or_incomplete_inputs_are_rejected(self):
        for cost, count in (
            (None, 30),
            (-1, 30),
            (math.inf, 30),
            (math.nan, 30),
            (600, 29),
        ):
            with self.subTest(cost=cost, count=count):
                with self.assertRaises(ValueError):
                    calculate_cash_payment(total_cost=cost, formal_rounds=count)


class FakeParticipant:
    def __init__(self, *, vars, payoff=0):
        self.vars = vars
        self.payoff = payoff
        self.code = 'PTEST'
        self.label = 'P001'
        self.finished = False

    def payoff_plus_participation_fee(self):
        return 15 + float(self.payoff) / 100


class FakePlayer:
    def __init__(self, participant, *, cash_rule='dynamic_cost_v1'):
        self.participant = participant
        self.session = SimpleNamespace(config={
            'cash_payment_rule': cash_rule,
            'payoff_source_var': 'dynamic_bottleneck_round_total_payoff',
            'payoff_rounds': 30,
            'final_payoff_label': '动态瓶颈服务率实验',
        })
        self._payoff = 0
        self.final_total_payoff = 0

    @property
    def payoff(self):
        return self._payoff

    @payoff.setter
    def payoff(self, value):
        self.participant.payoff += value - self._payoff
        self._payoff = value


class DynamicPaymentIntegrationTests(unittest.TestCase):
    def make_player(self, *, cost=600, points=3600):
        participant = FakeParticipant(
            vars={
                'dynamic_bottleneck_round_total_cost': cost,
                'dynamic_bottleneck_round_settled_formal_rounds': 30,
                'dynamic_bottleneck_round_total_payoff': points,
            },
            payoff=points,
        )
        return FakePlayer(participant)

    def test_final_page_and_native_payment_agree(self):
        player = self.make_player()
        context = PaymentInfo.vars_for_template(player)
        self.assertTrue(context['is_dynamic_cash'])
        self.assertEqual(context['cash_total_label'], '25.00')
        self.assertEqual(context['cash_base_label'], '15.00')
        self.assertEqual(context['cash_bonus_label'], '10.00')
        self.assertEqual(player.participant.payoff_plus_participation_fee(), 25)

        PaymentInfo.before_next_page(player, False)
        self.assertEqual(player.final_total_payoff, 3600)
        self.assertEqual(player.participant.vars['dynamic_bottleneck_cash_payment_cents'], 2500)
        self.assertEqual(player.participant.vars['dynamic_bottleneck_cash_bonus_cents'], 1000)
        self.assertEqual(player.participant.vars['dynamic_bottleneck_cash_payment_status'], 'settled')
        self.assertEqual(player.participant.payoff_plus_participation_fee(), 25)
        self.assertTrue(player.participant.finished)

    def test_final_settlement_is_idempotent(self):
        player = self.make_player()
        PaymentInfo.vars_for_template(player)
        first_adjustment = player.payoff
        PaymentInfo.vars_for_template(player)
        self.assertEqual(player.payoff, first_adjustment)
        PaymentInfo.before_next_page(player, False)
        PaymentInfo.before_next_page(player, False)
        self.assertEqual(player.payoff, first_adjustment)
        self.assertEqual(player.participant.payoff_plus_participation_fee(), 25)

    def test_exact_decimal_cost_record_wins_over_float_sum(self):
        player = self.make_player(cost=603.3000000000003)
        player.participant.vars['dynamic_bottleneck_round_total_cost_decimal'] = '603.30'

        context = PaymentInfo.vars_for_template(player)
        self.assertEqual(context['cash_total_label'], '24.95')
        self.assertEqual(player.participant.payoff_plus_participation_fee(), 24.95)

    def test_recovered_older_session_exports_mean_cost(self):
        player = self.make_player()
        del player.participant.vars['dynamic_bottleneck_round_total_cost']
        del player.participant.vars['dynamic_bottleneck_round_settled_formal_rounds']
        rounds = [
            SimpleNamespace(
                round_number=number,
                total_cost=20.11,
                group=SimpleNamespace(results_ready=True),
            )
            for number in range(4, 34)
        ]
        last_player = SimpleNamespace(in_all_rounds=lambda: rounds)
        player.participant.get_player = lambda app, round_number: last_player

        context = PaymentInfo.vars_for_template(player)
        self.assertEqual(context['cash_total_label'], '24.95')
        self.assertEqual(
            player.participant.vars['dynamic_bottleneck_round_mean_cost'],
            20.11,
        )
        player.session.code = 'SESSION1'
        headers, row = list(custom_export([player]))
        self.assertEqual(dict(zip(headers, row))['formal_mean_cost'], 20.11)

    def test_missing_cost_does_not_award_maximum_bonus(self):
        player = self.make_player()
        del player.participant.vars['dynamic_bottleneck_round_total_cost']
        context = PaymentInfo.vars_for_template(player)
        self.assertFalse(context['cash_settlement_ready'])

        PaymentInfo.before_next_page(player, False)
        self.assertEqual(
            player.participant.vars['dynamic_bottleneck_cash_payment_status'],
            'manual_review',
        )
        self.assertEqual(player.participant.payoff_plus_participation_fee(), 15)

    def test_incomplete_formal_count_cannot_trigger_cash_payment(self):
        player = self.make_player(cost=0)
        player.participant.vars['dynamic_bottleneck_round_settled_formal_rounds'] = 29

        context = PaymentInfo.vars_for_template(player)
        self.assertFalse(context['cash_settlement_ready'])
        self.assertEqual(player.participant.payoff_plus_participation_fee(), 15)
        self.assertEqual(
            player.participant.vars['dynamic_bottleneck_cash_payment_status'],
            'manual_review',
        )

    def test_other_scenarios_keep_original_points_rule(self):
        player = self.make_player()
        player.session.config.pop('cash_payment_rule')
        context = PaymentInfo.vars_for_template(player)
        self.assertFalse(context['is_dynamic_cash'])
        self.assertEqual(context['total_payoff'], 3600)

        PaymentInfo.before_next_page(player, False)
        self.assertEqual(player.final_total_payoff, 3600)
        self.assertEqual(player.payoff, 0)
        self.assertEqual(player.participant.payoff, 3600)

    def test_template_has_cash_receipt_and_preserves_legacy_branch(self):
        html = (Path(__file__).with_name('PaymentInfo.html')).read_text()
        self.assertIn('{{ if is_dynamic_cash }}', html)
        self.assertIn('{{ cash_total_label }}', html)
        self.assertIn('{{ cash_bonus_label }}', html)
        self.assertIn('{{ else }}', html)
        self.assertIn('{{ total_payoff }}', html)

    def test_export_contains_auditable_cash_breakdown_without_new_model_fields(self):
        player = self.make_player()
        player.session.code = 'SESSION1'
        PaymentInfo.before_next_page(player, False)
        headers, row = list(custom_export([player]))
        exported = dict(zip(headers, row))
        self.assertEqual(exported['participant_code'], 'PTEST')
        self.assertEqual(exported['formal_total_cost'], 600)
        self.assertEqual(exported['base_cny'], '15.00')
        self.assertEqual(exported['bonus_cny'], '10.00')
        self.assertEqual(exported['total_cny'], '25.00')
        self.assertEqual(exported['payment_status'], 'settled')
        self.assertFalse(hasattr(Player, 'final_cash_cents'))


class DynamicBudgetIntegrationTests(unittest.TestCase):
    def make_player(self, cost=600):
        participant = FakeParticipant(vars={
            'dynamic_bottleneck_round_total_cost': cost,
            'dynamic_bottleneck_round_settled_formal_rounds': 30,
            'dynamic_bottleneck_round_total_payoff': 3600,
        }, payoff=3600)
        player = FakePlayer(participant, cash_rule=BUDGET_RULE)
        player.session.code = 'BUDGET_TEST'
        return player

    def test_budget_receipt_native_payment_and_repeated_settlement_agree(self):
        player = self.make_player()
        for _ in range(2):
            context = PaymentInfo.vars_for_template(player)
            self.assertTrue(context['cash_settlement_ready'])
            self.assertEqual(context['cash_total_label'], '20.00')
            self.assertEqual(context['cash_bonus_label'], '5.00')
            self.assertEqual(context['payment_rule_version'], BUDGET_RULE)
            self.assertEqual(player.participant.payoff_plus_participation_fee(), 20)
        PaymentInfo.before_next_page(player, False)
        self.assertEqual(player.participant.vars['dynamic_bottleneck_cash_payment_cents'], 2000)

    def test_two_independent_forty_person_sessions_cannot_exceed_budget(self):
        players = [self.make_player(cost=0) for _ in range(80)]
        for index, player in enumerate(players):
            player.session.code = 'I0_ROOM' if index < 40 else 'I1_ROOM'
            PaymentInfo.vars_for_template(player)
        total = sum(player.participant.payoff_plus_participation_fee() for player in players)
        self.assertEqual(total, 2000)

    def test_payment_exports_version_cap_and_budget_scope(self):
        player = self.make_player(cost=0)
        PaymentInfo.before_next_page(player, False)
        headers, row = list(custom_export([player]))
        exported = dict(zip(headers, row))
        self.assertEqual(exported.get('payment_rule'), BUDGET_RULE)
        self.assertEqual(exported.get('max_bonus_cny'), '10.00')
        self.assertEqual(exported.get('max_total_cny'), '25.00')
        self.assertEqual(exported.get('budget_human_limit'), 80)
        self.assertEqual(exported.get('budget_total_cny'), '2000.00')
        legacy = FakePlayer(FakeParticipant(vars={}, payoff=0))
        legacy.session.code = 'OLD'
        legacy.participant.vars['dynamic_bottleneck_cash_payment_rule'] = 'dynamic_cost_v1'
        headers, row = list(custom_export([legacy]))
        old = dict(zip(headers, row))
        self.assertEqual(old.get('max_total_cny'), '35.00')
        self.assertEqual(old.get('budget_human_limit'), '')
        self.assertEqual(old.get('budget_total_cny'), '')

    def test_new_defaults_and_versioned_incentive_description(self):
        import settings
        import dynamic_bottleneck_round as app
        formal = next(c for c in settings.SESSION_CONFIGS if c['name'] == 'dynamic_bottleneck_round_prod')
        self.assertEqual(formal['cash_payment_rule'], 'dynamic_cost_rank_v3')
        player = self.make_player()
        context = PaymentInfo.vars_for_template(player)
        self.assertIn('最高 25 元', context.get('cash_payment_description', ''))
        self.assertIn('绩效报酬 0–10 元', context.get('cash_payment_description', ''))
        self.assertIn('平均成本为 20 时，合计为 20 元', context.get('cash_payment_description', ''))
        formal_context = app.FormalStart.vars_for_template(player)
        self.assertEqual(formal_context['cash_payment_description'], context['cash_payment_description'])
        player.session.config['cash_payment_rule'] = 'dynamic_cost_v1'
        old_context = app.FormalStart.vars_for_template(player)
        self.assertIn('最高 35 元', old_context['cash_payment_description'])

    def test_unknown_and_incomplete_rules_require_manual_review(self):
        for rule, count in ((BUDGET_RULE, 29), ('unknown', 30)):
            player = self.make_player(cost=0)
            player.session.config['cash_payment_rule'] = rule
            player.participant.vars['dynamic_bottleneck_round_settled_formal_rounds'] = count
            context = PaymentInfo.vars_for_template(player)
            self.assertFalse(context['cash_settlement_ready'])
            self.assertEqual(player.participant.vars['dynamic_bottleneck_cash_payment_status'], 'manual_review')
            self.assertEqual(player.participant.payoff_plus_participation_fee(), 15)


if __name__ == '__main__':
    unittest.main()
