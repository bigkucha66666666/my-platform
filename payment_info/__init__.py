from otree.api import *

from .cash_payment import BASE_CENTS, RULE_VERSION, calculate_cash_payment



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


def is_dynamic_cash_payment(player: Player):
    return bool(player.session.config.get('cash_payment_rule'))


def _restore_formal_cost_if_available(participant):
    """Support Sessions that reached round 33 before cost totals were stored."""
    get_player = getattr(participant, 'get_player', None)
    if not callable(get_player):
        return
    try:
        from dynamic_bottleneck_round import C as DynamicC
        from dynamic_bottleneck_round import (
            MEAN_COST_VAR,
            SETTLED_FORMAL_ROUNDS_VAR,
            TOTAL_COST_DECIMAL_VAR,
            TOTAL_COST_VAR,
            formal_cost_decimal_total,
            formal_cost_total,
            is_warmup_round,
        )

        last_player = get_player('dynamic_bottleneck_round', DynamicC.NUM_ROUNDS)
        formal_players = [
            round_player for round_player in last_player.in_all_rounds()
            if not is_warmup_round(round_player.round_number)
        ]
        if len(formal_players) != DynamicC.FORMAL_ROUNDS:
            return
        if not all(round_player.group.results_ready for round_player in formal_players):
            return
        exact_cost = formal_cost_decimal_total(last_player)
        participant.vars[TOTAL_COST_VAR] = formal_cost_total(last_player)
        participant.vars[TOTAL_COST_DECIMAL_VAR] = str(exact_cost)
        participant.vars[MEAN_COST_VAR] = float(exact_cost / DynamicC.FORMAL_ROUNDS)
        participant.vars[SETTLED_FORMAL_ROUNDS_VAR] = DynamicC.FORMAL_ROUNDS
    except (AttributeError, KeyError, LookupError):
        return


def dynamic_cash_payment_for_player(player: Player):
    if player.session.config.get('cash_payment_rule') != RULE_VERSION:
        return None
    participant = player.participant
    if 'dynamic_bottleneck_round_total_cost_decimal' not in participant.vars:
        _restore_formal_cost_if_available(participant)
    try:
        return calculate_cash_payment(
            total_cost=participant.vars.get(
                'dynamic_bottleneck_round_total_cost_decimal',
                participant.vars.get('dynamic_bottleneck_round_total_cost'),
            ),
            formal_rounds=participant.vars.get(
                'dynamic_bottleneck_round_settled_formal_rounds'
            ),
        )
    except ValueError:
        return None


def format_cash_cents(cents):
    return f'{int(cents) / 100:.2f}'


def settle_dynamic_cash_payment(player: Player):
    participant = player.participant
    payment = dynamic_cash_payment_for_player(player)
    participant.vars['dynamic_bottleneck_cash_payment_rule'] = str(
        player.session.config.get('cash_payment_rule', '')
    )
    participant.vars['dynamic_bottleneck_cash_base_cents'] = BASE_CENTS

    # The first 30 rounds retain their experimental point payoffs. Offset them
    # here so oTree's native Session payment equals base fee + cash bonus.
    prior_points = participant.payoff - player.payoff
    if payment is None:
        participant.vars['dynamic_bottleneck_cash_bonus_cents'] = 0
        participant.vars.pop('dynamic_bottleneck_cash_payment_cents', None)
        participant.vars['dynamic_bottleneck_cash_payment_status'] = 'manual_review'
        player.payoff = -prior_points
        return

    participant.vars['dynamic_bottleneck_cash_bonus_cents'] = payment.bonus_cents
    participant.vars['dynamic_bottleneck_cash_payment_cents'] = payment.total_cents
    participant.vars['dynamic_bottleneck_cash_payment_status'] = 'settled'
    player.payoff = cu(payment.bonus_cents) - prior_points


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
        if is_dynamic_cash_payment(player):
            player.final_total_payoff = participant.vars.get(payoff_source_var(player), cu(0))
            settle_dynamic_cash_payment(player)
        else:
            player.final_total_payoff = participant.vars.get(
                payoff_source_var(player), participant.payoff
            )
        participant.finished = True

    @staticmethod
    def vars_for_template(player: Player):
        maybe_restore_disconnect_participant(player)
        participant = player.participant
        if is_dynamic_cash_payment(player):
            player.final_total_payoff = participant.vars.get(payoff_source_var(player), cu(0))
            settle_dynamic_cash_payment(player)
        total_payoff = participant.vars.get(payoff_source_var(player), participant.payoff)
        context = dict(
            redemption_code=participant.label or participant.code,
            total_payoff=total_payoff,
            experiment_label=experiment_label(player),
            payoff_rounds=payoff_rounds(player),
            payoff_source_label=payoff_source_label(player),
            auto_advance_seconds=PaymentInfo.get_timeout_seconds(player),
            is_dynamic_cash=is_dynamic_cash_payment(player),
        )
        if context['is_dynamic_cash']:
            payment = dynamic_cash_payment_for_player(player)
            context.update(
                cash_settlement_ready=payment is not None,
                cash_total_label=(format_cash_cents(payment.total_cents) if payment else ''),
                cash_base_label=format_cash_cents(BASE_CENTS),
                cash_bonus_label=(format_cash_cents(payment.bonus_cents) if payment else ''),
                formal_mean_cost_label=(f'{payment.mean_cost:.2f}' if payment else ''),
                payment_rule_version=RULE_VERSION,
            )
        return context


PAYMENT_EXPORT_HEADERS = [
    'session_code',
    'participant_code',
    'participant_label',
    'payment_rule',
    'payment_status',
    'formal_rounds',
    'formal_total_cost',
    'formal_mean_cost',
    'formal_points',
    'base_cny',
    'bonus_cny',
    'total_cny',
    'native_cash_total_cny',
    'accounting_adjustment_points',
]


def custom_export(players):
    yield PAYMENT_EXPORT_HEADERS
    for player in players:
        participant = player.participant
        vars = participant.vars
        dynamic_cash = is_dynamic_cash_payment(player)
        status = vars.get('dynamic_bottleneck_cash_payment_status', '') if dynamic_cash else ''
        base_cents = vars.get('dynamic_bottleneck_cash_base_cents') if dynamic_cash else None
        bonus_cents = vars.get('dynamic_bottleneck_cash_bonus_cents') if dynamic_cash else None
        total_cents = vars.get('dynamic_bottleneck_cash_payment_cents') if dynamic_cash else None
        yield [
            player.session.code,
            participant.code,
            participant.label or '',
            vars.get('dynamic_bottleneck_cash_payment_rule', '') if dynamic_cash else '',
            status,
            vars.get('dynamic_bottleneck_round_settled_formal_rounds', '') if dynamic_cash else '',
            vars.get('dynamic_bottleneck_round_total_cost', '') if dynamic_cash else '',
            vars.get('dynamic_bottleneck_round_mean_cost', '') if dynamic_cash else '',
            vars.get(payoff_source_var(player), '') if dynamic_cash else '',
            format_cash_cents(base_cents) if base_cents is not None else '',
            format_cash_cents(bonus_cents) if bonus_cents is not None else '',
            format_cash_cents(total_cents) if total_cents is not None else '',
            f'{float(participant.payoff_plus_participation_fee()):.2f}' if dynamic_cash else '',
            float(player.payoff) if dynamic_cash else '',
        ]


page_sequence = [PaymentInfo]
