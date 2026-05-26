# Single Bottleneck Virtual Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `single_bottleneck` 单瓶颈出发时间实验中加入同组虚拟参与者，使 API 调用智能体和强化学习智能体像真人一样选择出发时间、参与排队、影响拥堵与收益结算。

**Architecture:** 将“真人 Player”和“虚拟智能体 AgentDecision”统一抽象为本轮参与排队的 actor。真人仍通过 oTree 页面提交选择；虚拟智能体不显示页面，由服务端在每轮结果结算前自动生成出发时间，然后复用同一套 FIFO 瓶颈、早到/晚到、收费、奖励与 payoff 计算逻辑。

**Tech Stack:** oTree / Python, existing `single_bottleneck` app, optional HTTP API client for API agent, offline RL training module for RL agent, existing custom export/admin report.

---

## 1. 实验定位

本方案中的智能体不是给真人提供建议的助手，而是同组虚拟参与者。它们和真人一样占用瓶颈容量，并进入同一轮排队计算。因此智能体的核心实验含义是：

- 观察虚拟参与者是否改变真人出发时间分布。
- 比较 API 智能体、RL 智能体和真人在收费/奖励机制下的响应差异。
- 检验不同虚拟参与者比例对拥堵、总延误、收益、均衡收敛的影响。
- 为后续“人类 + 智能体混合交通流”实验提供可重复的数据结构。

现有项目中，关键基础已经具备：

- `my_platform/single_bottleneck/__init__.py` 已实现出发时间选择、FIFO 排队、收益结算、奖励、粗收费、导出。
- `my_platform/settings.py` 已有 `single_bottleneck_prod` 与 `single_bottleneck_demo` session config。
- `my_platform/single_bottleneck/tests.py` 已有 oTree bot 测试，可扩展成虚拟智能体结算测试。

---

## 2. 设计边界

### 2.1 虚拟参与者如何进入小组

虚拟参与者不需要 oTree participant 页面，也不经过 `access_gate`。每个真实 oTree group 在 session 创建时生成一组虚拟 agent 配置，存入 session 或 participant/group 相关记录。每轮结算时，系统为这些 agent 生成本轮出发时间，并与真人选择一起进入排队。

建议默认配置：

```python
virtual_agents_enabled = 1
api_agent_count_per_group = 1
rl_agent_count_per_group = 1
virtual_agent_visible_to_humans = 0
```

第一版建议不向真人明确展示“本组有几个智能体”，除非实验设计需要信息公开处理。是否公开应作为 treatment 变量，而不是写死在机制里。

### 2.2 智能体是否获得收益

智能体需要计算 payoff，但不进入真实支付。智能体 payoff 只用于：

- 决策反馈。
- RL 状态与奖励。
- 实验分析。
- 与真人行为对比。

真人最终支付仍只读取现有 `single_bottleneck_total_payoff`。

### 2.3 智能体是否在线学习

正式实验建议冻结智能体策略：

- API agent 每轮可以根据上下文推理，但 prompt、模型、温度、可见信息固定。
- RL agent 使用预训练 policy，不在正式场次中更新参数。

在线学习可以用于 pilot，但正式实验不建议启用，否则同一个 treatment 内部策略持续变化，会降低可解释性。

---

## 3. 核心数据模型

### 3.1 新增 AgentDecision 表

在 `my_platform/single_bottleneck/__init__.py` 中新增一个 `ExtraModel`，用于保存虚拟智能体的每轮选择与结果。

建议字段：

```python
class AgentDecision(ExtraModel):
    session_code = models.StringField()
    group_id = models.IntegerField()
    round_number = models.IntegerField()
    agent_id = models.StringField()
    agent_type = models.StringField()
    policy_version = models.StringField(blank=True)
    departure_slot = models.IntegerField()
    departure_minute = models.FloatField()
    departure_time_label = models.StringField(blank=True)
    arrival_minute = models.FloatField(initial=0)
    arrival_time_label = models.StringField(blank=True)
    queue_delay_minutes = models.FloatField(initial=0)
    travel_time_minutes = models.FloatField(initial=0)
    schedule_early_minutes = models.FloatField(initial=0)
    schedule_late_minutes = models.FloatField(initial=0)
    slot_load = models.IntegerField(initial=0)
    reward_bonus = models.CurrencyField(initial=0)
    coarse_toll_charge = models.CurrencyField(initial=0)
    payoff = models.CurrencyField(initial=0)
    decision_source = models.StringField(blank=True)
    fallback_used = models.BooleanField(initial=False)
    latency_ms = models.IntegerField(initial=0)
    raw_response_json = models.LongStringField(blank=True)
    context_json = models.LongStringField(blank=True)
```

