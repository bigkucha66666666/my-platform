from otree.api import *


NOTICE_ROUND_CHOICES = [
    ['none', '未发现明显变化'],
    *[[str(round_number), f'正式第 {round_number} 轮'] for round_number in range(1, 31)],
]

PATTERN_CHOICES = [
    [1, '完全没有规律'],
    [2, '比较没有规律'],
    [3, '不确定'],
    [4, '有一定规律'],
    [5, '规律非常明显'],
]

REFERENCE_CHOICES = [
    [1, '完全不参考'],
    [2, '很少参考'],
    [3, '一般'],
    [4, '经常参考'],
    [5, '非常依赖'],
]

FREQUENCY_CHOICES = [
    [1, '完全不会'],
    [2, '很少'],
    [3, '偶尔'],
    [4, '经常'],
    [5, '几乎每轮都会'],
]

ADJUSTMENT_CHOICES = [
    [1, '完全不会'],
    [2, '很少'],
    [3, '偶尔'],
    [4, '经常'],
    [5, '几乎一定会'],
]

AGREEMENT_CHOICES = [
    [1, '完全不同意'],
    [2, '比较不同意'],
    [3, '不确定'],
    [4, '比较同意'],
    [5, '完全同意'],
]

AGENT_INFLUENCE_CHOICES = [
    [1, '完全没有影响'],
    [2, '影响较小'],
    [3, '一般'],
    [4, '影响较大'],
    [5, '影响非常大'],
]

AGENT_PREDICTABILITY_CHOICES = [
    [1, '明显更难预测'],
    [2, '稍微更难预测'],
    [3, '没有明显变化'],
    [4, '稍微更容易预测'],
    [5, '明显更容易预测'],
]

COMMON_STRATEGY_FIELDS = [
    'reference_previous_capacity',
    'predict_next_capacity',
    'adjust_after_high_cost',
    'expect_capacity_persistence',
]

AGENT_FIELDS = [
    'agent_choice_influence',
    'agent_predictability_effect',
]

EXPORT_HEADERS = [
    'session_code',
    'participant_code',
    'participant_label',
    'dynamic_group_id',
    'dynamic_group_label',
    'treatment_group',
    'api_agent_count',
    'rl_agent_count',
    'pattern_recognition',
    'noticed_pattern_round',
    'noticed_pattern_round_label',
    'pattern_description',
    'reference_previous_capacity',
    'predict_next_capacity',
    'adjust_after_high_cost',
    'expect_capacity_persistence',
    'agent_choice_influence',
    'agent_predictability_effect',
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
    api_agent_count = models.IntegerField(initial=0)
    rl_agent_count = models.IntegerField(initial=0)

    pattern_recognition = models.IntegerField(
        label='你是否感觉实验后半段的瓶颈服务率变化存在一定规律？',
        choices=PATTERN_CHOICES,
        widget=widgets.RadioSelect,
    )
    noticed_pattern_round = models.StringField(
        label='如果你感觉服务率变化规律发生过变化，大约从第几轮开始注意到？',
        choices=NOTICE_ROUND_CHOICES,
    )
    pattern_description = models.LongStringField(
        label='请用一句话描述你认为实验后半段服务率的变化规律。',
    )
    reference_previous_capacity = models.IntegerField(
        label='你做本轮出发时间选择时，会多大程度参考上一轮的服务率？',
        choices=REFERENCE_CHOICES,
        widget=widgets.RadioSelect,
    )
    predict_next_capacity = models.IntegerField(
        label='你是否会根据过去几轮的服务率变化，预测下一轮可能出现的服务率？',
        choices=FREQUENCY_CHOICES,
        widget=widgets.RadioSelect,
    )
    adjust_after_high_cost = models.IntegerField(
        label='上一轮成本较高或排队较严重时，你是否会在下一轮改变出发时间？',
        choices=ADJUSTMENT_CHOICES,
        widget=widgets.RadioSelect,
    )
    expect_capacity_persistence = models.IntegerField(
        label='当上一轮服务率较低时，你提前出发主要是因为你认为下一轮服务率仍可能较低。',
        choices=AGREEMENT_CHOICES,
        widget=widgets.RadioSelect,
    )
    agent_choice_influence = models.IntegerField(
        label='自动决策主体（Agent）的存在是否影响了你的出发时间选择？',
        choices=AGENT_INFLUENCE_CHOICES,
        widget=widgets.RadioSelect,
        blank=True,
    )
    agent_predictability_effect = models.IntegerField(
        label='你认为 Agent 的加入使整个交通环境变得：',
        choices=AGENT_PREDICTABILITY_CHOICES,
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


def copy_experiment_metadata(player):
    player.dynamic_group_id = int(
        player.participant.vars.get('assigned_group_id', 0) or 0
    )
    player.dynamic_group_label = str(
        player.participant.vars.get('assigned_group_label', '') or ''
    )
    player.treatment_group = treatment_group_for_player(player)
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


class PatternRecognition(Page):
    form_model = 'player'
    form_fields = [
        'pattern_recognition',
        'noticed_pattern_round',
        'pattern_description',
    ]

    @staticmethod
    def vars_for_template(player):
        return survey_context(player, 1)


class StrategySurvey(Page):
    form_model = 'player'

    @staticmethod
    def get_form_fields(player):
        fields = list(COMMON_STRATEGY_FIELDS)
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
            'answered_questions': 9 if player.treatment_group == 'HA' else 7,
            'has_payment_page': 'payment_info' in player.session.config.get(
                'app_sequence', []
            ),
        }


def noticed_round_label(player):
    value = player.field_maybe_none('noticed_pattern_round')
    if value == 'none':
        return '未发现明显变化'
    if value:
        return f'正式第 {value} 轮'
    return ''


def custom_export(players):
    yield EXPORT_HEADERS
    for player in players:
        yield [
            player.session.code,
            player.participant.code,
            player.participant.label or '',
            player.dynamic_group_id,
            player.dynamic_group_label,
            player.treatment_group,
            player.api_agent_count,
            player.rl_agent_count,
            player.field_maybe_none('pattern_recognition') or '',
            player.field_maybe_none('noticed_pattern_round') or '',
            noticed_round_label(player),
            player.field_maybe_none('pattern_description') or '',
            player.field_maybe_none('reference_previous_capacity') or '',
            player.field_maybe_none('predict_next_capacity') or '',
            player.field_maybe_none('adjust_after_high_cost') or '',
            player.field_maybe_none('expect_capacity_persistence') or '',
            player.field_maybe_none('agent_choice_influence') or '',
            player.field_maybe_none('agent_predictability_effect') or '',
        ]


page_sequence = [PatternRecognition, StrategySurvey, SurveyComplete]
