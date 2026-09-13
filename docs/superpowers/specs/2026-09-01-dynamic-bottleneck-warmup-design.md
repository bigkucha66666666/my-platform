# 动态瓶颈两轮热身设计

## 目标

在 `dynamic_bottleneck_round` 中增加 2 轮热身，随后保留完整的 60 轮正式实验。热身用于熟悉选择、收费和结果反馈，不污染正式数据、正式收益或 Agent 学习状态。

## 轮次与服务率

- oTree 总轮次为 62：原始轮次 1–2 为热身，3–62 映射为正式第 1–60 轮。
- 热身服务率通过 `dynamic_warmup_capacity=2` 配置，两轮固定且在决策前公布。
- 热身服务率必须为正数，且必须属于 `dynamic_capacity_values` 候选集合。
- 正式服务率序列只生成 60 个值；前 20 轮随机、后 40 轮 Markov 规律的分界不变。
- 正式第 1 轮的“上一轮服务率”为“无”，不将热身服务率当作正式历史。

## 页面流程

- 第 1 轮：`Introduction -> ComprehensionCheck -> WarmupStart -> RoundStartSync -> Decision -> ResultsSync -> Results`。
- 第 2 轮：正常热身决策和反馈。
- 第 3 轮：`FormalStart -> RoundStartSync -> Decision -> ResultsSync -> Results`，其中 `FormalStart` 明确提示热身已结束，正式 60 轮开始。
- 决策、等待和结果页使用“热身第 X/2 轮”或“正式第 X/60 轮”，不向参与者显示 62 的内部轮次。

## 数据、收益与 Agent 隔离

- 热身轮仍计算排队、到达时间和成本，以便显示完整反馈，但 `payoff` 固定为 0。
- 最终累计收益只汇总原始轮次 3–62。
- `custom_export` 和后台报告排除原始轮次 1–2，并将导出轮次重编为 1–60。
- LLM 和 RL 可参与热身拥堵计算，但热身不更新受限记忆、RL shadow 或独立 RL 状态。
- 正式第 1 轮的 Agent 历史为空；从正式第 2 轮开始，只读取上一个正式轮次。

## 验证

- 测试轮次映射、热身固定服务率、正式序列长度和首轮历史隔离。
- 测试热身不进入自定义导出、后台报告和最终收益。
- 测试热身开始/结束提示页的显示条件和文案。
- 运行 62 轮 oTree bot，并回归动态场景和旧单瓶颈测试。
