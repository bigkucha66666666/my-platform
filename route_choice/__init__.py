from otree.api import *

doc = """
一个极简的交通拥堵分析 demo。
所有参与者同时选择路线，路线人数越多，通行时间越长。
"""

class C(BaseConstants):
    NAME_IN_URL = 'route_choice'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 1

    # Route A: free-flow faster but congestion-sensitive.
    TIME_A_FREE = 10
    TIME_A_CONGESTION = 2

    # Route B: free-flow slower but congestion-resilient.
    TIME_B_FREE = 14
    TIME_B_CONGESTION = 1

    # Utility/points model.
    BASE_POINTS = 140
    TIME_COST = 3
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


def route_time(route: str, route_a_count: int, route_b_count: int) -> int:
    if route == 'A':
        return C.TIME_A_FREE + C.TIME_A_CONGESTION * route_a_count
    return C.TIME_B_FREE + C.TIME_B_CONGESTION * route_b_count


def set_results(subsession: Subsession):
    players = subsession.get_players()
    route_a_count = sum(p.route == 'A' for p in players)
    route_b_count = sum(p.route == 'B' for p in players)

    for p in players:
        p.route_a_count = route_a_count
        p.route_b_count = route_b_count
        p.my_route_count = route_a_count if p.route == 'A' else route_b_count
        p.travel_time = route_time(p.route, route_a_count, route_b_count)

        toll = C.TOLL_A if p.route == 'A' else C.TOLL_B
        points = C.BASE_POINTS - C.TIME_COST * p.travel_time - toll
        p.payoff = cu(max(0, points))


class MyPage(Page):
    form_model = 'player'
    form_fields = ['route']


class ResultsWaitPage(WaitPage):
    wait_for_all_groups = True
    after_all_players_arrive = set_results


class Results(Page):
    @staticmethod
    def vars_for_template(player: Player):
        total_players = player.route_a_count + player.route_b_count
        route_label = '路线 A（主干道）' if player.route == 'A' else '路线 B（环线）'
        congestion_ratio = 0
        if total_players > 0:
            congestion_ratio = round(player.my_route_count / total_players * 100)

        travel_time_if_a = route_time('A', player.route_a_count, player.route_b_count)
        travel_time_if_b = route_time('B', player.route_a_count, player.route_b_count)

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


page_sequence = [MyPage, ResultsWaitPage, Results]