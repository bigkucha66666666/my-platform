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
            expect('论文式单瓶颈示意', 'in', self.html)
            expect('单瓶颈闸口', 'in', self.html)
            expect('固定服务率：单位时间限量通行', 'in', self.html)
            expect('双车道汇入单车道', 'not in', self.html)
            expect('真实车道收窄示意', 'not in', self.html)
            expect('排队小汽车', 'in', self.html)
            expect('等待与早到/晚到都会转化为成本', 'not in', self.html)
            expect('无排队基准', 'not in', self.html)
            expect(' 成本分', 'not in', self.html)
            yield Submission(Introduction, check_html=False)
            expect('场景计算题', 'in', self.html)
            expect('排队成本 = 4 分钟 × 2 = 8 成本', 'in', self.html)
            expect('排队时间 = 队列长度 ÷ 瓶颈服务率', 'in', self.html)
            expect('预计到达 = 07:54 + 6 + 6 = 08:06', 'in', self.html)
            expect('最大排队时间的一半', 'not in', self.html)
            expect('收费不是提示文字，会计入最终选择成本', 'not in', self.html)
            expect('无排队基准', 'not in', self.html)
            expect(' 成本分', 'not in', self.html)
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

        expect('相对于准时到达且无排队延误', 'not in', self.html)
        expect('相对基准说明', 'not in', self.html)
        expect('无排队基准', 'not in', self.html)
        expect(' 成本分', 'not in', self.html)
        expect('粗收费已自动校准', 'not in', self.html)

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
        expect('所有参与者的成本分布', 'in', self.html)
        expect('柱高表示该出发时间参与者的平均成本', 'in', self.html)
        expect('柱顶人数表示该时间的选择人数', 'in', self.html)
        expect('所有参与者平均成本', 'in', self.html)
        expect('你的选择', 'in', self.html)
        expect('散点为当前轮同组参与者成本', 'not in', self.html)
        expect('当前轮同组成本分布', 'not in', self.html)
        expect('同组成本变化', 'not in', self.html)
        expect('个人成本变化', 'not in', self.html)
        expect('>R1<', 'not in', self.html)
        expect('>R2<', 'not in', self.html)
        expect('论文式单瓶颈路径', 'in', self.html)
        expect('单瓶颈闸口', 'in', self.html)
        expect('固定服务率：单位时间限量通行', 'in', self.html)
        expect('双车道汇入单车道', 'not in', self.html)
        expect('真实车道收窄示意', 'not in', self.html)
        expect('排队小汽车', 'in', self.html)
        expect('同组车辆排队等待通过', 'not in', self.html)
        expect('收窄产生排队成本', 'not in', self.html)
        expect('轮已完成，看看你这轮选择带来的成本', 'not in', self.html)
        expect('本轮各出发时间选择分布表', 'not in', self.html)
        expect('本轮各出发时间选择人数', 'not in', self.html)
        expect('result-overview-breakdown', 'in', self.html)
        expect('route-metric-grid', 'not in', self.html)
        expect('result-overview-chip-key">收费', 'not in', self.html)
        expect(' 成本分', 'not in', self.html)
        yield Submission(Results, check_html=False)

        expect(self.player.departure_slot, 'in', available_slots)
        expect(player_departure_minute(self.player), '==', chosen_minute)
        expect(float(self.player.payoff), '>=', 0)

        if self.case == 'same_time':
            group_players = self.group.get_players()
            interval = C.SLOT_SIZE_MINUTES / self.session.config.get(
                'bottleneck_capacity_per_slot',
                C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
            )
            expected_delays = [round(index * interval, 2) for index in range(len(group_players))]
            actual_delays = sorted(round(player.queue_delay_minutes, 2) for player in group_players)
            expect(actual_delays, '==', expected_delays)

        if self.round_number == C.NUM_ROUNDS:
            expect('single_bottleneck_total_payoff', 'in', self.participant.vars)
