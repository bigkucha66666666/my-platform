from math import ceil
import json
import random
import time

from otree.api import *

doc = """
一个极简的交通拥堵分析 demo。
所有参与者同时选择路线，路线人数越多，通行时间越长。
"""

DECISION_SOURCE_MANUAL = 'manual'
DECISION_SOURCE_TIMEOUT_AUTO = 'timeout_auto'
DECISION_SOURCE_DISCONNECT_AUTO = 'disconnect_auto'

DROPOUT_REASON_TIMEOUT = 'timeout'
DROPOUT_REASON_DISCONNECT = 'disconnect'

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
    decision_source = models.StringField(blank=True)


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
    'decision_source',
    'travel_time',
    'route_a_count',
    'route_b_count',
    'my_route_count',
    'payoff',
    'final_total_payoff',
    'is_dropout',
    'dropout_active',
    'dropout_reason',
    'has_recovered_after_disconnect',
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


def participant_var(player: Player, field_name, default=''):
    return player.participant.vars.get(field_name, default)


def player_route(player: Player):
    route = player.field_maybe_none('route') if hasattr(player, 'field_maybe_none') else None
    return route if route in {'A', 'B'} else None


def player_has_route(player: Player) -> bool:
    return player_route(player) is not None


def cumulative_payoff_so_far(player: Player):
    total_payoff = cu(0)
    for round_player in player.in_rounds(1, player.round_number):
        total_payoff += safe_model_field(round_player, 'payoff', cu(0))
    return total_payoff


def reward_history_so_far(player: Player):
    history = []
    for round_player in player.in_rounds(1, player.round_number):
        history.append(
            dict(
                round_number=safe_model_field(round_player, 'round_number', 0),
                payoff=safe_model_field(round_player, 'payoff', cu(0)),
            )
        )
    return history


def bounded_percent(value, max_value):
    try:
        numeric_value = float(value)
        numeric_max = float(max_value)
    except (TypeError, ValueError):
        return 0

    if numeric_max <= 0:
        return 0

    percentage = numeric_value / numeric_max * 100
    return max(0, min(100, round(percentage, 1)))


def format_numeric_label(value):
    numeric_value = float(value)
    return str(int(numeric_value)) if numeric_value.is_integer() else f'{numeric_value:.1f}'


