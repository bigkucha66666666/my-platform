from otree.api import Bot, Submission, expect

from . import PaymentInfo, payoff_source_var


class PlayerBot(Bot):
    def play_round(self):
        if self.session.config.get('cash_payment_rule'):
            from payment_info.cash_payment import calculate_cash_payment

            preview_payment = calculate_cash_payment(
                total_cost=self.participant.vars[
                    'dynamic_bottleneck_round_total_cost_decimal'
                ],
                formal_rounds=self.participant.vars[
                    'dynamic_bottleneck_round_settled_formal_rounds'
                ],
            )
            expected_cash = f'¥{preview_payment.total_cents / 100:.2f}'
            expect(expected_cash, 'in', self.html)
            expect('基础', 'in', self.html)
            expect('绩效', 'in', self.html)
            expect(
                float(self.participant.payoff_plus_participation_fee()),
                '==',
                preview_payment.total_cents / 100,
            )
        yield Submission(PaymentInfo, check_html=False)
        expected_payoff = self.participant.vars.get(
            payoff_source_var(self.player),
            self.participant.payoff,
        )
        expect(self.player.final_total_payoff, '==', expected_payoff)
        if self.session.config.get('cash_payment_rule'):
            from payment_info.cash_payment import calculate_cash_payment

            payment = calculate_cash_payment(
                total_cost=self.participant.vars[
                    'dynamic_bottleneck_round_total_cost_decimal'
                ],
                formal_rounds=self.participant.vars[
                    'dynamic_bottleneck_round_settled_formal_rounds'
                ],
            )
            expect(
                self.participant.vars['dynamic_bottleneck_cash_payment_cents'],
                '==',
                payment.total_cents,
            )
            expect(
                self.participant.vars['dynamic_bottleneck_cash_bonus_cents'],
                '==',
                payment.bonus_cents,
            )
            expect(
                self.participant.vars['dynamic_bottleneck_cash_payment_status'],
                '==',
                'settled',
            )
            expect(
                float(self.participant.payoff_plus_participation_fee()),
                '==',
                payment.total_cents / 100,
            )
        expect(self.participant.finished, '==', True)
