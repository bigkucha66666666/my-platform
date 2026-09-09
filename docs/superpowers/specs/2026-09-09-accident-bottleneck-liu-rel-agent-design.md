# 事故风险动态瓶颈 Liu-REL Agent 代码改造交接规范

## 1. 文档用途

本文档用于将 `dynamic_bottleneck_round` 中现有的独立 RL Agent 改造为“基于 Liu et al.（2023）的事故信息条件化强化学习模型”。接手者应按照本文档修改代码、补充测试并更新必要的技术说明。

本次目标是修改**独立 RL 参与者**的决策模型，不修改事故容量生成、Human 页面、LLM 决策模型、排队结算或正式支付逻辑。

## 2. 已确认的研究设计

- 实验环境：事故风险动态单瓶颈；
- 正常服务率：4.0；
- 事故概率：0.20；
- 事故容量损失：$L\sim\operatorname{Beta}(6.83057,4.05907)$；
- 事故轮服务率：$s=4(1-L)$；
- 行动空间：16 个一分钟间隔的出发时刻，07:46-08:01；
- 轮次：5 轮练习加 60 轮正式实验；
- 练习轮不进入 RL 学习状态；
- 主体构成：Human-only 为 20 Human，Human-Agent 为 16 Human + 2 LLM + 2 RL；
- 信息条件：I0、I1、I2；
- 学习方式：RL 在 60 轮正式实验中从统一初始状态开始在线学习，不使用预训练策略；
- 参数来源：通过独立 Pilot 校准，并在正式实验前冻结；
- 两个 RL Agent 使用独立状态和独立随机序列。

## 3. 文献依据与模型定位

主要依据：

> Liu, Q., Lu, D., Jiang, R., Han, X., Liu, R., & Gao, Z. (2023). Departure time choice behavior in commute problem with stochastic bottleneck capacity: Experiments and modeling. *Transportmetrica A: Transport Science*, 19(2), 1978590. <https://doi.org/10.1080/23249935.2021.1978590>

本地全文：

`/Users/hybsmac/Desktop/相关文献/动态瓶颈与Human-Agent/03_实验设计与Agent/01_Liu等_2023_随机瓶颈容量实验与强化学习.pdf`

原文实验采用：

- 20 名参与者；
- 16 个离散出发时刻；
- 随机瓶颈容量；
- 150 轮重复决策；
- 根据历史成本形成各出发时刻的选择倾向；
- 对未选择时刻进行线性插值或外推；
- 使用 Softmax 将倾向转换为选择概率。

当前实现是对 Liu-REL 的事故信息扩展，论文中应称为：

> 基于 Liu et al.（2023）的事故信息条件化强化学习模型（incident-conditioned Liu-REL Agent）。

不得将其称为未经修改的原模型，也不得称为 Q-learning、DQN、PPO 或深度强化学习。

## 4. 当前代码的主要问题

当前独立 RL 入口位于：

- `dynamic_bottleneck_round/agents/independent_rl_agent.py`；
- `dynamic_bottleneck_round/agents/rl_fallback.py`；
- `dynamic_bottleneck_round/__init__.py` 中的 `prepare_independent_rl_decisions_for_group()` 和 `update_independent_rl_states()`。

现有算法包含 Q 值更新，但最终选择主要由人工成本预测、风险调整、Q 值修正和惯性奖励共同决定：

$$
\operatorname{Score}
=\operatorname{ExpectedCost}
+\operatorname{RiskCorrection}
+\operatorname{QCorrection}
-\operatorname{InertiaBonus}.
$$

存在以下问题：

1. Q 值只是人工评分中的修正项，模型不属于清晰的标准 Q-learning；
2. 当前策略确定性选择最低评分时刻，没有明确的随机探索机制；
3. 容量概率主要来自固定先验，没有形成清晰的事故信息条件化学习；
4. 独立 RL 与 DeepSeek 故障回退共用 `rl_fallback.py`，直接替换该文件会同时改变 LLM 故障行为；
5. 当前 Agent 人格参数影响学习率、风险和惯性，使算法参数与人格效应混合，不利于论文解释。

