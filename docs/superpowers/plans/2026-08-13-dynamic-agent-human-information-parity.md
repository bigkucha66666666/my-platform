# 动态瓶颈人类与 Agent 信息一致性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让人类结果页、DeepSeek Agent 和独立 RL Agent 使用同一份上一轮公开反馈，并阻止 Agent 读取完整历史或当前轮未公开信息。

**Architecture:** 每轮结算后生成幂等的组级公开反馈快照并存入参考参与者的 `participant.vars`。结果页从当前轮快照渲染，下一轮 AgentChoiceSet 只携带上一轮快照；RL 继续保留内部 Q 值和转移计数，但其直接环境观察只来自同一上一轮快照。

**Tech Stack:** Python 3、oTree、`participant.vars`、`unittest`、现有 DeepSeek Agent 与本地表格型 RL。

---

## 文件结构

- Modify: `dynamic_bottleneck_round/__init__.py`：生成、保存、读取公开反馈快照，并统一结果页和 Agent 数据源。
- Modify: `dynamic_bottleneck_round/agents/rl_fallback.py`：从统一上一轮反馈构建 RL 的即时观察，不接受完整原始历史。
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`：覆盖 LLM/RL 信息同源和防泄漏。
- Modify: `dynamic_bottleneck_round/agents/test_rl_fallback.py`：覆盖 RL 对统一快照的消费。
- Modify: `dynamic_bottleneck_round/tests.py`：覆盖结算快照、结果页一致性和完整流程。

### Task 1: 定义公开反馈快照契约

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: 写失败测试，规定快照字段和匿名性**

在 `dynamic_bottleneck_round/tests.py` 增加测试，构造含人类、DeepSeek 和 RL 记录的已结算 group，调用 `public_feedback_snapshot_for_group(group)`，断言返回：

```python
self.assertEqual(snapshot['round_number'], group.round_number)
self.assertEqual(snapshot['dynamic_capacity'], group.dynamic_capacity)
self.assertIn('departure_outcomes', snapshot)
self.assertIn('group_average_cost', snapshot)
self.assertEqual(sum(row['participant_count'] for row in snapshot['departure_outcomes']), 5)
self.assertNotIn('participant_code', json.dumps(snapshot))
self.assertNotIn('agent_id', json.dumps(snapshot))
self.assertNotIn('actor_type', json.dumps(snapshot))
```

- [ ] **Step 2: 运行测试并确认因 helper 不存在而失败**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.DynamicBottleneckTests.test_public_feedback_snapshot_is_complete_and_anonymous -v
```

Expected: `FAIL` 或 `ERROR`，明确指出 `public_feedback_snapshot_for_group` 尚不存在。

- [ ] **Step 3: 实现最小快照生成函数**

在 `dynamic_bottleneck_round/__init__.py` 增加：

```python
PUBLIC_FEEDBACK_PARTICIPANT_VAR = 'dynamic_bottleneck_round_public_feedback_v1'


def public_feedback_snapshot_for_group(group):
    players = group.get_players()
    schedule = departure_schedule_for_player(players[0]) if players else static_departure_schedule()
    costs_by_slot = {slot: [] for slot in departure_slots(schedule)}
    for player in players:
        slot = player_departure_slot(player)
        if slot in costs_by_slot and player_has_departure_choice(player):
            costs_by_slot[slot].append(float(player.total_cost))
    for record in active_virtual_decisions_for_group(group):
        slot = int(record.get('departure_slot', 0) or 0)
        if slot in costs_by_slot:
            costs_by_slot[slot].append(float(record.get('total_cost', 0)))
    all_costs = [cost for values in costs_by_slot.values() for cost in values]
    return {
        'round_number': int(group.round_number),
        'dynamic_capacity': int(group.dynamic_capacity),
        'departure_outcomes': [
            {
                'slot': slot,
                'departure_minute': departure_minute_for_slot(slot, schedule),
                'departure_time': minute_to_clock(departure_minute_for_slot(slot, schedule)),
                'participant_count': len(costs_by_slot[slot]),
                'average_cost': (
                    round(sum(costs_by_slot[slot]) / len(costs_by_slot[slot]), 4)
                    if costs_by_slot[slot] else None
                ),
            }
            for slot in departure_slots(schedule)
        ],
        'group_average_cost': round(sum(all_costs) / len(all_costs), 4) if all_costs else 0,
    }
```

