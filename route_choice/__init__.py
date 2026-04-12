from otree.api import *
import random

doc = """
一个极简的交通拥堵分析 demo。
所有参与者同时选择路线，路线人数越多，通行时间越长。
"""

class C(BaseConstants):
    NAME_IN_URL = 'route_choice'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 10

    # Route A: free-flow faster but congestion-sensitive.
    TIME_A_FREE = 10
    TIME_A_CONGESTION = 2

    # Route B: free-flow slower but congestion-resilient.
    TIME_B_FREE = 14
    TIME_B_CONGESTION = 1

    # Utility/points model.
    BASE_POINTS = 140
    TIME_COST = 2
    TOLL_A = 8
    TOLL_B = 2

class Subsession(BaseSubsession):
    pass

class Group(BaseGroup):
    pass


class Player(BasePlayer):
    route = models.StringField(
        choices=[
            ['A', '路线 A（主干道）: 平时更快，但更容易拥堵'],
            ['B', '路线 B（环线）: 平时略慢，但更稳定'],
        ],
        widget=widgets.RadioSelect,
        label='请选择你的出行路线',
    )
    travel_time = models.IntegerField(initial=0)
    route_a_count = models.IntegerField(initial=0)
    route_b_count = models.IntegerField(initial=0)
    my_route_count = models.IntegerField(initial=0)


def route_time(route: str, route_a_count: int, route_b_count: int, total_players: int) -> int:
    route_count = route_a_count if route == 'A' else route_b_count
    safe_total = max(total_players, 1)
    congestion_ratio = route_count / safe_total
    effective_load = route_count * (1 + congestion_ratio)

    if route == 'A':
        time_value = C.TIME_A_FREE + C.TIME_A_CONGESTION * effective_load
    else:
        time_value = C.TIME_B_FREE + C.TIME_B_CONGESTION * effective_load

    return int(round(time_value))


def set_results(subsession: Subsession):
    players = subsession.get_players()
    route_a_count = sum(p.route == 'A' for p in players)
    route_b_count = sum(p.route == 'B' for p in players)
    total_players = route_a_count + route_b_count

    for p in players:
        p.route_a_count = route_a_count
        p.route_b_count = route_b_count
        p.my_route_count = route_a_count if p.route == 'A' else route_b_count
        p.travel_time = route_time(p.route, route_a_count, route_b_count, total_players)

        toll = C.TOLL_A if p.route == 'A' else C.TOLL_B
        points = C.BASE_POINTS - C.TIME_COST * p.travel_time - toll
        p.payoff = cu(max(0, points))


def access_allowed(player: Player):
    if player.session.config.get('name') != 'route_choice_prod':
        return True
    return bool(player.participant.vars.get('access_granted', False))


class MyPage(Page):
    timeout_seconds = 30
    form_model = 'player'
    form_fields = ['route']

    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        if timeout_happened and not player.route:
            player.route = random.choice(['A', 'B'])


class ResultsWaitPage(WaitPage):
    wait_for_all_groups = True
    after_all_players_arrive = set_results

    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)


class Results(Page):
    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)

    @staticmethod
    def vars_for_template(player: Player):
        total_players = player.route_a_count + player.route_b_count
        route_label = '路线 A（主干道）' if player.route == 'A' else '路线 B（环线）'
        congestion_ratio = 0
        if total_players > 0:
            congestion_ratio = round(player.my_route_count / total_players * 100)

        travel_time_if_a = route_time('A', player.route_a_count, player.route_b_count, total_players)
        travel_time_if_b = route_time('B', player.route_a_count, player.route_b_count, total_players)

        # Save final cumulative reward after round 10 for payment page display.
        if player.round_number == C.NUM_ROUNDS:
            total_payoff = sum(p.payoff for p in player.in_all_rounds())
            player.participant.vars['route_choice_total_payoff'] = total_payoff

        return dict(
            total_players=total_players,
            route_a_count=player.route_a_count,
            route_b_count=player.route_b_count,
            route_label=route_label,
            my_route_count=player.my_route_count,
            congestion_ratio=congestion_ratio,
            my_travel_time=player.travel_time,
            travel_time_if_a=travel_time_if_a,
            travel_time_if_b=travel_time_if_b,
            my_payoff=player.payoff,
        )


def custom_export(players):
    yield [
        'session_code',
        'session_config_name',
        'data_tier',
        'participant_code',
        'participant_label',
        'round_number',
        'route',
        'travel_time',
        'route_a_count',
        'route_b_count',
        'my_route_count',
        'payoff',
        'final_total_payoff',
    ]

    for p in players:
        session_config_name = p.session.config.get('name', '')
        if session_config_name == 'route_choice_prod':
            data_tier = 'prod'
        elif session_config_name == 'route_choice_demo':
            data_tier = 'demo'
        else:
            data_tier = 'other'

        participant = p.participant
        final_total_payoff = participant.vars.get('route_choice_total_payoff', '')

        yield [
            p.session.code,
            session_config_name,
            data_tier,
            participant.code,
            participant.label,
            p.round_number,
            p.route,
            p.travel_time,
            p.route_a_count,
            p.route_b_count,
            p.my_route_count,
            p.payoff,
            final_total_payoff,
        ]


page_sequence = [MyPage, ResultsWaitPage, Results]