本次改造必须把独立 RL 与 LLM 故障回退解耦。

## 5. 原始 Liu-REL 核心

### 5.1 历史经验

对 RL Agent $i$、正式轮次 $r$ 和出发时刻 $t$，保存该 Agent 在此前选择 $t$ 时获得的实际总成本。记可用经验集合为：

$$
\mathcal{H}_{i,r}(t)
=\{C_{i,j}:j<r,\ a_{i,j}=t\}.
$$

主模型只用该 RL Agent 自己的实际成本更新经验。不得使用后台计算的未选择时刻反事实成本，也不得把本轮其他主体尚未公开的选择写入经验。

### 5.2 已有经验时刻的倾向

对具有历史经验的出发时刻，按照 Liu et al.（2023）原式计算成本倾向：

$$
q_{i,r}(t)
=\overline{C}_{i,r}(t)
-\lambda\sigma_{i,r}(t).
$$

其中，$\overline{C}_{i,r}(t)$ 为历史平均成本，$\sigma_{i,r}(t)$ 为历史成本标准差。实现时使用加权总体标准差，即 `ddof=0`；单条经验的标准差为 0。

必须保留原文中的减号。若未来要研究风险惩罚形式

$$
\overline C+\lambda\sigma,
$$

应作为单独模型和稳健性分析，不得在本次实现中静默替换。

### 5.3 未选择时刻的插值与外推

将已有有效倾向的时刻按行动编号排序为：

$$
t_1<t_2<\cdots<t_n.
$$

处理规则与原文保持一致：

1. 在两个已有时刻之间的未选择时刻，使用相邻倾向进行线性插值；
2. 对 $t_1-1$，使用最早两个已有时刻进行线性外推；
3. 对 $t_n+1$，使用最晚两个已有时刻进行线性外推；
4. 早于 $t_1-1$ 的时刻使用 $q(t_1-1)$；
5. 晚于 $t_n+1$ 的时刻使用 $q(t_n+1)$；
6. 如果当前可用经验不足两个不同出发时刻，不执行外推，转入稀疏经验回退规则。

### 5.4 Softmax 选择

定义 Agent 截至上一轮的平均实际成本为：

$$
\phi_{i,r}
=\frac{1}{r-1}\sum_{j<r}C_{i,j}.
$$

选择概率为：

$$
P_{i,r}(t)
=\frac{
\exp\left[-\eta q_{i,r}(t)/\phi_{i,r}\right]
}{
\sum_{k=1}^{16}
\exp\left[-\eta q_{i,r}(k)/\phi_{i,r}\right]
}.
$$

实现要求：

- 使用数值稳定 Softmax，先从所有 logit 中减去最大 logit；
- 使用一个正数下限保护 $\phi$，避免除以零；
- 概率必须全部有限、非负且总和在浮点误差范围内等于 1；
- 根据概率抽样，不得直接选择最大概率行动；
- 抽样使用可复现的独立随机数生成器。

## 6. 事故信息条件化扩展

原始 Liu-REL 没有 I0、I1、I2 当轮事故信息。当前模型在保留倾向更新、插值和 Softmax 的基础上，只改变“哪些历史经验用于计算当前倾向”。

### 6.1 I0：不知道当轮事故和服务率

决策前可使用：

- 正常服务率；
- 事故概率；
- 事故损失分布；
- 截至上一轮已经公开的历史结果。

不得使用当轮 `incident_occurred` 或 `actual_capacity`。

倾向计算使用该 Agent 的全部历史正式轮经验：

$$
w_j^{I0}=1.
$$

### 6.2 I1：只知道当轮是否发生事故

决策前可使用当前 `incident_occurred`，不得使用当前事故损失率或实际服务率。

优先选择与当轮事故状态相同的历史经验：

$$
w_j^{I1}
=\mathbf{1}
\{A_j=A_r\},
$$