- [ ] **Step 4: 运行定向测试并确认通过**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.DynamicBottleneckTests.test_public_feedback_snapshot_is_complete_and_anonymous -v
```

Expected: `OK`。

### Task 2: 结算后幂等保存快照

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: 写失败测试，规定重复结算不会累计数据**

增加测试，连续调用两次 `save_public_feedback_snapshot(group)`，断言对应轮次只有一个快照且两次内容相同：

```python
first = save_public_feedback_snapshot(group)
second = save_public_feedback_snapshot(group)
stored = group.get_players()[0].participant.vars[PUBLIC_FEEDBACK_PARTICIPANT_VAR]
self.assertEqual(first, second)
self.assertEqual(list(stored), [str(group.round_number)])
self.assertEqual(stored[str(group.round_number)], first)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.DynamicBottleneckTests.test_public_feedback_snapshot_save_is_idempotent -v
```

Expected: 因保存 helper 不存在而失败。

- [ ] **Step 3: 实现覆盖式保存并接入结算锁内**

实现：

```python
def save_public_feedback_snapshot(group):
    players = group.get_players()
    if not players:
        return {}
    snapshot = public_feedback_snapshot_for_group(group)
    stored = players[0].participant.vars.get(PUBLIC_FEEDBACK_PARTICIPANT_VAR, {})
    by_round = deepcopy(stored) if isinstance(stored, dict) else {}
    by_round[str(group.round_number)] = snapshot
    players[0].participant.vars[PUBLIC_FEEDBACK_PARTICIPANT_VAR] = by_round
    return deepcopy(snapshot)
```

在 `set_results()` 完成全部人类与虚拟参与者结果写入、RL 状态更新之前或之后的同一结算锁内调用一次，确保快照只基于完整结算结果。

- [ ] **Step 4: 运行两个快照测试**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.tests.DynamicBottleneckTests.test_public_feedback_snapshot_is_complete_and_anonymous \
  dynamic_bottleneck_round.tests.DynamicBottleneckTests.test_public_feedback_snapshot_save_is_idempotent -v
```

Expected: `OK`。

### Task 3: LLM 只读取上一轮统一快照

**Files:**
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: 写失败测试，禁止完整历史并要求上一轮反馈**

增加三轮历史测试。在第 3 轮构建 `AgentChoiceSet`，断言：

```python
history = choice_set.history
self.assertEqual(history['public_feedback']['round_number'], 2)
self.assertEqual(history['own_previous_result']['round_number'], 2)
self.assertNotIn('previous_rounds', history)
self.assertNotIn('round_number": 1', json.dumps(history))
```

并断言 LLM 与 RL 构建决策时引用的是同一个 `public_feedback` 值。

- [ ] **Step 2: 运行测试并确认旧的 `previous_rounds` 行为导致失败**

Run:

```bash
python -m unittest dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentIntegrationTests.test_choice_set_exposes_only_previous_public_feedback -v
```

Expected: `FAIL`，因为当前 `agent_history_for_group()` 返回完整历史。

- [ ] **Step 3: 将 Agent 历史改为上一轮公开反馈与自身结果**

将 `agent_history_for_group(group, agent_id)` 改成：

```python
def previous_public_feedback_for_group(group):
    if int(group.round_number) <= 1:
        return None
    players = group.get_players()
    if not players:
        return None
    stored = players[0].participant.vars.get(PUBLIC_FEEDBACK_PARTICIPANT_VAR, {})
    snapshot = stored.get(str(group.round_number - 1)) if isinstance(stored, dict) else None
    return deepcopy(snapshot) if isinstance(snapshot, dict) else None


def agent_history_for_group(group, agent_id):
    previous_round = int(group.round_number) - 1
    own_result = None
    if previous_round >= 1:
        for record in active_virtual_decisions_for_round(group, previous_round):
            if record.get('agent_id') == agent_id:
                own_result = public_personal_result_from_record(record)
                break
    return {
        'public_feedback': previous_public_feedback_for_group(group),
        'own_previous_result': own_result,
    }
```

`public_personal_result_from_record()` 只保留轮次、出发时间、排队、到达、早晚到、成本、payoff、收费和奖励，不保留模型响应、人格或后台标识。

- [ ] **Step 4: 运行 Agent 上下文测试**

Run:

```bash
python -m unittest dynamic_bottleneck_round.agents.test_dynamic_agent_integration -q
```

Expected: 全部通过，且原有 `after_decision` 不泄漏测试继续通过。

### Task 4: RL 使用相同上一轮反馈

**Files:**
- Modify: `dynamic_bottleneck_round/agents/test_rl_fallback.py`
- Modify: `dynamic_bottleneck_round/agents/rl_fallback.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: 写失败测试，规定 RL 观察适配器输入**

增加 `public_feedback_observation(snapshot)` 测试：

```python
observation = public_feedback_observation(snapshot)
self.assertEqual(observation['revealed_capacity'], 3)
self.assertEqual(observation['anonymous_slot_counts'], {'1': 2, '2': 1})
self.assertNotIn('agent_id', json.dumps(observation))
self.assertNotIn('actor_type', json.dumps(observation))
```

- [ ] **Step 2: 运行测试并确认 helper 缺失**

Run:

```bash
python -m unittest dynamic_bottleneck_round.agents.test_rl_fallback.RLFallbackTests.test_public_feedback_observation_uses_only_public_fields -v
```

Expected: `ERROR`，helper 尚不存在。

- [ ] **Step 3: 实现适配器并替换 RL 结算观察输入**

在 `rl_fallback.py` 增加：

```python
def public_feedback_observation(snapshot):
    feedback = dict(snapshot or {})
    return {
        'revealed_capacity': feedback.get('dynamic_capacity'),
        'anonymous_slot_counts': {
            str(int(row['slot'])): int(row.get('participant_count', 0))
            for row in feedback.get('departure_outcomes', [])
        },
    }
