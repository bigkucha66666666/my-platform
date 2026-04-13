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
    DECISION_TIMEOUT_SECONDS = 30
    RESULTS_TIMEOUT_SECONDS = 12
    DROPOUT_TIMEOUT_SECONDS = 1

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


def creating_session(subsession: Subsession):
    if subsession.round_number == 1:
        players = subsession.get_players()
        for player in players:
            player.participant.is_dropout = False
            player.participant.finished = False
        cohort_size = max(1, int(subsession.session.config.get('cohort_size', len(players) or 1)))
        matrix = [
            players[index:index + cohort_size]
            for index in range(0, len(players), cohort_size)
        ]
        subsession.set_group_matrix(matrix)
    else:
        subsession.group_like_round(1)


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


def participant_is_dropout(player: Player) -> bool:
    return bool(getattr(player.participant, 'is_dropout', False))


def mark_dropout(player: Player):
    player.participant.is_dropout = True


def set_results(group: Group):
    players = group.get_players()
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

        if group.round_number == C.NUM_ROUNDS:
            total_payoff = sum(round_player.payoff for round_player in p.in_all_rounds())
            p.participant.vars['route_choice_total_payoff'] = total_payoff


def access_allowed(player: Player):
    if player.session.config.get('name') != 'route_choice_prod':
        return True
    return bool(player.participant.vars.get('access_granted', False))


class MyPage(Page):
    form_model = 'player'
    form_fields = ['route']

    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)

    @staticmethod
    def get_timeout_seconds(player: Player):
        if participant_is_dropout(player):
            return C.DROPOUT_TIMEOUT_SECONDS
        return C.DECISION_TIMEOUT_SECONDS

    @staticmethod
    def vars_for_template(player: Player):
        return dict(auto_advance_seconds=MyPage.get_timeout_seconds(player))

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        if timeout_happened and not player.route:
            player.route = random.choice(['A', 'B'])
        if timeout_happened:
            mark_dropout(player)


class ResultsWaitPage(WaitPage):
    wait_for_all_groups = False
    after_all_players_arrive = set_results

    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)


class Results(Page):
    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)

    @staticmethod
    def get_timeout_seconds(player: Player):
        if participant_is_dropout(player):
            return C.DROPOUT_TIMEOUT_SECONDS
        return C.RESULTS_TIMEOUT_SECONDS

    @staticmethod
    def vars_for_template(player: Player):
        total_players = player.route_a_count + player.route_b_count
        route_label = '路线 A（主干道）' if player.route == 'A' else '路线 B（环线）'
        congestion_ratio = 0
        if total_players > 0:
            congestion_ratio = round(player.my_route_count / total_players * 100)

        travel_time_if_a = route_time('A', player.route_a_count, player.route_b_count, total_players)
        travel_time_if_b = route_time('B', player.route_a_count, player.route_b_count, total_players)

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
            auto_advance_seconds=Results.get_timeout_seconds(player),
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
        'is_dropout',
        'finished',
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
        is_dropout = getattr(participant, 'is_dropout', False)
        finished = getattr(participant, 'finished', False)

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
            is_dropout,
            finished,
        ]


page_sequence = [MyPage, ResultsWaitPage, Results]
