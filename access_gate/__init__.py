from otree.api import *


doc = """
正式会话入口口令验证页。
"""


class C(BaseConstants):
    NAME_IN_URL = 'access_gate'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 1


class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    pass


class Player(BasePlayer):
    access_password = models.StringField(label='请输入实验口令')


class AccessGate(Page):
    form_model = 'player'
    form_fields = ['access_password']

    @staticmethod
    def error_message(player: Player, values):
        expected = player.session.config.get('participant_password')
        if not expected:
            return '系统未配置实验口令，请联系管理员。'
        if values['access_password'] != expected:
            return '口令错误，请重试。'

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        player.participant.vars['access_granted'] = True


page_sequence = [AccessGate]