其中 $A_r\in\{0,1\}$ 表示正常或事故。

如果同状态经验覆盖不足两个不同出发时刻，则整体回退到 I0 的全部历史经验。不得根据当前实际服务率挑选历史样本。

### 6.3 I2：知道当轮实际服务率

决策前可以使用当前实际服务率 $s_r$。使用历史已公开服务率与当前服务率之间的高斯核权重：

$$
w_j^{I2}
=\exp\left[
-\frac{(s_r-s_j)^2}{2h^2}
\right],
$$

其中 $h>0$ 为服务率相似度带宽。

对每个出发时刻，使用核权重计算加权平均成本和加权总体标准差。若有效经验不足两个不同出发时刻，则按以下顺序回退：

1. 使用与当前事故状态一致的 I1 历史经验；
2. 使用 I0 全部历史经验；
3. 若全部历史仍不足两个不同时刻，使用均匀随机选择。

I2 只能使用决策上下文中已经公开的 `actual_capacity`。不得从事故序列、Group 隐藏字段或未来记录中读取服务率。

### 6.4 稀疏经验规则

- 前两个正式轮次始终在 16 个时刻间均匀随机选择；
- 第三轮以后，如果按 I0/I1/I2 及其回退规则仍不足两个有经验的不同时刻，则继续均匀随机；
- 稀疏经验均匀选择属于正常策略，`decision_source` 应记录为 `liu_rel_uniform_sparse`，不能记为错误回退；
- 两个已有时刻足以启动插值和外推；
- 不允许用零填充未知时刻倾向，因为零可能被 Softmax 误解释为低成本优势。

## 7. 参数规范

### 7.1 必需参数

新增或明确以下 Session 配置：

```text
rel_policy_version = dynamic_liu_rel_incident_v1
rel_lambda
rel_eta
rel_capacity_bandwidth
rel_random_seed
rel_initial_uniform_rounds = 2
rel_parameters_frozen
```

参数约束：

- `rel_lambda >= 0`；
- `rel_eta > 0`；
- `rel_capacity_bandwidth > 0`；
- `rel_random_seed` 必须是明确整数；
- `rel_initial_uniform_rounds` 正式版本固定为 2，不允许从管理界面临时改变。
- 正式 Session 必须设置 `rel_parameters_frozen = 1`；演示 Session 可以为 0。

### 7.2 文献参数与演示参数

Liu et al.（2023）报告：

- 个性化反馈场景：$\lambda^*=0.2828$；
- 全时刻反馈场景：$\lambda^*=0.2036$；
- 强化敏感系数：$\eta=14.7445$。

这些参数由原文 150 轮实验数据校准，不能直接视为当前事故实验的正式参数。

演示或单元测试可以使用明确标记的参考值：

```text
rel_lambda = 0.25
rel_eta = 14.7445
rel_capacity_bandwidth = 0.560924
```

其中 0.25 是原文两个 $\lambda^*$ 的近似中间值；0.560924 是当前事故容量分布的理论标准差：

$$
4\sqrt{
\frac{\alpha\beta}
{(\alpha+\beta)^2(\alpha+\beta+1)}
},
\qquad
\alpha=6.83057,\ \beta=4.05907.
$$

上述值只能用于演示、测试和 Pilot 初始搜索中心，不能在未校准情况下标记为正式实验参数。

### 7.3 Pilot 校准规则

正式参数必须使用独立 Pilot 数据联合校准，并遵守：

1. 不使用正式主实验数据调参；
2. I0、I1、I2 使用同一组 $\lambda$ 和 $\eta$，避免参数变化与信息处理混淆；
3. $h$ 只控制 I2 的服务率经验相似度；
4. 校准目标至少同时包含出发时刻分布、跨轮切换率和平均成本趋势；
5. 记录候选参数范围、评价函数、最优结果和敏感性分析；
6. 正式实验前把参数写入固定配置并预注册。

若 Pilot 尚未完成，生产 Session 应拒绝使用“未冻结参数”标志启动；演示 Session 可以使用参考值。

