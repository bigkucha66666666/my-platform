# 动态瓶颈服务率场景粗收费与参与者信息同步设计

## 目标

使 `dynamic_bottleneck_round` 与 `single_bottleneck` 使用相同的点排队、成本、粗收费和参与者信息口径。动态场景唯一的核心实验变化仍是：同组同轮服务率固定，不同轮次按预设序列变化。

新场景保持独立模型和独立运行，不导入 `single_bottleneck` 的模型类，也不读取其参与者数据。

## 实验口径

- 目标到达时间、固定行驶时间、固定行驶成本、排队成本、早到成本、晚到成本和基础收益与 `single_bottleneck` 保持一致。
- 同一分钟出发的参与者构成同一批次，使用相同的完全清队等待时间。
- 前序批次未清空的队列继续传递给后续批次。
- 粗收费直接加入最终选择成本；奖励只增加 payoff，不抵扣展示成本。
- 默认继续关闭奖励，开启自动粗收费。

## 出发时间范围

每个小组在 Session 创建时生成一次共同出发时间范围，之后所有轮次保持不变，避免可选范围随本轮服务率变化而泄露状态。

范围按组内人数和候选服务率中的最小值生成：

1. `required_occupied_slots = ceil(group_players / min(dynamic_capacity_values))`
2. `slots_each_side = max(departure_schedule_min_slots_each_side, ceil(required_occupied_slots / 2))`
3. 时间范围围绕“目标到达时间减固定行驶时间”对称展开，选择精度为 1 分钟。

该口径确保最低服务率下仍有足够的时间选择空间，同时 before/after 两种公布模式看到完全相同的时间范围。

## 自动粗收费校准

### 校准时机

Session 第 1 轮完成分组和出发时间范围生成后，为每种唯一组合预校准：

`(组内人数, 候选服务率, 出发时间范围, 搜索参数)`

相同组合只计算一次，随后在同一 Session 内复用。每轮根据真实服务率选择对应收费配置，不在参与者进入页面时重新运行搜索。

### 校准目标

- 搜索一个围绕无拥堵准时出发时刻对称的连续收费窗口。
- 收费窗口内使用统一固定收费额。
- 均衡判断采用当前点模型和完全清队等待规则。
- 候选结果优先最小化均衡中被选择时点的最终选择成本差距。
- 小规模采用精确搜索，大规模自动采用近似搜索。

### 保存位置

动态 app 使用自己的收费结果键保存容量状态映射，不复用单瓶颈参与者变量：

```text
participant.vars['dynamic_bottleneck_round_toll_by_capacity']
```

映射中的每个服务率保存：收费窗口、收费金额、校准人数、服务率、模式、来源、成本差距、偏离差距、均衡数量和均衡摘要。每轮将实际生效值复制到 `Player` 字段，以便结果计算和导出不依赖临时变量。

### 缓存

新增动态 app 自有缓存文件。缓存匹配必须同时校验人数、服务率、时间范围、1 分钟选择精度、收费搜索范围、同分钟排队规则和双侧收费窗口规则。条件不匹配时忽略缓存并重新计算。

### 失败策略

自动校准失败时立即阻止 Session 创建，并报告组号、人数、服务率、时点数、校准模式和搜索范围。不得回退到手动收费或无收费。

## 公布规则

### 决策前公布

`capacity_reveal_timing='before_decision'` 时允许自动粗收费。决策页同时显示：

- 本轮真实服务率；
- 本轮实际收费时间范围；
- 收费金额；
- 时间滚轮中每个时间对应的“无收费”或“收费 X”。

### 决策后公布

`capacity_reveal_timing='after_decision'` 与自动粗收费不兼容。Session 创建时若同时设置 `coarse_toll_auto_enabled=1`，直接给出明确配置错误。

决策后公布模式仅允许手动粗收费，且所有服务率状态使用同一时间窗口和收费金额。参与者决策前可看到该统一收费规则，但看不到真实服务率。

## 参与者页面同步

### Introduction

沿用单瓶颈的成本中心结构，精简展示：

- 每轮选择出发时间并经过同一单一瓶颈；
- 同一分钟参与者采用相同等待时间；
- 固定行驶、排队、早到、晚到和粗收费构成最终选择成本；
- 服务率在不同轮次可能变化，同组同轮保持一致；
- 候选服务率及 balanced shuffle 下的目标比例；
- 本组统一出发时间范围和自动收费说明。

