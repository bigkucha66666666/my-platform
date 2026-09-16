from otree.api import *


LIKERT_CHOICES = [
    [1, '完全不同意'],
    [2, '比较不同意'],
    [3, '不确定'],
    [4, '比较同意'],
    [5, '完全同意'],
]

SERVICE_RATE_UNDERSTANDING_CHOICES = [
    ['fewer_pass_more_queue', '每分钟能通过的主体更少，更容易形成排队'],
    ['more_pass_less_queue', '每分钟能通过的主体更多，更不容易形成排队'],
    ['schedule_only', '服务率只影响早到或迟到，不影响排队'],
    ['uncertain', '不确定'],
]

PREDECISION_CAPACITY_ACCESS_CHOICES = [
    ['exact_current_rate', '知道本轮的精确服务率'],
    [
        'distribution_then_reveal',
        '只知道服务率的取值范围和分布，本轮精确值在结算后才能看到',
    ],
    ['no_capacity_information', '既不知道分布，也不知道本轮精确值'],
    ['uncertain', '不确定'],
]

PRIMARY_DECISION_BASIS_CHOICES = [
    ['current_rate', '本轮显示的精确服务率'],
    ['distribution', '服务率的整体取值范围和分布'],
    ['history_cost', '前几轮的服务率、排队和个人成本'],
    ['others', '对其他参与者出发时刻的预测'],
    ['fixed_time', '一个相对固定的习惯出发时刻'],
    ['no_fixed_rule', '没有固定依据，多数时候凭感觉选择'],
]

PAGE_ONE_FIELDS = [
    'service_rate_understanding',
    'predecision_capacity_access',
    'primary_decision_basis',
]

PAGE_TWO_FIELDS = [
    'adjust_after_high_cost',
    'anticipate_others',
    'avoid_crowded_slots',
    'decision_confidence',
    'perceived_information_benefit',
    'perceived_departure_concentration',
]

AGENT_FIELDS = [
    'agent_changed_strategy',
    'expected_agent_consistency',
    'agent_induced_avoidance',
]

ANSWER_FIELDS = PAGE_ONE_FIELDS + PAGE_TWO_FIELDS + AGENT_FIELDS

EXPORT_HEADERS = [
    'session_code',
    'participant_code',
    'participant_label',
    'group_id',
    'dynamic_group_id',
    'dynamic_group_label',
    'treatment_group',
    'information_condition',
    'treatment_condition',
    'api_agent_count',
    'rl_agent_count',
    *ANSWER_FIELDS,
]


class C(BaseConstants):
    NAME_IN_URL = 'dynamic_bottleneck_survey'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 1


class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    pass


class Player(BasePlayer):
    dynamic_group_id = models.IntegerField(initial=0)
    dynamic_group_label = models.StringField(blank=True)
    treatment_group = models.StringField(blank=True)
    information_condition = models.StringField(blank=True)
    treatment_condition = models.StringField(blank=True)
    api_agent_count = models.IntegerField(initial=0)
    rl_agent_count = models.IntegerField(initial=0)

    service_rate_understanding = models.StringField(
        label='在其他条件相同时，瓶颈服务率越低，通常意味着什么？',
        choices=SERVICE_RATE_UNDERSTANDING_CHOICES,
        widget=widgets.RadioSelect,
    )
    predecision_capacity_access = models.StringField(
        label='在正式实验的每一轮，你在提交出发时刻之前可以获得哪种服务率信息？',
        choices=PREDECISION_CAPACITY_ACCESS_CHOICES,
        widget=widgets.RadioSelect,
    )
    primary_decision_basis = models.StringField(
        label='以下哪一项最符合你在大多数轮次中选择出发时刻的主要依据？',
        widget=widgets.RadioSelect,
    )
    adjust_after_high_cost = models.IntegerField(
        label='上一轮成本较高或排队较严重时，我会在下一轮改变出发时刻。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
    )
    anticipate_others = models.IntegerField(
        label='做选择时，我会考虑其他参与者可能选择哪些出发时刻。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
    )
    avoid_crowded_slots = models.IntegerField(
        label='我会主动避开自己认为可能较拥挤的出发时刻。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
    )
    decision_confidence = models.IntegerField(
        label='在大多数轮次中，我对自己的出发时刻选择有信心。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
    )
    perceived_information_benefit = models.IntegerField(
        label='实验中提供的服务率相关信息有助于我降低个人成本。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
    )
    perceived_departure_concentration = models.IntegerField(
        label='我感觉参与者的选择经常集中在少数几个出发时刻。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
    )
    agent_changed_strategy = models.IntegerField(
        label='知道本组中存在Agent后，我改变了自己的出发时刻策略。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
        blank=True,
    )
    expected_agent_consistency = models.IntegerField(
        label='我预期Agent会比人类更一致地根据服务率相关信息调整出发时刻。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
        blank=True,
    )
    agent_induced_avoidance = models.IntegerField(
        label='由于本组中存在Agent，我会更主动地避开可能拥挤的出发时刻。',
        choices=LIKERT_CHOICES,
        widget=widgets.RadioSelect,
        blank=True,
    )


