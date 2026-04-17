from math import ceil
import json
import random
import time

from otree.api import *

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
    ROUND1_JOIN_GRACE_SECONDS = 15
    WAIT_GRACE_SECONDS = 5
    SYNC_POLL_INTERVAL_SECONDS = 1
    AUTO_CONTINUE_DELAY_MS = 200

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
    decision_deadline_ts = models.FloatField(initial=0)
    results_ready = models.BooleanField(initial=False)


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


EXPORT_HEADERS = [
    'session_code',
    'session_config_name',
    'data_tier',
    'grouping_enabled',
    'manual_grouping_spec',
    'assigned_group_id',
    'assigned_group_label',
    'assigned_group_members',
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


def config_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False


def participant_display_label(player: Player) -> str:
    return player.participant.label or player.participant.code


def safe_model_field(obj, field_name, default=''):
    value = obj.field_maybe_none(field_name) if hasattr(obj, 'field_maybe_none') else getattr(obj, field_name, None)
    return default if value is None else value


def build_auto_group_matrix(players, cohort_size: int):
    return [
        players[index:index + cohort_size]
        for index in range(0, len(players), cohort_size)
    ]


def parse_manual_grouping_spec(spec: str):
    raw_spec = (spec or '').strip()
    if not raw_spec:
        raise ValueError(
            '启用手动分组时，manual_grouping_spec 不能为空。示例：P001,P003|P002,P004|P005'
        )

    groups = []
    for raw_group in raw_spec.split('|'):
        raw_labels = [label.strip() for label in raw_group.split(',')]
        if any(not label for label in raw_labels):
            raise ValueError(
                'manual_grouping_spec 中存在空标签。请使用格式：P001,P003|P002,P004|P005'
            )
        group_labels = raw_labels
        if not group_labels:
            raise ValueError(
                'manual_grouping_spec 存在空分组。请使用格式：P001,P003|P002,P004|P005'
            )
        groups.append(group_labels)
    return groups


def validate_manual_groups(players, manual_groups, session_name: str):
    if session_name != 'route_choice_prod':
        raise ValueError('仅正式场次 route_choice_prod 支持按 participant_label 手动分组。')

    available_labels = []
    label_to_player = {}
    missing_labels = []
    for player in players:
        label = player.participant.label
        if not label:
            missing_labels.append(player.participant.code)
            continue
        if label in label_to_player:
            raise ValueError(f'participant_label 重复：{label}。请检查房间标签配置。')
        available_labels.append(label)
        label_to_player[label] = player

    if missing_labels:
        raise ValueError(
            '检测到缺少 participant_label 的参与者，无法执行手动分组：'
            + ', '.join(missing_labels)
        )

    configured_labels = [label for group_labels in manual_groups for label in group_labels]
    duplicate_labels = sorted({label for label in configured_labels if configured_labels.count(label) > 1})
    if duplicate_labels:
        raise ValueError(
            'manual_grouping_spec 中存在重复标签：' + ', '.join(duplicate_labels)
        )

    unknown_labels = sorted(set(configured_labels) - set(available_labels))
    if unknown_labels:
        raise ValueError(
            'manual_grouping_spec 中存在未知标签：' + ', '.join(unknown_labels)
        )

    missing_configured = sorted(set(available_labels) - set(configured_labels))
    if missing_configured:
        raise ValueError(
            'manual_grouping_spec 漏掉了以下标签：' + ', '.join(missing_configured)
        )

    return label_to_player


def build_manual_group_matrix(players, spec: str, session_name: str):
    manual_groups = parse_manual_grouping_spec(spec)
    label_to_player = validate_manual_groups(players, manual_groups, session_name)
    return [[label_to_player[label] for label in group_labels] for group_labels in manual_groups]


def assign_group_metadata(matrix, grouping_enabled: bool):
    for group_index, group_players in enumerate(matrix, start=1):
        member_labels = ','.join(participant_display_label(player) for player in group_players)
        group_label = f'G{group_index:02d}'
        for player in group_players:
            participant = player.participant
            participant.vars['grouping_enabled'] = grouping_enabled
            participant.vars['assigned_group_id'] = group_index
            participant.vars['assigned_group_label'] = group_label
            participant.vars['assigned_group_members'] = member_labels


def creating_session(subsession: Subsession):
    if subsession.round_number == 1:
        players = subsession.get_players()
        for player in players:
            player.participant.is_dropout = False
            player.participant.finished = False
        session_name = subsession.session.config.get('name', '')
        grouping_enabled = config_flag(subsession.session.config.get('grouping_enabled', 0))
        manual_grouping_spec = subsession.session.config.get('manual_grouping_spec', '')
        if session_name != 'route_choice_prod' and (grouping_enabled or manual_grouping_spec.strip()):
            raise ValueError('仅正式场次 route_choice_prod 支持配置 participant_label 手动分组。')
        if grouping_enabled:
            matrix = build_manual_group_matrix(players, manual_grouping_spec, session_name)
        else:
            cohort_size = max(1, int(subsession.session.config.get('cohort_size', len(players) or 1)))
            matrix = build_auto_group_matrix(players, cohort_size)
        subsession.set_group_matrix(matrix)
        assign_group_metadata(matrix, grouping_enabled)
    else:
        subsession.group_like_round(1)

    for group in subsession.get_groups():
        group.decision_deadline_ts = 0
        group.results_ready = False


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


def ensure_decision_deadline(group: Group):
    if group.decision_deadline_ts:
        return

    if group.round_number == 1:
        seconds_until_deadline = (
            C.ROUND1_JOIN_GRACE_SECONDS
            + C.DECISION_TIMEOUT_SECONDS
            + C.WAIT_GRACE_SECONDS
        )
    else:
        seconds_until_deadline = (
            C.RESULTS_TIMEOUT_SECONDS
            + C.DECISION_TIMEOUT_SECONDS
            + C.WAIT_GRACE_SECONDS
        )

    group.decision_deadline_ts = time.time() + seconds_until_deadline


def all_players_have_route(group: Group) -> bool:
    return all(bool(player.route) for player in group.get_players())


def fill_missing_routes(group: Group):
    for player in group.get_players():
        if player.route:
            continue
        player.route = random.choice(['A', 'B'])
        mark_dropout(player)


def schedule_next_round_deadline(group: Group):
    if group.round_number >= C.NUM_ROUNDS:
        return

    next_group = group.in_round(group.round_number + 1)
    if next_group.decision_deadline_ts:
        return

    next_group.decision_deadline_ts = time.time() + (
        C.RESULTS_TIMEOUT_SECONDS
        + C.DECISION_TIMEOUT_SECONDS
        + C.WAIT_GRACE_SECONDS
    )


def maybe_prepare_results(group: Group):
    ensure_decision_deadline(group)

    if group.results_ready:
        return

    if all_players_have_route(group):
        set_results(group)
        return

    if time.time() >= group.decision_deadline_ts:
        fill_missing_routes(group)
        set_results(group)


def set_results(group: Group):
    if group.results_ready:
        return

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

    group.results_ready = True
    schedule_next_round_deadline(group)


def export_row_for_player(p: Player):
    session_config_name = p.session.config.get('name', '')
    grouping_enabled = config_flag(p.session.config.get('grouping_enabled', 0))
    manual_grouping_spec = p.session.config.get('manual_grouping_spec', '')
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
    assigned_group_id = participant.vars.get('assigned_group_id', '')
    assigned_group_label = participant.vars.get('assigned_group_label', '')
    assigned_group_members = participant.vars.get('assigned_group_members', '')
    participant_code = safe_model_field(participant, 'code', '')
    participant_label = safe_model_field(participant, 'label', '')
    round_number = safe_model_field(p, 'round_number', '')
    route = safe_model_field(p, 'route', '')
    travel_time = safe_model_field(p, 'travel_time', 0)
    route_a_count = safe_model_field(p, 'route_a_count', 0)
    route_b_count = safe_model_field(p, 'route_b_count', 0)
    my_route_count = safe_model_field(p, 'my_route_count', 0)
    payoff = safe_model_field(p, 'payoff', '')

    return [
        p.session.code,
        session_config_name,
        data_tier,
        grouping_enabled,
        manual_grouping_spec,
        assigned_group_id,
        assigned_group_label,
        assigned_group_members,
        participant_code,
        participant_label,
        round_number,
        route,
        travel_time,
        route_a_count,
        route_b_count,
        my_route_count,
        payoff,
        final_total_payoff,
        is_dropout,
        finished,
    ]


def row_to_dict(row):
    def normalize(value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    record = {}
    for header, value in zip(EXPORT_HEADERS, row):
        record[header] = normalize(value)
    return record


def build_session_reports(players):
    reports = {}
    for p in players:
        row = export_row_for_player(p)
        record = row_to_dict(row)
        session_code = record['session_code']
        session_pk = getattr(p.session, 'id', 0) or 0

        if session_code not in reports:
            reports[session_code] = dict(
                session_code=session_code,
                session_config_name=record['session_config_name'],
                data_tier=record['data_tier'],
                grouping_enabled=record['grouping_enabled'],
                manual_grouping_spec=record['manual_grouping_spec'],
                session_pk=session_pk,
                rows=[],
                participant_codes=set(),
                finished_codes=set(),
                dropout_codes=set(),
                rounds=set(),
            )

        report = reports[session_code]
        report['rows'].append(record)
        report['participant_codes'].add(record['participant_code'])
        if record['finished']:
            report['finished_codes'].add(record['participant_code'])
        if record['is_dropout']:
            report['dropout_codes'].add(record['participant_code'])
        report['rounds'].add(record['round_number'])

    session_reports = []
    for report in reports.values():
        summary = dict(
            session_code=report['session_code'],
            session_config_name=report['session_config_name'],
            data_tier=report['data_tier'],
            total_records=len(report['rows']),
            participant_count=len(report['participant_codes']),
            finished_count=len(report['finished_codes']),
            dropout_count=len(report['dropout_codes']),
            round_count=len(report['rounds']),
            grouping_status='手动分组' if report['grouping_enabled'] else '自动分组',
        )
        session_reports.append(
            dict(
                session_code=report['session_code'],
                session_config_name=report['session_config_name'],
                label=f"{report['session_code']} | {report['session_config_name']}",
                session_pk=report['session_pk'],
                summary=summary,
                rows=report['rows'],
            )
        )

    session_reports.sort(key=lambda item: (item['session_pk'], item['session_code']), reverse=True)
    return session_reports


def vars_for_admin_report(subsession: Subsession):
    players = []
    for round_subsession in subsession.in_all_rounds():
        players.extend(round_subsession.get_players())

    session_reports = build_session_reports(players)
    default_session_code = ''
    current_session_code = subsession.session.code
    available_session_codes = [report['session_code'] for report in session_reports]

    if current_session_code in available_session_codes:
        default_session_code = current_session_code
    elif session_reports:
        default_session_code = session_reports[0]['session_code']

    return dict(
        export_headers=EXPORT_HEADERS,
        export_headers_json=json.dumps(EXPORT_HEADERS, ensure_ascii=False),
        session_options=[
            dict(
                session_code=report['session_code'],
                label=report['label'],
            )
            for report in session_reports
        ],
        default_session_code=default_session_code,
        current_session_code=current_session_code,
        session_reports_json=json.dumps(session_reports, ensure_ascii=False),
    )


def access_allowed(player: Player):
    if player.session.config.get('name') != 'route_choice_prod':
        return True
    return bool(player.participant.vars.get('access_granted', False))


class MyPage(Page):
    form_model = 'player'
    form_fields = ['route']

    @staticmethod
    def is_displayed(player: Player):
        if not access_allowed(player):
            return False
        ensure_decision_deadline(player.group)
        return True

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


class ResultsSync(Page):

    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)

    @staticmethod
    def vars_for_template(player: Player):
        maybe_prepare_results(player.group)
        remaining_seconds = max(0, ceil(player.group.decision_deadline_ts - time.time()))

        return dict(
            results_ready=player.group.results_ready,
            remaining_seconds=remaining_seconds,
            poll_interval_ms=C.SYNC_POLL_INTERVAL_SECONDS * 1000,
            auto_continue_delay_ms=C.AUTO_CONTINUE_DELAY_MS,
        )

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        maybe_prepare_results(player.group)


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
    yield EXPORT_HEADERS
    for p in players:
        yield export_row_for_player(p)


page_sequence = [MyPage, ResultsSync, Results]