`agent_id` 建议格式：

```text
G01_API_01
G01_RL_01
```

其中 `G01` 对应当前 assigned group，后缀表示 agent 类型与序号。

### 3.2 统一 Actor 结构

不要让排队函数直接依赖 `Player`。新增一个轻量结构，把真人和智能体统一成 actor：

```python
@dataclass
class BottleneckActor:
    actor_id: str
    actor_type: str
    source_object: object
    departure_slot: int
    departure_minute: float
```

`actor_type` 取值：

- `human`
- `api_agent`
- `rl_agent`

结算函数只读取 actor 的出发时间，输出统一结果，再根据 `actor_type` 写回 `Player` 或 `AgentDecision`。

---

## 4. 每轮运行流程

### 4.1 当前流程

当前机制大致为：

1. 真人进入 `Decision` 页面。
2. 真人提交 `departure_minute`。
3. 超时或断线时，系统自动补填随机出发时间。
4. `maybe_prepare_results(group)` 检查是否所有真人都有选择。
5. `set_results(group)` 计算排队和收益。

### 4.2 加入虚拟智能体后的流程

目标流程：

1. 真人继续通过 `Decision` 页面提交选择。
2. 到达结算条件时，先补齐缺失真人选择。
3. 服务端读取本组虚拟智能体配置。
4. 为每个 API agent 构造 context，并调用 API 得到出发时间。
5. 为每个 RL agent 构造 state，并调用冻结 policy 得到出发时间。
6. 校验所有智能体出发时间是否合法。
7. API/RL 失败时使用 fallback 策略生成合法出发时间。
8. 将真人与智能体合并为 actors。
9. 调用统一排队结算函数。
10. 真人结果写回 `Player`。
11. 智能体结果写回 `AgentDecision`。
12. `group.results_ready = True`。

核心原则：智能体必须在 `set_results(group)` 内部或其前置函数中完成决策，不能依赖页面交互。

---

## 5. API 智能体设计

### 5.1 决策上下文

API agent 每轮接收结构化 context。建议包括：

```json
{
  "experiment": "single_bottleneck",
  "round_number": 4,
  "total_rounds": 10,
  "preferred_arrival_time": "08:00",
  "free_flow_travel_minutes": 6,
  "bottleneck_capacity_per_slot": 1,
  "choice_set": [
    {"slot": 1, "departure_minute": 464, "departure_time": "07:44"},
    {"slot": 2, "departure_minute": 465, "departure_time": "07:45"}
  ],
  "cost_parameters": {
    "queue_cost_per_minute": 2,
    "early_cost_per_minute": 1,
    "late_cost_per_minute": 3
  },
  "tolls": [
    {"slot": 4, "charge": 8}
  ],
  "rewards": [
    {"slot": 2, "bonus": 8}
  ],
  "history": {
    "own_choices": [
      {"round": 1, "slot": 5, "payoff": 132}
    ],
    "group_distribution": [
      {"round": 1, "slot_counts": {"5": 3, "6": 2}}
    ]
  }
}
```

第一版建议只给 API agent 看历史轮次的聚合分布，不给它看当轮真人实时选择。原因是当轮真人选择只有结算时才完整可得，若让 API agent 看到当轮选择，它会成为“后行动者”，实验含义会偏离同步选择。

### 5.2 Prompt 约束

API agent 的返回必须是 JSON：

```json
{
  "departure_minute": 474,
  "reason": "The selected time balances schedule delay and expected queueing cost."
}
```

服务端只信任 `departure_minute`。`reason` 只用于记录与分析。

### 5.3 失败处理

API 调用必须有硬超时，例如 5 秒。任何失败都不能阻塞实验：

- 网络错误：fallback。
- API 超时：fallback。
- 非 JSON：fallback。
- 出发时间不在选择集合内：fallback。
- 返回小数但不在 1 分钟网格：fallback。

