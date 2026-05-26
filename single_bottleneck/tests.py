from otree.api import Bot, Submission, expect

from . import (
    C,
    COARSE_TOLL_AUTO_RESULT_VAR,
    COMPREHENSION_SEEN_VAR,
    ComprehensionCheck,
    Decision,
    Introduction,
    Results,
    ResultsSync,
    departure_minute_for_player_slot,
    departure_slots_for_player,
    player_departure_minute,
)


class PlayerBot(Bot):
    cases = ['staggered', 'same_time']

    def play_round(self):
        if self.round_number == 1:
            expect('真实车道收窄示意', 'in', self.html)
            expect('双车道汇入单车道', 'in', self.html)
            expect('排队小汽车', 'in', self.html)
            expect('等待与早到/晚到都会转化为成本', 'not in', self.html)
            yield Submission(Introduction, check_html=False)
            expect('场景计算题', 'in', self.html)
            expect('排队成本 = 4 分钟 × 2 = 8 成本分', 'in', self.html)
            expect('前方排队人数', 'in', self.html)
            expect('预计到达 = 07:54 + 6 + 6 = 08:06', 'in', self.html)
            expect('收费不是提示文字，会计入最终选择成本', 'not in', self.html)
            yield Submission(ComprehensionCheck, check_html=False)
            expect(self.participant.vars.get(COMPREHENSION_SEEN_VAR), '==', True)
            if self.session.config.get('coarse_toll_auto_enabled'):
                auto_toll_result = self.participant.vars.get(COARSE_TOLL_AUTO_RESULT_VAR, {})
                expect(auto_toll_result.get('calibration_source'), '==', 'cache')

        available_slots = departure_slots_for_player(self.player)
        if self.case == 'same_time':
            chosen_slot = available_slots[len(available_slots) // 2]
        else:
            chosen_slot = available_slots[(self.player.id_in_group + self.round_number - 2) % len(available_slots)]
        chosen_minute = departure_minute_for_player_slot(self.player, chosen_slot)

        yield Submission(
            Decision,
            dict(departure_minute=chosen_minute),
            check_html=False,
        )

        yield Submission(ResultsSync, check_html=False)
        expect('你的本轮用时', 'in', self.html)
        expect('自由流行程', 'in', self.html)
        expect('本轮成本概览', 'in', self.html)
        expect('A 出发', 'in', self.html)
        expect('B 到达', 'in', self.html)
        expect('出发时间：', 'not in', self.html)
        expect('到达时间：', 'not in', self.html)
        expect('总行程时间：', 'not in', self.html)
        expect('同一分钟选择人数：', 'not in', self.html)
        expect('当前轮同组成本分布', 'in', self.html)
        expect('同组平均成本', 'in', self.html)
        expect('当前参与者：你', 'in', self.html)
        expect('同组成本变化', 'not in', self.html)
        expect('个人成本变化', 'not in', self.html)
        expect('>R1<', 'not in', self.html)
        expect('>R2<', 'not in', self.html)
        expect('真实车道收窄示意', 'in', self.html)
        expect('双车道汇入单车道', 'in', self.html)
        expect('排队小汽车', 'in', self.html)
        expect('同组车辆排队等待通过', 'not in', self.html)
        expect('收窄产生排队成本', 'not in', self.html)
        expect('轮已完成，看看你这轮选择带来的成本', 'not in', self.html)
        expect('本轮各出发时间选择分布表', 'not in', self.html)
        yield Submission(Results, check_html=False)

        expect(self.player.departure_slot, 'in', available_slots)
        expect(player_departure_minute(self.player), '==', chosen_minute)
        expect(float(self.player.payoff), '>=', 0)

        if self.case == 'same_time':
            group_players = self.group.get_players()
            expect(len({round(player.queue_delay_minutes, 2) for player in group_players}), '==', 1)
            expect(len({player.arrival_time_label for player in group_players}), '==', 1)
            expect(len({round(player.schedule_early_minutes, 2) for player in group_players}), '==', 1)
            expect(len({round(player.schedule_late_minutes, 2) for player in group_players}), '==', 1)
            expect(len({float(player.payoff) for player in group_players}), '==', 1)

        if self.round_number == C.NUM_ROUNDS:
            expect('single_bottleneck_total_payoff', 'in', self.participant.vars)
