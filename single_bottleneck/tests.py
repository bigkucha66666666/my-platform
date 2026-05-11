from otree.api import Bot, Submission, expect

from . import C, Decision, Introduction, Results, ResultsSync, departure_slots_for_player


class PlayerBot(Bot):
    def play_round(self):
        if self.round_number == 1:
            yield Submission(Introduction, check_html=False)

        available_slots = departure_slots_for_player(self.player)
        chosen_slot = available_slots[(self.player.id_in_group + self.round_number - 2) % len(available_slots)]

        yield Submission(
            Decision,
            dict(departure_slot=chosen_slot),
            check_html=False,
        )

        yield Submission(ResultsSync, check_html=False)
        yield Submission(Results, check_html=False)

        expect(self.player.departure_slot, 'in', available_slots)
        expect(float(self.player.payoff), '>=', 0)

        if self.round_number == C.NUM_ROUNDS:
            expect('single_bottleneck_total_payoff', 'in', self.participant.vars)