第一版 fallback 推荐“最低预测成本”启发式，而不是随机。这样 agent 行为更稳定，也便于解释。

---

## 6. RL 智能体设计

### 6.1 环境抽象

将当前单瓶颈机制抽成一个仿真环境：

- `reset()` 初始化 group 分布、历史状态和智能体状态。
- `step(actions)` 输入所有 actor 的出发时间，输出收益、排队、到达时间和下一状态。
- `action_space` 是所有合法 `departure_slot`。
- `observation_space` 是历史分布、收费/奖励向量、上一轮自身选择和收益。

第一版不需要完全符合 Gymnasium 接口，但建议函数签名接近：

```python
observation = env.reset()
observation, reward, terminated, info = env.step(action)
```

### 6.2 状态设计

建议第一版 RL state 使用低维离散/连续混合特征：

- 当前轮次比例：`round_number / total_rounds`
- 上一轮自己的 slot
- 上一轮自己的 payoff
- 上一轮各 slot 人数分布
- 当前 toll 向量
- 当前 reward 向量
- cost 参数：排队、早到、晚到成本

不要在第一版加入自然语言信息，也不要让 RL agent 读取 API agent 的理由文本。

### 6.3 训练路线

推荐路线：

1. 第一阶段：tabular Q-learning 或 epsilon-greedy bandit，快速得到可解释 baseline。
2. 第二阶段：DQN，适合加入更多历史特征。
3. 第三阶段：PPO 或多智能体 RL，仅在需要模拟复杂混合交通流时引入。

正式实验中加载冻结 policy：

```python
rl_policy_path = "my_platform/single_bottleneck/agents/policies/q_policy_v1.json"
```

如果 policy 文件不存在或读取失败，使用同一个 fallback 策略，不影响实验运行。

---

## 7. Treatment 设计

建议新增四类 session config：

| config | 真人 | API agent | RL agent | 用途 |
|---|---:|---:|---:|---|
| `single_bottleneck_human_only` | 有 | 0 | 0 | 基准组 |
| `single_bottleneck_with_api_agent` | 有 | 1 | 0 | API 智能体影响 |
| `single_bottleneck_with_rl_agent` | 有 | 0 | 1 | RL 智能体影响 |
| `single_bottleneck_with_api_and_rl_agents` | 有 | 1 | 1 | 两类智能体共同影响 |

如果样本量有限，可以先保留现有 `single_bottleneck_prod`，只通过 session config 参数开关智能体：

```python
virtual_agents_enabled=1
api_agent_count_per_group=1
rl_agent_count_per_group=1
virtual_agent_visible_to_humans=0
```

正式论文分析更推荐独立 config，因为 admin report 和导出时 treatment 分组更清晰。

---

## 8. 导出与分析指标

### 8.1 扩展导出字段

现有 `EXPORT_HEADERS` 以真人 Player 为单位。加入智能体后，建议导出两张逻辑表：

1. `human_rows`：保持现有真人记录，新增本组智能体数量字段。
2. `agent_rows`：每个智能体每轮一行。

如果技术上先做单表，也要加入：

- `actor_type`
- `actor_id`
- `agent_type`
- `policy_version`
- `fallback_used`
- `latency_ms`
- `raw_response_json`

### 8.2 核心分析指标

建议至少计算：

- 真人平均 payoff。
- 智能体平均 payoff。
- 总排队延误。
- 真人排队延误。
- 智能体排队延误。
- 各 slot 选择分布。
- 与无智能体基准相比的分布偏移。
- 收费/奖励时点选择比例。
- API fallback 率。
- RL policy 版本表现差异。

---

## 9. File Structure

建议最终文件结构：

```text
my_platform/single_bottleneck/
  __init__.py
  agents/
    __init__.py
    base.py
    context.py
    api_agent.py
    rl_agent.py
    fallback.py
    simulation.py
    policies/
      q_policy_v1.json
  tests.py
```

文件职责：