## 8. RL 状态结构

建议新状态采用可序列化字典：

```json
{
  "policy_version": "dynamic_liu_rel_incident_v1",
  "rounds_observed": 0,
  "experiences": [],
  "last_departure_slot": null,
  "last_choice_probability": null,
  "last_context_level": null,
  "last_propensities": {},
  "last_choice_probabilities": {}
}
```

每条正式经验至少包含：

```json
{
  "formal_round_number": 1,
  "departure_slot": 8,
  "total_cost": 12.5,
  "incident_occurred": false,
  "actual_capacity": 4.0
}
```

要求：

- 每个 RL Agent 独立保存状态；
- 只在本轮完成结算并生成公开反馈后追加经验；
- 同一正式轮不得重复追加；
- 练习轮不得追加；
- 历史事故状态与实际服务率在结算后已对所有处理公开，因此可以进入下一轮的内部经验；
- 状态版本不匹配时只对新 Session 初始化，不迁移正在运行的旧 Session；
- 60 轮规模很小，不需要数据库模型或外部存储。

## 9. 代码结构与改动边界

### 9.1 新增模块

新增：

`dynamic_bottleneck_round/agents/liu_rel_agent.py`

建议包含以下纯函数：

```text
initial_liu_rel_state()
valid_or_initial_liu_rel_state()
append_liu_rel_experience()
select_information_conditioned_experiences()
weighted_cost_statistics_by_slot()
interpolate_propensities()
liu_rel_choice_probabilities()
choose_liu_rel_departure()
```

纯统计和概率函数不得依赖 oTree 对象，方便独立测试。

### 9.2 修改独立 RL 包装层

修改：

`dynamic_bottleneck_round/agents/independent_rl_agent.py`

保留现有对外函数名称，以减少集成改动：

```text
initial_independent_rl_state()
valid_or_initial_independent_rl_state()
choose_independent_rl_departure()
observe_independent_rl_outcome()
```

内部改为调用 `liu_rel_agent.py`，策略版本更新为：

```text
dynamic_liu_rel_incident_v1
```

### 9.3 修改 oTree 集成

修改 `dynamic_bottleneck_round/__init__.py`：

- `prepare_independent_rl_decisions_for_group()` 向 Liu-REL 传入正式轮号、信息条件、允许公开的事故状态或服务率、固定参数和稳定随机种子；
- `update_independent_rl_states()` 在正式轮结算后追加该 RL 自己的实际行动、成本、事故状态和服务率；
- 保持每个 Agent 的状态按 `agent_id` 独立；
- 保持现有同时决策顺序和虚拟主体结算逻辑；
- 练习轮继续生成 RL 选择以参与排队，使用独立的均匀随机策略，不读取或更新正式 REL 经验；
- 不改变 Human、LLM、成本、排队和事故序列代码。

### 9.4 不修改 LLM 故障回退

以下功能继续保留现状：

- `dynamic_bottleneck_round/agents/rl_fallback.py`；
- `rl_candidate_for_choice_set()`；
- `prepare_rl_candidates_for_agents()`；
- `apply_rl_fallback_to_choice()`；
- `update_rl_shadow_states()`。

它们属于 DeepSeek API 失败时的影子/回退机制，不是本次独立 RL 参与者的主模型。不得因为替换独立 RL 而改变 LLM 失败时的行为。

### 9.5 人格参数

为保持 Liu-REL 算法定义清晰，`dynamic_liu_rel_incident_v1` 不使用人格中的：

- `adaptation_speed`；
- `capacity_risk_aversion`；
- `choice_inertia`。

现有 RL persona ID 和标签可以为数据兼容而保留，但不得进入本版选择概率。两个 RL Agent 的异质性来自独立随机选择和独立经验路径。

## 10. 可复现随机机制

禁止使用 Python 内置 `hash()` 生成正式随机种子，因为不同进程可能得到不同结果。

建议以以下字段拼接后使用 SHA-256 生成局部种子：

