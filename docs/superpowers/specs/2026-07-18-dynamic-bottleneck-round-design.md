# 整轮随机瓶颈服务率实验设计

## 目标

新建独立 oTree app `dynamic_bottleneck_round`。参与者仍在每轮选择 1 分钟精度的出发时间，但瓶颈服务率按小组和轮次随机确定。同组同轮服务率完全相同，不同轮可不同。

## 边界

- 新 app 拥有独立的 `Subsession`、`Group`、`Player` 与模板。
- 不导入 `single_bottleneck` 的模型类，不修改其业务逻辑。
- 复用其经验参数：10 轮、1 分钟出发精度、固定行驶时间、点排队、早到/晚到成本、超时代填和结果同步。
- v1 不接入 API Agent 或自动粗收费校准。奖励和粗收费保留手动配置接口，两个默认场景均关闭。

## 随机服务率

Session config 提供：

- `dynamic_capacity_values`：逗号分隔的正整数，表示每个 1 分钟服务时间槽最多通过人数。
- `dynamic_capacity_probabilities`：与状态一一对应的概率。
- `dynamic_capacity_seed`：基础随机种子。
- `dynamic_capacity_draw_mode`：`balanced_shuffle` 或 `iid`。
- `capacity_reveal_timing`：`before_decision` 或 `after_decision`。

`balanced_shuffle` 对概率乘总轮数后使用最大余数法分配整数次数，再用组级种子打乱。`iid` 每轮独立抽取。组级种子由基础种子和小组编号确定，因此同一配置可复现。

## 保存与计算

`Group` 保存当轮真实服务率、状态和概率。`Player` 同步保存要求的动态字段，保证每名参与者每轮都能直接导出。

排队按出发分钟排序。同一出发分钟的参与者使用相同的批次最大等待时间：

`(本批次所需服务时间槽数 - 1) × 1 分钟 + 前方遗留等待`。

成本为固定行驶成本、排队成本、早到成本、晚到成本与可选粗收费之和；奖励只增加 payoff，不抵扣展示的出行成本。

## 页面

- `Introduction`：展示动态服务率规则和所有状态/概率。
- `ComprehensionCheck`：包含“同组同轮容量是否相同”以及容量对排队的计算题。
- `RoundStartSync`：每轮决策前记录已到达参与者。全组到齐后统一设置决策开始与截止时间；第 1 轮最长等待 120 秒，后续轮次最长等待 60 秒，到期自动开始，避免个别掉线者阻塞全组。
- `Decision`：决策前公布模式显示真实服务率；决策后公布模式只显示状态和概率。
- `ResultsSync`：等待同组决策完成，并在截止时间后代填缺失决策。同步页统一每 3 秒轮询一次，降低大组并发请求。
- `Results`：显示本轮/上轮服务率、个人时间、各项成本、payoff 和匿名组内出发时间分布。

`Decision` 不再由首位访问者触发本轮计时。决策截止时间由 `RoundStartSync` 在全组到齐或最长等待到期时统一写入，迟到参与者只能使用本轮剩余决策时间。

## 后台与导出

`custom_export` 按参与者每轮输出指定字段。`vars_for_admin_report` 聚合每轮容量、平均排队、平均成本、出发时间分布和容量状态次数。

## 错误处理

Session 创建时严格校验容量、概率、抽取模式和公布时点。任一配置非法都立即拒绝创建 session，并在错误中指明字段和原因。