- `__init__.py`：保留 oTree 页面、模型、session config glue、结算入口。尽量只接入 agent orchestration，不堆大量策略代码。
- `agents/base.py`：定义 `AgentChoice`、`AgentContext`、`VirtualAgentSpec` 等基础结构。
- `agents/context.py`：从 group/player/session 构造智能体可见上下文。
- `agents/api_agent.py`：封装 API 调用、JSON 校验、超时与元数据记录。
- `agents/rl_agent.py`：加载冻结 policy 并输出动作。
- `agents/fallback.py`：最低预测成本、随机合法选择等 fallback 策略。
- `agents/simulation.py`：纯函数排队和收益计算，可被正式结算、fallback 预测和 RL 训练共用。
- `tests.py`：扩展 oTree bot 与纯函数测试。

---

## 10. Implementation Tasks

### Task 1: 抽离纯函数排队结算

**Files:**

- Modify: `my_platform/single_bottleneck/__init__.py`
- Create: `my_platform/single_bottleneck/agents/__init__.py`
- Create: `my_platform/single_bottleneck/agents/simulation.py`
- Test: `my_platform/single_bottleneck/tests.py`

- [ ] **Step 1: 写排队结算测试**

在 `tests.py` 中新增测试，覆盖同一出发时间下所有 actor 使用期望排队位置，结果与当前实现一致。

- [ ] **Step 2: 创建 `simulation.py`**

实现纯函数：

```python
def simulate_bottleneck_round(
    *,
    actors,
    first_departure_minute,
    service_interval_minutes,
    cost_params,
    toll_by_slot,
    reward_by_slot,
):
    """Return per-actor bottleneck outcomes without mutating oTree models."""
```

输入 actor 列表，输出每个 actor 的 arrival、queue delay、cost、payoff。

- [ ] **Step 3: 修改 `set_results(group)`**

让 `set_results(group)` 先构造 human actors，再调用 `simulate_bottleneck_round()`，最后把结果写回 `Player`。

- [ ] **Step 4: 运行测试**

运行：

```bash
cd my_platform
otree test single_bottleneck
```

期望：原有 `staggered` 和 `same_time` bot case 通过。

- [ ] **Step 5: Commit**

```bash
git add my_platform/single_bottleneck/__init__.py my_platform/single_bottleneck/agents my_platform/single_bottleneck/tests.py
git commit -m "refactor: extract single bottleneck simulation"
```

### Task 2: 新增虚拟智能体数据模型与配置

**Files:**

- Modify: `my_platform/single_bottleneck/__init__.py`
- Modify: `my_platform/settings.py`
- Test: `my_platform/single_bottleneck/tests.py`

- [ ] **Step 1: 新增 `AgentDecision`**

在 `single_bottleneck/__init__.py` 中新增 `ExtraModel`，字段按第 3.1 节定义。

- [ ] **Step 2: 新增 session config 参数读取函数**

实现：

```python
def virtual_agents_enabled(session) -> bool:
    return config_flag(session.config.get('virtual_agents_enabled', 0))

def api_agent_count_per_group(session) -> int:
    return max(0, parse_int(session.config.get('api_agent_count_per_group', 0), 0))

def rl_agent_count_per_group(session) -> int:
    return max(0, parse_int(session.config.get('rl_agent_count_per_group', 0), 0))
```

- [ ] **Step 3: 在 `settings.py` 加 demo 配置**

先只在 `single_bottleneck_demo` 添加：

```python
virtual_agents_enabled=0,
api_agent_count_per_group=0,
rl_agent_count_per_group=0,
virtual_agent_visible_to_humans=0,
```

- [ ] **Step 4: 写配置测试**

测试关闭智能体时，结算结果与现有逻辑一致。

- [ ] **Step 5: Commit**

```bash
git add my_platform/settings.py my_platform/single_bottleneck/__init__.py my_platform/single_bottleneck/tests.py
git commit -m "feat: add virtual agent configuration"
```

### Task 3: 实现 fallback 智能体

**Files:**

- Create: `my_platform/single_bottleneck/agents/base.py`
- Create: `my_platform/single_bottleneck/agents/context.py`
- Create: `my_platform/single_bottleneck/agents/fallback.py`
- Modify: `my_platform/single_bottleneck/__init__.py`
- Test: `my_platform/single_bottleneck/tests.py`

- [ ] **Step 1: 定义基础结构**

在 `base.py` 中定义：

```python
@dataclass
class AgentChoice:
    departure_minute: float
    decision_source: str
    fallback_used: bool
    latency_ms: int = 0
    raw_response_json: str = ""
```

