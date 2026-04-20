# single_bottleneck 实验说明

这个 app 对应一个离散化的单瓶颈出发时间实验：

- 每轮每个参与者选择一个出发时点。
- 所有人共享同一个瓶颈，瓶颈容量有限，会形成排队。
- 实际到达时间由 `出发时间 + 自由流通行时间 + 排队延误` 决定。
- 收益由 `基础分 - 排队成本 - 早到成本 - 晚到成本 + 可选奖励补贴` 决定。

## 已完成的实现

- 独立 app：`single_bottleneck`
- 多轮重复：当前固定为 10 轮
- 正式/演示两套 session config
- 可选奖励处理
- 自动分组 / 正式场次手动分组
- 超时自动补填、断线后自动补填、恢复后继续作答
- admin report 与 custom export
- `payment_info` 可按 session config 读取累计收益变量

## 当前 session config 参数

在 `settings.py` 里已经加入以下可配项：

- `reward_treatment_enabled`
  - `0` 关闭奖励处理
  - `1` 开启奖励处理
- `rewarded_slot_spec`
  - 指定哪些出发时点有奖励
  - 示例：`1-3,9-11`
- `reward_bonus_points`
  - 奖励时点的额外收益
- `bottleneck_capacity_per_slot`
  - 每个离散时点窗口可通过的人数
- `cohort_size`
  - 自动分组时每组人数
- `grouping_enabled`
  - 正式场次是否启用手动分组
- `manual_grouping_spec`
  - 手动分组标签串，例如 `P001,P003|P002,P004|P005`

## 当前写死在代码里的核心参数

这些参数目前写在 `single_bottleneck/__init__.py` 的 `C` 里：

- `NUM_ROUNDS = 10`
- `PREFERRED_ARRIVAL_MINUTE = 8 * 60`
- `FREE_FLOW_TRAVEL_MINUTES = 6`
- `SLOT_SIZE_MINUTES = 2`
- `NUM_DEPARTURE_SLOTS = 11`
- `BASE_POINTS = 140`
- `QUEUE_COST_PER_MINUTE = 2`
- `EARLY_COST_PER_MINUTE = 1`
- `LATE_COST_PER_MINUTE = 3`

这组默认值遵循标准单瓶颈机制的结构：

- 排队成本为正
- 早到成本低于排队成本
- 晚到成本高于排队成本

如果你要严格复刻某篇文献，下一步优先改这几个常数。

## 时点含义

当前离散出发时点围绕“无拥堵基准出发时刻”展开：

- 目标到达时间：`08:00`
- 自由流通行时间：`6` 分钟
- 无拥堵基准出发：`07:54`
- 一共 11 个时点
- 相邻时点间隔 2 分钟

因此当前默认时点覆盖：

- 从 `07:44`
- 到 `08:04`

## 收益计算

对任一参与者：

1. 根据所选时点进入对应队列
2. 结合瓶颈容量决定何时能通过瓶颈
3. 得到排队延误 `queue_delay`
4. 得到实际到达时间 `arrival`
5. 计算：
   - `early = max(0, preferred_arrival - arrival)`
   - `late = max(0, arrival - preferred_arrival)`
6. 收益：
   - `payoff = base_points - queue_cost * queue_delay - early_cost * early - late_cost * late + reward_bonus`

## 你后面最可能改的地方

如果你想做文献复刻，优先改：

1. `single_bottleneck/__init__.py` 里的 `C` 参数
2. `settings.py` 里的奖励处理 session config
3. `Introduction.html` 和 `Decision.html` 的说明文案

如果你想做 treatment 扩展，优先改：

1. `rewarded_slot_spec`
2. `reward_bonus_points`
3. 新增一套独立 `SESSION_CONFIG`

## 当前限制

- 我没有在当前环境里运行 `otree check`，因为本地解释器没有安装 `otree`，而你刚刚明确说了不要下载。
- 所以目前完成的是：
  - 代码结构落地
  - Python 静态编译通过
  - 模板和参数入口已经接好

如果你下一步要做“严格贴文献参数”，我建议直接给我那篇文献里明确的：

- 组人数
- 时点数量
- 每时点容量
- 早到/晚到/排队成本
- 奖励处理针对哪些时点、奖励多少

我可以把这套实现直接改成文献同款。