def build_reward_trend_svg(player: Player):
    chart_width = 360
    chart_height = 144
    padding_left = 30
    padding_right = 10
    padding_top = 10
    padding_bottom = 24
    plot_width = chart_width - padding_left - padding_right
    plot_height = chart_height - padding_top - padding_bottom
    plot_bottom = padding_top + plot_height
    plot_right = padding_left + plot_width
    max_value = max(float(C.BASE_POINTS), 1)

    history = reward_history_so_far(player)
    trend_points = []
    for index, item in enumerate(history):
        if len(history) == 1:
            x_pos = padding_left + plot_width / 2
        else:
            x_pos = padding_left + (plot_width * index / (len(history) - 1))
        y_ratio = max(0, min(1, float(item['payoff']) / max_value))
        y_pos = padding_top + (1 - y_ratio) * plot_height
        trend_points.append(
            dict(
                x=round(x_pos, 1),
                y=round(y_pos, 1),
                round_number=item['round_number'],
                payoff=item['payoff'],
            )
        )

    y_tick_values = [C.BASE_POINTS, C.BASE_POINTS / 2, 0]
    y_ticks = []
    for tick_value in y_tick_values:
        y_ratio = max(0, min(1, float(tick_value) / max_value))
        y_pos = padding_top + (1 - y_ratio) * plot_height
        y_ticks.append(
            dict(
                label=format_numeric_label(tick_value),
                y=round(y_pos, 1),
                label_y=round(y_pos + 4, 1),
            )
        )

    area_points = ''
    if trend_points:
        area_points = ' '.join(
            [
                f"{trend_points[0]['x']},{round(plot_bottom, 1)}",
                *[f"{point['x']},{point['y']}" for point in trend_points],
                f"{trend_points[-1]['x']},{round(plot_bottom, 1)}",
            ]
        )

    if history:
        reward_values = [float(item['payoff']) for item in history]
        highest_value = max(reward_values)
        lowest_value = min(reward_values)
        average_value = sum(reward_values) / len(reward_values)
    else:
        highest_value = 0
        lowest_value = 0
        average_value = 0

    return dict(
        reward_history_rounds=[item['round_number'] for item in history],
        reward_history_values=[item['payoff'] for item in history],
        reward_trend_svg_points=' '.join(f"{point['x']},{point['y']}" for point in trend_points),
        reward_trend_area_points=area_points,
        reward_trend_current_point=(
            dict(
                **trend_points[-1],
                label_y=max(padding_top + 10, trend_points[-1]['y'] - 10),
            )
            if trend_points else None
        ),
        reward_trend_points=trend_points,
        reward_trend_y_ticks=y_ticks,
        reward_trend_has_multiple_points=len(trend_points) > 1,
        reward_trend_width=chart_width,
        reward_trend_height=chart_height,
        reward_trend_plot_left=padding_left,
        reward_trend_plot_right=round(plot_right, 1),
        reward_trend_plot_top=padding_top,
        reward_trend_plot_bottom=round(plot_bottom, 1),
        reward_trend_y_label_x=padding_left - 8,
        reward_trend_x_label_y=round(plot_bottom + 16, 1),
        reward_trend_summary=[
            dict(label='当前', value=format_numeric_label(history[-1]['payoff']) if history else '0'),
            dict(label='最高', value=format_numeric_label(highest_value)),
            dict(label='最低', value=format_numeric_label(lowest_value)),
            dict(label='平均', value=format_numeric_label(average_value)),
        ],
    )


def current_round_average_scope_players(player: Player):
    grouping_enabled = config_flag(player.session.config.get('grouping_enabled', 0))
    if grouping_enabled:
        return player.group.get_players()
    current_subsession = player.subsession
    return current_subsession.get_players()


def current_round_average_reference(player: Player):
    scoped_players = current_round_average_scope_players(player)
    completed_payoffs = []
    for scoped_player in scoped_players:
        payoff = safe_model_field(scoped_player, 'payoff', None)
        if payoff is None:
            continue
        completed_payoffs.append(float(payoff))

    if not completed_payoffs:
        return dict(
            reward_trend_show_avg_line=False,
            reward_trend_avg_line_y=0,
            reward_trend_avg_line_value='',
            reward_trend_avg_line_label='',
        )

    average_value = sum(completed_payoffs) / len(completed_payoffs)
    max_value = max(float(C.BASE_POINTS), 1)
    plot_top = 10
    plot_height = 144 - 10 - 24
    average_ratio = max(0, min(1, average_value / max_value))
    average_y = plot_top + (1 - average_ratio) * plot_height
    grouping_enabled = config_flag(player.session.config.get('grouping_enabled', 0))

    return dict(
        reward_trend_show_avg_line=True,
        reward_trend_avg_line_y=round(average_y, 1),
        reward_trend_avg_line_value=format_numeric_label(average_value),
        reward_trend_avg_line_label='当前轮同组平均' if grouping_enabled else '当前轮全场平均',
    )


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
            player.participant.dropout_active = False
            player.participant.dropout_reason = ''
            player.participant.has_recovered_after_disconnect = False
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
    return bool(participant_var(player, 'is_dropout', False))


def participant_dropout_active(player: Player) -> bool:
    return bool(participant_var(player, 'dropout_active', False))


def participant_dropout_reason(player: Player) -> str:
    reason = (participant_var(player, 'dropout_reason', '') or '').strip()
    if reason in {DROPOUT_REASON_TIMEOUT, DROPOUT_REASON_DISCONNECT}:
        return reason
    return ''


def participant_has_recovered_after_disconnect(player: Player) -> bool:
    return bool(participant_var(player, 'has_recovered_after_disconnect', False))


