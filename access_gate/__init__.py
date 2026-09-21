import time

from otree.api import *
from .strict_room_labels import install_strict_room_label_lookup


install_strict_room_label_lookup()


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
        expected_label = player.participant.vars.get('expected_room_label')
        actual_label = player.participant.label
        if expected_label and actual_label != expected_label:
            return (
                f'当前入口标签不正确，请使用 {expected_label} '
                '进入实验房间。'
            )

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        participant_vars = player.participant.vars
        participant_vars['access_granted'] = True
        participant_vars.setdefault('access_granted_at_ts', time.time())


page_sequence = [AccessGate]
