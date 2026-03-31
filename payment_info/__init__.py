from otree.api import *



doc = """
This application provides a webpage instructing participants how to get paid.
Examples are given for the lab and Amazon Mechanical Turk (AMT).
"""


class C(BaseConstants):
    NAME_IN_URL = 'payment_info'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 1


class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    pass


class Player(BasePlayer):
    final_total_payoff = models.CurrencyField(initial=0)


# FUNCTIONS
# PAGES
class PaymentInfo(Page):
    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        participant = player.participant
        player.final_total_payoff = participant.vars.get(
            'route_choice_total_payoff', participant.payoff
        )

    @staticmethod
    def vars_for_template(player: Player):
        participant = player.participant
        total_payoff = participant.vars.get('route_choice_total_payoff', participant.payoff)
        return dict(
            redemption_code=participant.label or participant.code,
            total_payoff=total_payoff,
        )


page_sequence = [PaymentInfo]