```text
session_code
group_id
agent_id
formal_round_number
rel_policy_version
rel_random_seed
```

每次决策创建局部 `random.Random(seed)`，只用于该 Agent 该轮的 Softmax 抽样。这样能够保证：

- 同一 Session、Agent 和轮次可重复；
- 两个 RL Agent 的随机序列相互独立；
- 不污染全局随机状态；
- 服务器重启或重复预取不会改变已定义选择。

## 11. 决策来源和审计记录

建议使用以下 `decision_source`：

```text
liu_rel_uniform_initial
liu_rel_uniform_warmup
liu_rel_uniform_sparse
liu_rel_softmax_i0
liu_rel_softmax_i1
liu_rel_softmax_i1_backoff_i0
liu_rel_softmax_i2_kernel
liu_rel_softmax_i2_backoff_i1
liu_rel_softmax_i2_backoff_i0
rl_fallback_lowest_schedule_cost
```

现有导出已经包含：

- `agent_context_json`；
- `rl_policy_version`；
- `rl_rounds_observed`。

优先在 `agent_context_json` 中增加以下审计字段，避免不必要的数据库结构变化：

```json
{
  "policy_version": "dynamic_liu_rel_incident_v1",
  "information_condition": "I1",
  "context_level": "i1_incident",
  "propensities": {"1": 10.2},
  "choice_probabilities": {"1": 0.04},
  "selected_probability": 0.04,
  "distinct_experienced_slots": 4,
  "effective_observation_count": 7.0,
  "rel_lambda": 0.25,
  "rel_eta": 14.7445,
  "rel_capacity_bandwidth": 0.560924,
  "random_seed_fingerprint": "short-stable-id"
}
```

不得把未来事故序列、隐藏当轮容量、API 密钥或完整随机种子写入参与者可见内容。

## 12. 错误处理

- 合法但经验不足：均匀随机选择，不算失败；
- 练习轮：在 16 个时刻间均匀随机选择，记录 `liu_rel_uniform_warmup`，不增加 `rounds_observed`；
- Softmax 出现非有限值或概率和异常：抛出明确算法错误；
- 状态损坏或版本不匹配：新 Session 初始化干净状态；
- 正式参数缺失或越界：Session 创建阶段失败；
- 运行时算法异常：沿用最低计划延误成本回退，记录 `rl_fallback_lowest_schedule_cost`；
- 回退行动仍参与真实排队和成本结算；
- 回退轮的实际经验可以追加到状态，但必须保留回退标记，以便稳健性分析剔除；
- 重复调用决策准备函数必须返回已保存记录，不能重新抽样。

## 13. 测试要求

新增：

`dynamic_bottleneck_round/agents/test_liu_rel_agent.py`

至少覆盖以下单元测试：

1. 初始状态结构和策略版本正确；
2. 状态版本变化会重新初始化；
3. 前两个正式轮次概率为 $1/16$；
4. 相同稳定种子得到相同选择；
5. 不同 Agent ID 生成独立随机序列；
6. 单条经验的标准差为 0；
7. 多条经验的加权平均和总体标准差计算正确；
8. 已有时刻倾向严格使用 $\overline C-\lambda\sigma$；
9. 中间未选时刻线性插值正确；
10. 两端外推和边界常数延伸正确；
11. 不足两个不同时刻时进入均匀稀疏策略；
12. Softmax 概率全部有限、非负且和为 1；
13. 较低倾向在其他条件相同时获得较高选择概率；
14. I0 不读取当前事故状态和服务率；
15. I1 只按当前事故状态筛选，不读取当前实际服务率；
16. I1 经验不足时回退 I0；
17. I2 高斯核对接近当前服务率的历史经验赋予更高权重；
18. I2 按规定依次回退 I1、I0 和均匀策略；
19. 同一正式轮不能重复追加经验；
20. 练习轮使用 `liu_rel_uniform_warmup` 且不能更新 REL 状态。

更新现有集成测试，至少验证：