- [ ] **Step 2: 构造 context**

在 `context.py` 中实现从 group 构造 agent 可见上下文，包括 choice set、成本参数、收费/奖励向量和历史分布。

- [ ] **Step 3: 实现最低预测成本 fallback**

在 `fallback.py` 中遍历所有合法 slot，估计该 slot 的个人成本，选择成本最低的出发时间。若并列，选择更接近自由流准时出发时间的 slot。

- [ ] **Step 4: 接入 set_results**

当 `virtual_agents_enabled=1` 且 API/RL count 大于 0 时，即使 API/RL 还没实现，也先用 fallback 为虚拟智能体生成 `AgentDecision` 并加入 actors。

- [ ] **Step 5: 测试智能体影响排队**

测试一个 group 中真人都选自由流时刻，加入 1 个 fallback agent 后，slot load 和 queue delay 与“多一个参与者”一致。

- [ ] **Step 6: Commit**

```bash
git add my_platform/single_bottleneck/__init__.py my_platform/single_bottleneck/agents my_platform/single_bottleneck/tests.py
git commit -m "feat: add fallback virtual agents"
```

### Task 4: 实现 API 智能体

**Files:**

- Create: `my_platform/single_bottleneck/agents/api_agent.py`
- Modify: `my_platform/settings.py`
- Modify: `my_platform/single_bottleneck/__init__.py`
- Test: `my_platform/single_bottleneck/tests.py`

- [ ] **Step 1: 新增 API 配置**

建议配置项：

```python
api_agent_model='',
api_agent_timeout_seconds=5,
api_agent_temperature=0,
api_agent_policy_version='api_v1',
```

API key 从环境变量读取，不写入 `settings.py`。

- [ ] **Step 2: 实现 API 调用封装**

`api_agent.py` 负责：

- 构造 prompt。
- 调用 API。
- 解析 JSON。
- 校验 `departure_minute`。
- 返回 `AgentChoice`。
- 失败时返回 fallback choice，并记录 `fallback_used=True`。

- [ ] **Step 3: 写 mock 测试**

不在测试中真实访问网络。用 mock client 返回合法 JSON、非法 JSON、超时三种结果，验证 fallback 行为。

- [ ] **Step 4: 接入 orchestration**

当 `api_agent_count_per_group > 0` 时，优先调用 API agent。失败时 fallback。

- [ ] **Step 5: Commit**

```bash
git add my_platform/settings.py my_platform/single_bottleneck/__init__.py my_platform/single_bottleneck/agents my_platform/single_bottleneck/tests.py
git commit -m "feat: add api virtual agent"
```

### Task 5: 实现 RL 智能体冻结策略

**Files:**

- Create: `my_platform/single_bottleneck/agents/rl_agent.py`
- Create: `my_platform/single_bottleneck/agents/policies/q_policy_v1.json`
- Modify: `my_platform/settings.py`
- Modify: `my_platform/single_bottleneck/__init__.py`
- Test: `my_platform/single_bottleneck/tests.py`

- [ ] **Step 1: 新增 RL 配置**

```python
rl_agent_policy_path='single_bottleneck/agents/policies/q_policy_v1.json',
rl_agent_policy_version='q_policy_v1',
```

- [ ] **Step 2: 定义 policy 文件格式**

第一版可用 JSON：

```json
{
  "policy_version": "q_policy_v1",
  "default_slot_rule": "min_predicted_cost",
  "q_table": {}
}
```

当 `q_table` 没有匹配状态时，用 `default_slot_rule`。

- [ ] **Step 3: 实现 `rl_agent.py`**

读取 policy，构造状态 key，查表得到 slot。若 policy 缺失、状态未命中或 slot 非法，使用 fallback。

- [ ] **Step 4: 写测试**

覆盖：

- policy 命中合法 slot。
- policy 返回非法 slot。
- policy 文件不存在。
- 状态未命中。

- [ ] **Step 5: Commit**

```bash
git add my_platform/settings.py my_platform/single_bottleneck/__init__.py my_platform/single_bottleneck/agents my_platform/single_bottleneck/tests.py
git commit -m "feat: add frozen rl virtual agent"
```

### Task 6: 扩展导出与 admin report