`balanced_shuffle` 使用“目标比例”，不得写成每轮独立出现概率；`iid` 才使用“每轮抽取概率”。

### ComprehensionCheck

同步单瓶颈参与者需要掌握的计算信息，包含四题：

1. 排队成本计算；
2. 早到或晚到成本计算；
3. 给定服务率和同分钟人数，计算统一等待与到达时间；
4. 判断同组同轮服务率是否相同，并识别收费如何进入最终选择成本。

题目答案不导出；参与者检查答案并阅读解释后可继续。

### Decision

采用与单瓶颈一致的时间滚轮、键盘/箭头操作、倒计时和提交结构。滚轮项目只显示时间与收费信息，不显示相对基准说明。

before 模式顶部醒目显示真实服务率；after 模式只显示候选状态和目标比例/概率。奖励关闭时不展示奖励信息。

### Results

采用单瓶颈的成本中心表达，不在本轮结果卡片突出 payoff。显示：

- 本轮和上一轮服务率；
- 出发时间、到达时间、排队时间、同分钟人数和总行程时间；
- 固定行驶成本、排队成本、早到成本、晚到成本和粗收费；
- 最终选择成本；
- 当前轮组内各出发时间的平均成本和人数，当前参与者选择高亮。

payoff 继续后台计算并累计，在最终支付页面统一展示。

### 同步页面

保留 `RoundStartSync` 的统一开轮机制和 3 秒轮询。`ResultsSync` 的用词、视觉层级和异常说明与单瓶颈保持一致，但不得改变已有 120/60 秒开轮等待和共享决策截止时间。

## 配置

动态场景增加并默认使用：

```python
departure_schedule_auto_enabled=1
departure_schedule_min_slots_each_side=10
coarse_toll_auto_enabled=1
coarse_toll_auto_min_toll=0
coarse_toll_auto_max_toll=40
coarse_toll_auto_toll_step=1
coarse_toll_auto_mode='auto'
coarse_toll_auto_approx_refine_pool_size=8
coarse_toll_auto_approx_refine_iterations=160
coarse_toll_enabled=1
coarse_toll_time_window_spec='07:53-07:55'
coarse_toll_points=3
```

最后三个手动参数仅在关闭自动校准时生效。

## 数据与导出

在现有动态字段基础上增加每人每轮实际生效的收费信息：

- `coarse_toll_source`
- `coarse_toll_auto_enabled`
- `coarse_toll_calibration_source`
- `coarse_toll_calibration_mode`
- `coarse_toll_calibration_players`
- `coarse_toll_calibration_capacity`
- `coarse_toll_calibration_cost_gap`
- `coarse_toll_calibration_deviation_gap`
- `coarse_toll_calibration_nash_count`
- `coarse_toll_equilibrium_distribution`
- `coarse_toll_equilibrium_costs`
- `coarse_toll_enabled`
- `coarse_toll_slot_spec`
- `coarse_toll_time_window_spec`
- `coarse_toll_points`
- `coarse_toll_charge`
- 实际出发时间范围、时点数量和范围生成依据

后台报告按轮展示真实服务率、实际收费窗口、收费额、平均收费、平均排队和平均最终选择成本。

## 测试

- 同一人数、同一服务率、同一时间范围复用同一校准结果。
- 不同服务率能使用不同收费配置。
- 同组同轮所有参与者使用相同收费窗口和金额。
- 收费正确加入 `total_cost` 并从 payoff 中扣除。
- before 模式显示真实服务率和实际收费。
- after 模式拒绝自动收费，统一手动收费不泄露服务率。
- 动态时间范围按组人数和最低服务率生成，所有轮次保持不变。
- 参与者页面包含与单瓶颈一致的成本和点排队信息。
- 缓存假设不匹配时不会被使用。
- 5 人精确模式、60/100 人大组模式、same-time 和 staggered bot 均通过。
- 原 `single_bottleneck` 测试保持通过。

## 不在本次范围

- 不接入 API Agent。
- 不修改 `single_bottleneck` 的模型、收费结果或参与者流程。
- 不让 after-decision 模式使用按真实服务率变化的自动收费。