```

在 `update_rl_shadow_states()` 和 `update_independent_rl_states()` 中读取当前刚保存的公开快照，经适配器后传入 `observe_rl_outcome()`。不再分别调用 `group_slot_counts_for_rl()` 构造另一套观察数据。

- [ ] **Step 4: 运行 RL 与独立 RL 测试**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_rl_fallback \
  dynamic_bottleneck_round.agents.test_independent_rl_agent -q
```

Expected: 全部通过。

### Task 5: 结果页复用统一快照

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: 写失败测试，规定图表与公开快照一致**

保存快照后调用 `result_current_round_cost_snapshot(player)`，逐时点断言人数与平均成本一致：

```python
public_by_slot = {row['slot']: row for row in public_snapshot['departure_outcomes']}
chart_by_slot = {row['slot']: row for row in chart_snapshot['bars']}
for slot, public_row in public_by_slot.items():
    self.assertEqual(chart_by_slot[slot]['participant_count'], public_row['participant_count'])
self.assertEqual(chart_snapshot['average_cost_label'], number_display(public_snapshot['group_average_cost']))
```

- [ ] **Step 2: 运行测试并确认结果页仍独立重算而失败或未命中快照**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.DynamicBottleneckTests.test_result_chart_uses_public_feedback_snapshot -v
```

Expected: `FAIL`，证明结果页尚未以快照为数据源。

- [ ] **Step 3: 修改图表 helper 从已保存快照派生显示数据**

`result_current_round_cost_snapshot(player)` 先读取当前轮已保存快照；仅旧 Session 没有快照时调用 `public_feedback_snapshot_for_group()` 生成只读兼容值，不写回旧轮次。轴上限、高度百分比和当前选择高亮仍在该 helper 内计算，模板无需修改。

- [ ] **Step 4: 运行动态场景核心测试**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests -q
```

Expected: 全部通过。

### Task 6: 全量回归与流程验证

**Files:**
- Verify: `dynamic_bottleneck_round/__init__.py`
- Verify: `dynamic_bottleneck_round/agents/rl_fallback.py`
- Verify: `dynamic_bottleneck_round/tests.py`
- Verify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Verify: `dynamic_bottleneck_round/agents/test_rl_fallback.py`

- [ ] **Step 1: 静态编译**

Run:

```bash
python -m py_compile \
  settings.py \
  dynamic_bottleneck_round/__init__.py \
  dynamic_bottleneck_round/tests.py \
  dynamic_bottleneck_round/agents/deepseek_agent.py \
  dynamic_bottleneck_round/agents/rl_fallback.py \
  dynamic_bottleneck_round/agents/independent_rl_agent.py
```

Expected: 无输出，退出码 0。

- [ ] **Step 2: 运行 Agent 与模板单元测试**

Run:

```bash
python -m unittest \
  admin_template_compatibility_tests \
  dynamic_bottleneck_round.tests \
  dynamic_bottleneck_round.agents.test_rl_fallback \
  dynamic_bottleneck_round.agents.test_independent_rl_agent \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration -q
```

Expected: 全部通过。

- [ ] **Step 3: 运行动态场景组合 bot**

Run:

```bash
eval "$(conda shell.zsh hook)" && conda activate otree_env && \
OTREE_DATABASE_URL=sqlite:////tmp/dynamic_information_parity.sqlite3 \
OTREE_ADMIN_PASSWORD=devpass \
DYNAMIC_BOTTLENECK_API_AGENT_ENABLED=1 \
DYNAMIC_BOTTLENECK_API_AGENT_COUNT=1 \
DYNAMIC_BOTTLENECK_RL_AGENT_ENABLED=1 \
DYNAMIC_BOTTLENECK_RL_AGENT_COUNT=2 \
otree test dynamic_bottleneck_round_demo 2
```

Expected: 三个 bot case 全部完成，LLM失败回退和独立RL均不阻塞结算。

- [ ] **Step 4: 运行原单瓶颈回归**

Run:

```bash
eval "$(conda shell.zsh hook)" && conda activate otree_env && \
OTREE_DATABASE_URL=sqlite:////tmp/single_bottleneck_information_parity_regression.sqlite3 \
OTREE_ADMIN_PASSWORD=devpass \
otree test single_bottleneck_demo 5
```

Expected: 原 `single_bottleneck` 全流程通过。

- [ ] **Step 5: 检查格式与意外改动**

Run:

```bash
git diff --check -- \
  dynamic_bottleneck_round/__init__.py \
  dynamic_bottleneck_round/agents/rl_fallback.py \
  dynamic_bottleneck_round/tests.py \
  dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py \
  dynamic_bottleneck_round/agents/test_rl_fallback.py
```

Expected: 无输出。