def treatment_group_for_player(player):
    treatment = str(
        player.participant.vars.get('dynamic_bottleneck_treatment_group', '') or ''
    ).strip().upper()
    if treatment in {'H', 'HA'}:
        return treatment
    api_count = int(
        player.participant.vars.get('dynamic_bottleneck_api_agent_count', 0) or 0
    )
    rl_count = int(
        player.participant.vars.get('dynamic_bottleneck_rl_agent_count', 0) or 0
    )
    return 'HA' if api_count + rl_count > 0 else 'H'


def information_condition_for_player(player):
    condition = str(
        player.participant.vars.get(
            'dynamic_bottleneck_information_condition',
            '',
        )
        or ''
    ).strip().upper()
    if condition in {'I0', 'I1'}:
        return condition
    session_config = getattr(getattr(player, 'session', None), 'config', {})
    for field_name in (
        'capacity_information_condition',
        'accident_information_condition',
    ):
        condition = str(session_config.get(field_name, '') or '').strip().upper()
        if condition in {'I0', 'I1'}:
            return condition
    return ''


def primary_decision_basis_choices(player):
    choices = list(PRIMARY_DECISION_BASIS_CHOICES)
    if information_condition_for_player(player) != 'I1':
        choices = [choice for choice in choices if choice[0] != 'current_rate']
    return choices


def copy_experiment_metadata(player):
    player.dynamic_group_id = int(
        player.participant.vars.get('assigned_group_id', 0) or 0
    )
    player.dynamic_group_label = str(
        player.participant.vars.get('assigned_group_label', '') or ''
    )
    player.treatment_group = treatment_group_for_player(player)
    player.information_condition = information_condition_for_player(player)
    player.treatment_condition = (
        f'{player.treatment_group}-{player.information_condition}'
        if player.information_condition
        else player.treatment_group
    )
    player.api_agent_count = int(
        player.participant.vars.get('dynamic_bottleneck_api_agent_count', 0) or 0
    )
    player.rl_agent_count = int(
        player.participant.vars.get('dynamic_bottleneck_rl_agent_count', 0) or 0
    )


def creating_session(subsession):
    for player in subsession.get_players():
        copy_experiment_metadata(player)


def survey_context(player, step):
    copy_experiment_metadata(player)
    return {
        'survey_step': step,
        'survey_total_steps': 2,
        'show_agent_questions': player.treatment_group == 'HA',
        'treatment_label': (
            'Human + Agent 组'
            if player.treatment_group == 'HA'
            else 'Human-only 组'
        ),
    }


class UnderstandingAndStrategy(Page):
    form_model = 'player'
    form_fields = PAGE_ONE_FIELDS

    @staticmethod
    def vars_for_template(player):
        return survey_context(player, 1)


class DecisionExperience(Page):
    form_model = 'player'

    @staticmethod
    def get_form_fields(player):
        fields = list(PAGE_TWO_FIELDS)
        if treatment_group_for_player(player) == 'HA':
            fields.extend(AGENT_FIELDS)
        return fields

    @staticmethod
    def error_message(player, values):
        if treatment_group_for_player(player) != 'HA':
            return None
        errors = {
            field_name: '请回答这道题。'
            for field_name in AGENT_FIELDS
            if values.get(field_name) is None
        }
        return errors or None

    @staticmethod
    def vars_for_template(player):
        return survey_context(player, 2)


class SurveyComplete(Page):
    @staticmethod
    def vars_for_template(player):
        copy_experiment_metadata(player)
        return {
            'answered_questions': 12 if player.treatment_group == 'HA' else 9,
            'has_payment_page': 'payment_info' in player.session.config.get(
                'app_sequence', []
            ),
        }


def custom_export(players):
    yield EXPORT_HEADERS
    for player in players:
        metadata = [
            player.session.code,
            player.participant.code,
            player.participant.label or '',
            player.dynamic_group_id,
            player.dynamic_group_id,
            player.dynamic_group_label,
            player.treatment_group,
            player.information_condition,
            player.treatment_condition,
            player.api_agent_count,
            player.rl_agent_count,
        ]
        answers = [player.field_maybe_none(field_name) or '' for field_name in ANSWER_FIELDS]
        yield metadata + answers


page_sequence = [UnderstandingAndStrategy, DecisionExperience, SurveyComplete]
