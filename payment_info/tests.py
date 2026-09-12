from otree.api import Bot, Submission, expect

from . import PaymentInfo, payoff_source_var


class PlayerBot(Bot):
    def play_round(self):
        yield Submission(PaymentInfo, check_html=False)
        expected_payoff = self.participant.vars.get(
            payoff_source_var(self.player),
            self.participant.payoff,
        )
        expect(self.player.final_total_payoff, '==', expected_payoff)
        expect(self.participant.finished, '==', True)