def mark_timeout_dropout(player: Player):
    participant = player.participant
    participant.is_dropout = True
    participant.dropout_active = True
    participant.dropout_reason = DROPOUT_REASON_TIMEOUT


def mark_disconnect_dropout(player: Player):
    participant = player.participant
    participant.is_dropout = True
    participant.dropout_active = True
    if participant_dropout_reason(player) == DROPOUT_REASON_TIMEOUT:
        return
    participant.dropout_reason = DROPOUT_REASON_DISCONNECT


def maybe_restore_disconnect_participant(player: Player):
    if not participant_dropout_active(player):
        return False
    if participant_dropout_reason(player) != DROPOUT_REASON_DISCONNECT:
        return False

    participant = player.participant
    participant.dropout_active = False
    participant.dropout_reason = ''
    participant.has_recovered_after_disconnect = True
    return True


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


def remaining_decision_seconds(group: Group) -> int:
    ensure_decision_deadline(group)
    return max(0, ceil(group.decision_deadline_ts - time.time()))


def all_players_have_route(group: Group) -> bool:
    return all(player_has_route(player) for player in group.get_players())


def fill_missing_routes(group: Group):
    for player in group.get_players():
        if player_has_route(player):
            continue
        player.route = random.choice(['A', 'B'])
        player.decision_source = DECISION_SOURCE_DISCONNECT_AUTO
        mark_disconnect_dropout(player)


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

    if not all_players_have_route(group):
        fill_missing_routes(group)

    players = group.get_players()
    player_routes = {}
    for p in players:
        route = player_route(p)
        if route is None:
            route = random.choice(['A', 'B'])
            p.route = route
            p.decision_source = DECISION_SOURCE_DISCONNECT_AUTO
            mark_disconnect_dropout(p)
        player_routes[p.id_in_group] = route

    route_a_count = sum(route == 'A' for route in player_routes.values())
    route_b_count = sum(route == 'B' for route in player_routes.values())
    total_players = route_a_count + route_b_count

    for p in players:
        route = player_routes[p.id_in_group]
        p.route_a_count = route_a_count
        p.route_b_count = route_b_count
        p.my_route_count = route_a_count if route == 'A' else route_b_count
        p.travel_time = route_time(route, route_a_count, route_b_count, total_players)

        toll = C.TOLL_A if route == 'A' else C.TOLL_B
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
    is_dropout = participant_is_dropout(p)
    dropout_active = participant_dropout_active(p)
    dropout_reason = participant_dropout_reason(p)
    has_recovered_after_disconnect = participant_has_recovered_after_disconnect(p)
    finished = participant.vars.get('finished', False)
    assigned_group_id = participant.vars.get('assigned_group_id', '')
    assigned_group_label = participant.vars.get('assigned_group_label', '')
    assigned_group_members = participant.vars.get('assigned_group_members', '')
    participant_code = safe_model_field(participant, 'code', '')
    participant_label = safe_model_field(participant, 'label', '')
    round_number = safe_model_field(p, 'round_number', '')
    route = safe_model_field(p, 'route', '')
    decision_source = safe_model_field(p, 'decision_source', '')
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
        decision_source,
        travel_time,
        route_a_count,
        route_b_count,
        my_route_count,
        payoff,
        final_total_payoff,
        is_dropout,
        dropout_active,
        dropout_reason,
        has_recovered_after_disconnect,
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
                historical_dropout_codes=set(),
                active_dropout_codes=set(),
                recovered_codes=set(),
                rounds=set(),
            )

        report = reports[session_code]
        report['rows'].append(record)
        report['participant_codes'].add(record['participant_code'])
        if record['finished']:
            report['finished_codes'].add(record['participant_code'])
        if record['is_dropout']:
            report['historical_dropout_codes'].add(record['participant_code'])
        if record['dropout_active']:
            report['active_dropout_codes'].add(record['participant_code'])
        if record['has_recovered_after_disconnect']:
            report['recovered_codes'].add(record['participant_code'])
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
            historical_dropout_count=len(report['historical_dropout_codes']),
            active_dropout_count=len(report['active_dropout_codes']),
            recovered_count=len(report['recovered_codes']),
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
        maybe_prepare_results(player.group)
        if player.group.results_ready:
            return False
        if player_has_route(player):
            return False
        maybe_restore_disconnect_participant(player)
        return True

    @staticmethod
    def get_timeout_seconds(player: Player):
        maybe_prepare_results(player.group)
        if not player.group.results_ready and not player_has_route(player):
            maybe_restore_disconnect_participant(player)
        if participant_dropout_active(player):
            return C.DROPOUT_TIMEOUT_SECONDS
        return min(C.DECISION_TIMEOUT_SECONDS, max(1, remaining_decision_seconds(player.group)))

    @staticmethod
    def vars_for_template(player: Player):
        return dict(auto_advance_seconds=MyPage.get_timeout_seconds(player))

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        if timeout_happened and not player_has_route(player):
            player.route = random.choice(['A', 'B'])
            player.decision_source = DECISION_SOURCE_TIMEOUT_AUTO
            mark_timeout_dropout(player)
            return
        player.decision_source = DECISION_SOURCE_MANUAL


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
        if participant_dropout_active(player):
            return C.DROPOUT_TIMEOUT_SECONDS
        return C.RESULTS_TIMEOUT_SECONDS

    @staticmethod
    def vars_for_template(player: Player):
        maybe_prepare_results(player.group)
        route = player_route(player)
        route_a_count = safe_model_field(player, 'route_a_count', 0)
        route_b_count = safe_model_field(player, 'route_b_count', 0)
        my_route_count = safe_model_field(player, 'my_route_count', 0)
        my_travel_time = safe_model_field(player, 'travel_time', 0)
        my_payoff = safe_model_field(player, 'payoff', cu(0))
        cumulative_payoff = cumulative_payoff_so_far(player)
        current_payoff_pct = bounded_percent(my_payoff, C.BASE_POINTS)
        cumulative_payoff_pct = bounded_percent(
            cumulative_payoff,
            C.BASE_POINTS * max(1, player.round_number),
        )
        current_payoff_min_width_px = 18 if current_payoff_pct > 0 else 0
        cumulative_payoff_min_width_px = 18 if cumulative_payoff_pct > 0 else 0
        reward_trend_data = build_reward_trend_svg(player)
        reward_average_data = current_round_average_reference(player)
        total_players = route_a_count + route_b_count
        route_label = {
            'A': '路线 A（主干道）',
            'B': '路线 B（环线）',
        }.get(route, '系统随机分配中')
        congestion_ratio = 0
        if total_players > 0:
            congestion_ratio = round(my_route_count / total_players * 100)

        travel_time_if_a = route_time('A', route_a_count, route_b_count, total_players)
        travel_time_if_b = route_time('B', route_a_count, route_b_count, total_players)

        return dict(
            total_players=total_players,
            route_a_count=route_a_count,
            route_b_count=route_b_count,
            route_label=route_label,
            my_route_count=my_route_count,
            congestion_ratio=congestion_ratio,
            my_travel_time=my_travel_time,
            travel_time_if_a=travel_time_if_a,
            travel_time_if_b=travel_time_if_b,
            my_payoff=my_payoff,
            cumulative_payoff_so_far=cumulative_payoff,
            current_payoff_pct=current_payoff_pct,
            cumulative_payoff_pct=cumulative_payoff_pct,
            current_payoff_min_width_px=current_payoff_min_width_px,
            cumulative_payoff_min_width_px=cumulative_payoff_min_width_px,
            auto_advance_seconds=Results.get_timeout_seconds(player),
            **reward_trend_data,
            **reward_average_data,
        )

def custom_export(players):
    yield EXPORT_HEADERS
    for p in players:
        yield export_row_for_player(p)


page_sequence = [MyPage, ResultsSync, Results]
