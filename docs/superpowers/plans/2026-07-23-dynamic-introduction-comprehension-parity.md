# Dynamic Introduction and Comprehension Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dynamic bottleneck introduction and comprehension pages match the established single-bottleneck participant experience while retaining the dynamic service-rate mechanism.

**Architecture:** Keep `dynamic_bottleneck_round` independent and copy only the verified presentation pattern. The dynamic app continues to calculate its own template context; templates render candidate service rates, reveal timing, and a fourth dynamic-capacity question without importing runtime state from `single_bottleneck`.

**Tech Stack:** oTree Python page classes, oTree templates, HTML/CSS/JavaScript, Python `unittest`, oTree bots.

---

### Task 1: Lock the participant-facing template contract

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing introduction assertions**

Extend `TemplateContractTests.test_introduction_explains_round_level_capacity_draw` to assert the single-bottleneck structure:

```python
for text in (
    '最后提醒',
    '当前流程',
    '单瓶颈示意',
    '居住地',
    '工作地',
    '候选瓶颈服务率',
    '规则测试',
):
    self.assertIn(text, html)
self.assertIn('{{ for item in capacity_states }}', html)
self.assertIn('{{ capacity_reveal_description }}', html)
```

- [ ] **Step 2: Write failing comprehension assertions**

Extend `test_comprehension_check_tests_group_capacity_equality`:

```python
self.assertIn('class="scenario-box"', html)
self.assertIn('class="answer-feedback"', html)
self.assertIn('同一小组、同一轮', html)
self.assertIn('{{ example_arrival_time }}', html)
self.assertEqual(html.count('class="question-card"'), 4)
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.tests.TemplateContractTests.test_introduction_explains_round_level_capacity_draw \
  dynamic_bottleneck_round.tests.TemplateContractTests.test_comprehension_check_tests_group_capacity_equality
```

Expected: both tests fail because the current dynamic templates use the compact layout.

### Task 2: Align dynamic template context with the shared presentation

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Add failing context tests**

Add tests that create `before_decision` and `after_decision` configs and verify:

```python
self.assertEqual(
    capacity_reveal_description(before_config),
    '每轮真实服务率会在选择出发时间前公布。',
)
self.assertEqual(
    capacity_reveal_description(after_config),
    '每轮真实服务率会在提交出发时间后公布。',
)
```

Also verify the queue example uses a configured capacity and integer display values.

- [ ] **Step 2: Run the context tests and verify RED**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.DynamicPresentationContextTests
```

Expected: failure because `capacity_reveal_description()` does not exist.

- [ ] **Step 3: Add the presentation helper and template variables**

Implement:

```python
def capacity_reveal_description(config):
    if config.reveal_timing == REVEAL_BEFORE_DECISION:
        return '每轮真实服务率会在选择出发时间前公布。'
    return '每轮真实服务率会在提交出发时间后公布。'
```

Update `Introduction.vars_for_template()` with `capacity_reveal_description`.

Update `ComprehensionCheck.vars_for_template()` to expose:

```python
example_same_departure_people
example_capacity_per_slot
example_capacity_window_minutes
example_departure_time
example_same_departure_wait_minutes
example_arrival_without_queue_time
example_arrival_with_short_wait_time
example_arrival_time
preferred_arrival_time
free_flow_travel_minutes
late_cost_per_minute
coarse_toll_description
```

Calculate the third-question result with `service_batch_wait_minutes()` and format integer-valued results without decimals.

- [ ] **Step 4: Run the context tests and verify GREEN**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.DynamicPresentationContextTests
```

Expected: all tests pass.

### Task 3: Replace the introduction with the single-bottleneck presentation

**Files:**
- Modify: `dynamic_bottleneck_round/Introduction.html`
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Reuse the established structure**

Copy the single-bottleneck page structure and styles for:

```text
intro-hero
process-card
route-demo-card
bottleneck-visual
intro-facts
confirm-card
```

- [ ] **Step 2: Replace fixed-capacity content**

Render candidate states without exposing the current round:

```html
<section class="capacity-state-panel">
  <p class="capacity-state-title">候选瓶颈服务率</p>
  <div class="capacity-state-grid">
    {{ for item in capacity_states }}
      <div class="capacity-state-item">
        <strong>{{ item.capacity }} 车 / {{ capacity_window_minutes }} 分钟</strong>
        <span>{{ capacity_frequency_label }}：{{ item.probability_label }}</span>
      </div>
    {{ endfor }}
  </div>
  <p>{{ capacity_reveal_description }}</p>
</section>
```

The route badge should say `每轮服务率可能变化`; do not render `player.dynamic_capacity`.

- [ ] **Step 3: Run the introduction contract test**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.TemplateContractTests.test_introduction_explains_round_level_capacity_draw
```

Expected: pass.

### Task 4: Replace the comprehension page with the single-bottleneck presentation

**Files:**
- Modify: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Reuse single-bottleneck test interactions**

Use the existing `check-shell`, `check-hero`, `check-context`, `question-card`, `scenario-box`, `answer-options`, `answer-feedback`, and `check-actions` structure. Preserve the behavior that “检查答案” reveals explanations and enables continuation regardless of score.

- [ ] **Step 2: Render four questions**

Use:

```text
Q1 排队成本计算
Q2 早到成本计算
Q3 给定服务率的共同等待和到达时间计算
Q4 同组同轮服务率一致性与粗收费计算
```

Q3 must use `example_arrival_without_queue_time`, `example_arrival_with_short_wait_time`, and `example_arrival_time` as three distinct choices.

- [ ] **Step 3: Run template contract tests**

Run:

```bash
python -m unittest dynamic_bottleneck_round.tests.TemplateContractTests
```

Expected: all template contract tests pass.

### Task 5: Full verification

**Files:**
- Verify: `dynamic_bottleneck_round/__init__.py`
- Verify: `dynamic_bottleneck_round/Introduction.html`
- Verify: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Verify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Run static checks**

```bash
python3 -m py_compile \
  dynamic_bottleneck_round/__init__.py \
  dynamic_bottleneck_round/tests.py \
  dynamic_bottleneck_round/toll_calibration.py \
  settings.py
git diff --check -- \
  dynamic_bottleneck_round/__init__.py \
  dynamic_bottleneck_round/Introduction.html \
  dynamic_bottleneck_round/ComprehensionCheck.html \
  dynamic_bottleneck_round/tests.py
```

Expected: no output from `git diff --check`; compilation succeeds.

- [ ] **Step 2: Run all dynamic unit tests**

```bash
python -m unittest dynamic_bottleneck_round.tests
```

Expected: all tests pass.

- [ ] **Step 3: Run isolated two-person dynamic bot**

Copy the project to a fresh `/tmp` directory without `db.sqlite3`, then run:

```bash
otree test dynamic_bottleneck_round_demo 2
```

Expected: staggered, same-time, and timeout-recovery cases complete all 10 rounds.

- [ ] **Step 4: Run isolated single-bottleneck regression**

From a fresh `/tmp` copy without `db.sqlite3`, run:

```bash
otree test single_bottleneck_demo 5
```

Expected: all existing single-bottleneck bot cases complete.
