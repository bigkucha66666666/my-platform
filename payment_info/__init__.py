from otree.api import *



doc = """
This application provides a webpage instructing participants how to get paid.
Examples are given for the lab and Amazon Mechanical Turk (AMT).
"""


class C(BaseConstants):
    NAME_IN_URL = 'payment_info'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 1
    PAYMENT_TIMEOUT_SECONDS = 30
    DROPOUT_TIMEOUT_SECONDS = 1


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
    def get_timeout_seconds(player: Player):
        if getattr(player.participant, 'is_dropout', False):
            return C.DROPOUT_TIMEOUT_SECONDS
        return C.PAYMENT_TIMEOUT_SECONDS

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        participant = player.participant
        player.final_total_payoff = participant.vars.get(
            'route_choice_total_payoff', participant.payoff
        )
        participant.finished = True

    @staticmethod
    def vars_for_template(player: Player):
        participant = player.participant
        total_payoff = participant.vars.get('route_choice_total_payoff', participant.payoff)
        return dict(
            redemption_code=participant.label or participant.code,
            total_payoff=total_payoff,
            auto_advance_seconds=PaymentInfo.get_timeout_seconds(player),
        )


page_sequence = [PaymentInfo]