**Files:**

- Modify: `my_platform/single_bottleneck/__init__.py`
- Modify: `my_platform/single_bottleneck/admin_report.html`
- Test: `my_platform/single_bottleneck/tests.py`

- [ ] **Step 1: 扩展导出**

在 export 中加入 agent rows，至少包含 actor type、agent id、agent type、slot、payoff、fallback、latency、policy version。

- [ ] **Step 2: 扩展 admin report**

在 session summary 中加入：

- API agent 数量。
- RL agent 数量。
- agent 总记录数。
- API fallback 率。

- [ ] **Step 3: 测试导出字段**

创建启用 agent 的 demo session，验证导出中包含 human rows 和 agent rows。

- [ ] **Step 4: Commit**

```bash
git add my_platform/single_bottleneck/__init__.py my_platform/single_bottleneck/admin_report.html my_platform/single_bottleneck/tests.py
git commit -m "feat: export virtual agent decisions"
```

### Task 7: 新增 treatment session configs

**Files:**

- Modify: `my_platform/settings.py`
- Test: `my_platform/single_bottleneck/tests.py`

- [ ] **Step 1: 添加四个 config**

新增：

- `single_bottleneck_human_only`
- `single_bottleneck_with_api_agent`
- `single_bottleneck_with_rl_agent`
- `single_bottleneck_with_api_and_rl_agents`

- [ ] **Step 2: 保持现有 config 兼容**

现有 `single_bottleneck_prod` 和 `single_bottleneck_demo` 默认关闭智能体，避免影响当前实验。

- [ ] **Step 3: 测试 config 创建**

分别创建四类 demo session，验证不会在 session 创建阶段报错。

- [ ] **Step 4: Commit**

```bash
git add my_platform/settings.py my_platform/single_bottleneck/tests.py
git commit -m "feat: add virtual agent treatment configs"
```

---

## 11. Testing Strategy

必须覆盖以下行为：

- 关闭智能体时，现有实验结果完全不变。
- 启用 1 个 fallback agent 时，总排队 actor 数增加 1。
- API agent 返回非法值时不阻塞实验，fallback 生效。
- RL policy 缺失时不阻塞实验，fallback 生效。
- 智能体 payoff 不进入真人最终支付。
- 导出中能区分 `human`、`api_agent`、`rl_agent`。
- 同一 session config 下，冻结策略的 RL agent 行为可复现。

建议命令：

```bash
cd my_platform
otree test single_bottleneck
```

如果本地没有安装 oTree，至少运行 Python 静态编译：

```bash
python -m compileall my_platform/single_bottleneck
```

---

## 12. Main Risks

### 12.1 API 延迟影响实验

风险：API 调用太慢，真人等待结果页面。

控制方式：

- 硬超时。
- fallback。
- 记录 latency。
- 正式场次前用 demo 压测。

### 12.2 API 行为不可复现

风险：同一个 prompt 在不同日期或模型版本下结果不同。

控制方式：

- 固定模型名。
- 固定 temperature。
- 记录 prompt/context/raw response。
- 保存 policy version。

### 12.3 RL 在线学习破坏 treatment 稳定性

风险：同一 treatment 内策略不断变化，结果难解释。

控制方式：

- 正式实验只加载冻结 policy。
- 在线学习仅用于 pilot 或离线训练。

### 12.4 结算逻辑重复导致真人和智能体不一致

风险：为智能体另写一套收益逻辑后，与真人结果不一致。

控制方式：

- 抽离纯函数 `simulate_bottleneck_round()`。
- 真人和智能体都调用同一函数。
- 用测试锁定关闭智能体时的旧行为。

---

## 13. Recommended First Milestone

第一阶段只做“fallback 虚拟智能体”，不接 API，不接 RL。这个阶段完成后，实验已经能验证“虚拟参与者作为同组 actor 参与排队”的核心机制。

第一阶段交付物：

- `AgentDecision` 数据模型。
- 统一 actor 结算。
- fallback agent。
- session config 开关。
- 测试证明智能体会影响真人排队结果。
- 导出中能看到智能体记录。

完成第一阶段后，再接 API agent 和 RL agent，风险更低，因为后两者都只是替换“如何选择出发时间”，不会再改变排队结算主结构。
