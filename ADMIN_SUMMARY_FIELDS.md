# 管理员汇总字段说明（route_choice + payment_info）

本文用于说明管理员在导出数据后，哪些字段用于核对参与者身份、轮次行为和最终奖励。

## 一、推荐的核心汇总字段

- participant_label: 参与者标签（例如 P001-P050），用于和发放名单对齐。
- participant_code: 系统参与者唯一编码，作为兜底唯一标识。
- session_code: 会话标识，用于区分不同实验批次。
- round_number: 轮次编号（1-10）。
- route: 本轮路线选择（A 或 B）。
- travel_time: 本轮通行时间。
- route_a_count: 本轮路线 A 总人数。
- route_b_count: 本轮路线 B 总人数。
- payoff: 本轮奖励 points。
- final_total_payoff: 10轮累计奖励 points（在 payment_info 应用中记录，便于最终发放）。

## 二、管理员核对建议

1. 身份核对
- 先用 participant_label 作为主键。
- 如果标签缺失或重复，使用 participant_code 进行二次核对。

2. 过程核对
- 按 participant_label + round_number 排序。
- 正常情况下每位参与者应有 10 条 route_choice 记录（round_number 1 到 10）。

3. 最终奖励核对
- final_total_payoff 应等于该参与者 10 条 route_choice 记录中的 payoff 求和。
- 建议随机抽查 3-5 位参与者手工核对一次。

## 三、字段来源位置（代码）

- route_choice 行为与单轮奖励字段定义：
  - my_platform/route_choice/__init__.py
- 10轮累计奖励写入 participant.vars：
  - my_platform/route_choice/__init__.py
- 最终奖励字段 final_total_payoff（可导出）：
  - my_platform/payment_info/__init__.py
- 最终奖励展示页面：
  - my_platform/payment_info/PaymentInfo.html

## 四、发放建议

1. 以 final_total_payoff 作为最终发放依据。
2. 先导出并冻结一份原始数据，再生成发放表。
3. 保留 participant_label、participant_code、final_total_payoff 三列用于审计留痕。
