from otree.api import *



doc = """
This application provides a webpage instructing participants how to get paid.
Examples are given for the lab and Amazon Mechanical Turk (AMT).
"""


class C(BaseConstants):
    NAME_IN_URL = 'payment_info'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 1
    PAYMENT_TIMEOUT_SECONDS = 60
    DROPOUT_TIMEOUT_SECONDS = 1


class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    pass


class Player(BasePlayer):
    final_total_payoff = models.CurrencyField(initial=0)


# FUNCTIONS
def participant_var(participant, field_name, default=''):
    return participant.vars.get(field_name, default)


def payoff_source_var(player: Player):
    return player.session.config.get('payoff_source_var', 'route_choice_total_payoff')


def experiment_label(player: Player):
    return player.session.config.get('final_payoff_label', '交通实验')


def payoff_rounds(player: Player):
    value = player.session.config.get('payoff_rounds', 10)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10


def payoff_source_label(player: Player):
    return player.session.config.get(
        'payoff_source_label',
        f'{experiment_label(player)} 全 {payoff_rounds(player)} 轮累计结果',
    )


def maybe_restore_disconnect_participant(player: Player):
    participant = player.participant
    if not bool(participant_var(participant, 'dropout_active', False)):
        return
    if (participant_var(participant, 'dropout_reason', '') or '') != 'disconnect':
        return

    participant.dropout_active = False
    participant.dropout_reason = ''
    participant.has_recovered_after_disconnect = True


# PAGES
class PaymentInfo(Page):
    @staticmethod
    def get_timeout_seconds(player: Player):
        maybe_restore_disconnect_participant(player)
        if bool(participant_var(player.participant, 'dropout_active', False)):
            return C.DROPOUT_TIMEOUT_SECONDS
        return C.PAYMENT_TIMEOUT_SECONDS

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        maybe_restore_disconnect_participant(player)
        participant = player.participant
        player.final_total_payoff = participant.vars.get(
            payoff_source_var(player), participant.payoff
        )
        participant.finished = True

    @staticmethod
    def vars_for_template(player: Player):
        maybe_restore_disconnect_participant(player)
        participant = player.participant
        total_payoff = participant.vars.get(payoff_source_var(player), participant.payoff)
        return dict(
            redemption_code=participant.label or participant.code,
            total_payoff=total_payoff,
            experiment_label=experiment_label(player),
            payoff_rounds=payoff_rounds(player),
            payoff_source_label=payoff_source_label(player),
            auto_advance_seconds=PaymentInfo.get_timeout_seconds(player),
        )


page_sequence = [PaymentInfo]