1. 独立 RL 使用 `dynamic_liu_rel_incident_v1`；
2. Human、LLM、RL 的 I0/I1/I2 外部信息白名单不变；
3. 两个 RL Agent 状态隔离；
4. 练习轮决策不污染第一轮正式状态；
5. 决策记录重复读取不会重新抽样；
6. Agent 选择继续进入同一排队和成本结算；
7. `agent_context_json` 保存倾向和概率审计信息；
8. DeepSeek 的现有 RL 影子回退测试继续通过；
9. `dynamic_bottleneck_round` 全部测试通过；
10. `single_bottleneck` 回归测试通过。

实施过程必须遵循测试驱动：先增加失败测试并确认失败原因，再实现最小修改使其通过。

## 14. 验收标准

只有同时满足以下条件才算完成：

- 独立 RL 不再从 `rl_fallback.py` 调用现有混合启发式策略；
- 独立 RL 使用 Liu 倾向更新、插值/外推和 Softmax 抽样；
- I0、I1、I2 严格遵守不同的当轮信息边界；
- I2 能利用精确服务率但不会泄漏隐藏或未来数据；
- 5 轮练习不进入正式经验；
- 两个 RL Agent 的状态和随机序列独立；
- 正式参数可配置、可校验、可导出并能在实验前冻结；
- 决策记录能够还原每轮概率和选择依据；
- LLM 主策略和 LLM 故障回退行为未被意外改变；
- 所有新增、现有和回归测试通过。

## 15. 明确不在本次范围

- 不实现 PPO、DQN、Actor-Critic 或神经网络策略；
- 不预训练 RL；
- 不用正式实验数据校准参数；
- 不修改事故概率或 Beta 参数；
- 不修改 I0/I1/I2 对 Human 和 LLM 的展示；
- 不改变 16 个出发时刻、5+60 轮结构和主体数量；
- 不把 RL 目标改为最小化系统总成本；本版仍学习降低自身实际成本；
- 不修改论文正文、前后问卷或数据分析平台；
- 不删除现有 LLM 故障回退算法。

## 16. 论文表述边界

可以表述：

> 本研究以 Liu et al.（2023）的出发时刻选择强化学习模型为基础，保留经验成本倾向更新、未选择时刻插值和概率选择机制，并针对事故状态及连续事故服务率构建信息条件化经验筛选，以适配 I0、I1 和 I2 出行前信息处理。

不能表述：

- “完全复现 Liu et al.（2023）”；
- “采用标准 Q-learning”；
- “采用 PPO 或深度强化学习”；
- “参数由 Liu et al.（2023）直接确定”；
- “RL 一定比 Human 或 LLM 更优”；
- “两个 RL 和两个 LLM 的差异具有因果效应”。

当前 16 Human + 2 LLM + 2 RL 设计只能严格识别混合 Agent 进入的总体效应；LLM 与 RL 的差异仍属于描述性或探索性结果。

## 17. 交给另一个聊天的执行提示词

请先完整阅读：

`my_platform/docs/superpowers/specs/2026-09-09-accident-bottleneck-liu-rel-agent-design.md`

然后按照该设计修改 `dynamic_bottleneck_round` 的独立 RL Agent。先检查现有代码和测试，再使用测试驱动方式实施。新增 `agents/liu_rel_agent.py`，让 `independent_rl_agent.py` 改为调用事故信息条件化 Liu-REL；保持 `rl_fallback.py` 及 DeepSeek 故障回退路径不变。严格实现 5 轮练习不学习、60 轮在线学习、I0/I1/I2 信息隔离、倾向插值/外推、数值稳定 Softmax、稳定随机种子、独立 Agent 状态和审计导出。不要实现 PPO，不要修改事故序列、排队成本、Human/LLM 页面或正式支付逻辑。完成后运行相关单元测试、整个 `dynamic_bottleneck_round` 测试和 `single_bottleneck` 回归测试，并报告改动文件、测试结果及仍需 Pilot 确定的参数。
