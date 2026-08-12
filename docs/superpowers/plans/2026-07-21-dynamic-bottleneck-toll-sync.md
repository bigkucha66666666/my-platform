# Dynamic Bottleneck Toll and Participant Information Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `dynamic_bottleneck_round` 增加按组人数和候选服务率预校准的自动粗收费，并使参与者掌握的成本、排队、收费和结果信息与 `single_bottleneck` 一致。

**Architecture:** 动态 app 独立保存组级固定出发时间范围和 `{capacity: toll_result}` 校准映射，每轮把真实服务率对应的收费配置复制到 Player。校准核心放入动态 app 自有模块；页面通过动态 app helper 获取实际生效收费，不依赖 `single_bottleneck` 模型或 participant vars。

**Tech Stack:** Python 3.10、oTree、原生 HTML/CSS/JavaScript、`unittest`、oTree browser bots。

---

## File Structure

- Create `dynamic_bottleneck_round/toll_calibration.py`: 独立点模型粗收费搜索、精确/大组模式和结果类型。
- Create `dynamic_bottleneck_round/toll_calibration_cache.json`: 动态场景缓存文件，初始允许为空记录。
- Modify `dynamic_bottleneck_round/__init__.py`: 动态时间范围、校准编排、每轮收费字段、计算、导出和页面变量。
- Modify `dynamic_bottleneck_round/tests.py`: 纯函数、配置、页面、导出和 bot 回归。
- Modify `dynamic_bottleneck_round/Introduction.html`: 同步成本/排队/收费认知并展示动态服务率。
- Modify `dynamic_bottleneck_round/ComprehensionCheck.html`: 三道成本计算题和一道动态服务率/收费题。
- Modify `dynamic_bottleneck_round/Decision.html`: 同步时间滚轮并逐时点显示收费。
- Modify `dynamic_bottleneck_round/ResultsSync.html`: 同步等待说明与视觉层级。
- Modify `dynamic_bottleneck_round/Results.html`: 成本中心结果与当前轮群体成本/人数图。
- Modify `dynamic_bottleneck_round/admin_report.html`: 增加实际收费和平均收费。
- Modify `settings.py`: 开启动态 app 自动收费和动态时间范围。

### Task 1: Dynamic Schedule and Reveal Validation

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: Write failing schedule and validation tests**

```python
class DynamicScheduleTests(unittest.TestCase):
    def test_schedule_uses_lowest_capacity_and_group_size(self):
        schedule = build_dynamic_departure_schedule(
            players_count=60,
            capacity_values=(1, 2, 3),
            min_slots_each_side=10,
        )
        self.assertEqual(schedule['capacity_basis'], 1)
        self.assertEqual(schedule['slots_each_side'], 30)
        self.assertEqual(schedule['num_slots'], 61)

    def test_auto_toll_rejects_after_decision(self):
        with self.assertRaisesRegex(ValueError, 'after_decision.*自动粗收费'):
            validate_toll_reveal_compatibility({
                'capacity_reveal_timing': 'after_decision',
                'coarse_toll_auto_enabled': 1,
            })
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicScheduleTests`

Expected: FAIL because the schedule and compatibility helpers do not exist.

- [ ] **Step 3: Implement group-level schedule helpers**

Add constants and helpers that mirror the single-bottleneck schedule record but use `min(capacity_values)`:

```python
DEPARTURE_SCHEDULE_VAR = 'dynamic_bottleneck_round_departure_schedule'

def build_dynamic_departure_schedule(*, players_count, capacity_values, min_slots_each_side):
    capacity_basis = min(capacity_values)
    required_occupied_slots = ceil(players_count / capacity_basis)
    slots_each_side = max(min_slots_each_side, ceil(required_occupied_slots / 2))
    center = C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES
    first = center - slots_each_side * C.DEPARTURE_CHOICE_STEP_MINUTES
    last = center + slots_each_side * C.DEPARTURE_CHOICE_STEP_MINUTES
    return {
        'enabled': True,
        'source': 'auto',
        'players_count': players_count,
        'capacity_basis': capacity_basis,
        'slots_each_side': slots_each_side,
        'num_slots': slots_each_side * 2 + 1,
        'slot_size_minutes': C.DEPARTURE_CHOICE_STEP_MINUTES,
        'first_departure_minute': first,
        'last_departure_minute': last,
    }

def validate_toll_reveal_compatibility(session_config):
    if config_flag(session_config.get('coarse_toll_auto_enabled', 0)) and str(
        session_config.get('capacity_reveal_timing', REVEAL_BEFORE_DECISION)
    ).strip().lower() == REVEAL_AFTER_DECISION:
        raise ValueError('capacity_reveal_timing=after_decision 时不能启用自动粗收费。')
```

Update all departure slot/minute helpers to accept the current player's schedule. Store one schedule per group in every group member's `participant.vars` before capacity/toll calibration.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicScheduleTests`

Expected: PASS.

### Task 2: Independent Toll Calibration Core

**Files:**
- Create: `dynamic_bottleneck_round/toll_calibration.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing calibration contract tests**

```python
class DynamicTollCalibrationTests(unittest.TestCase):
    def test_candidate_uses_batch_max_wait_and_inherited_queue(self):
        candidate = calibrate_best_candidate(
            players=5,
            capacity=2,
            valid_slots=tuple(range(1, 22)),
            first_departure_minute=464,
            slot_size_minutes=1,
            min_toll=0,
            max_toll=20,
            toll_step=1,
        )
        self.assertGreaterEqual(candidate.window_start, 1)
        self.assertLessEqual(candidate.window_end, 21)
        self.assertGreaterEqual(candidate.toll, 0)

    def test_large_group_mode_returns_candidate(self):
        candidate = calibrate_best_candidate(
            players=60,
            capacity=1,
            valid_slots=tuple(range(1, 62)),
            first_departure_minute=444,
            slot_size_minutes=1,
            calibration_mode='large-group',
        )
        self.assertEqual(candidate.calibration_mode, 'large-group')
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicTollCalibrationTests`

Expected: FAIL because the dynamic calibration module does not exist.

- [ ] **Step 3: Implement independent calibration module**

Implement these public types/functions without importing `single_bottleneck`:

```python
@dataclass(frozen=True)
class EquilibriumCandidate:
    window_start: int
    window_end: int
    toll: float
    cost_gap: float
    nash_count: int
    distribution: tuple[int, ...]
    selected_costs: tuple[tuple[int, float], ...]
    calibration_mode: str = 'exact'
    deviation_gap: float = 0

    @property
    def window_spec(self):
        return str(self.window_start) if self.window_start == self.window_end else f'{self.window_start}-{self.window_end}'

def calibrate_best_candidate(...):
    candidates = calibrate_candidates_by_mode(..., top_k=1)
    if not candidates:
        raise CalibrationError(...)
    return candidates[0]
```

Port only the pure point-model search needed by the dynamic app: service batches, inherited queue, symmetric windows, exact feasibility, large-group approximation and deterministic candidate sorting. Constants are passed as function parameters or declared locally to match `C` values.

- [ ] **Step 4: Run calibration tests and verify GREEN**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicTollCalibrationTests`

Expected: PASS, including the 60-person large-group case within a practical runtime.

### Task 3: Precalibrate by Capacity and Apply Per Round

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Create: `dynamic_bottleneck_round/toll_calibration_cache.json`

- [ ] **Step 1: Write failing group/capacity toll tests**

```python
class DynamicTollApplicationTests(unittest.TestCase):
    def test_builds_one_result_per_capacity(self):
        results = calibrate_tolls_for_group(
            players_count=5,
            capacities=(1, 2, 3),
            schedule=self.schedule,
            settings=self.settings,
        )
        self.assertEqual(set(results), {'1', '2', '3'})
        self.assertEqual(results['2']['capacity'], 2)

    def test_round_selects_toll_matching_actual_capacity(self):
        apply_round_toll(self.group, self.tolls_by_capacity)
        for player in self.group.get_players():
            self.assertEqual(player.coarse_toll_calibration_capacity, self.group.dynamic_capacity)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicTollApplicationTests`

Expected: FAIL because capacity-indexed calibration and Player toll fields are absent.

- [ ] **Step 3: Add config helpers, cache matching and model fields**

Add dynamic namespaced vars/constants and Player fields for source, mode, calibration metadata, effective window, points and charge. Implement cache key validation against:

```python
{
    'players': players_count,
    'capacity': capacity,
    'choice_slots': schedule['num_slots'],
    'choice_step_minutes': schedule['slot_size_minutes'],
    'first_departure_time': minute_to_clock(schedule['first_departure_minute']),
    'last_departure_time': minute_to_clock(schedule['last_departure_minute']),
    'same_time_queue_rule': 'batch_max_wait',
    'toll_window_rule': 'symmetric_continuous',
}
```

Use an initial cache document:

```json
{
  "version": 1,
  "same_time_queue_rule": "batch_max_wait",
  "toll_window_rule": "symmetric_continuous",
  "records": []
}
```

- [ ] **Step 4: Implement Session precalibration and round application**

During round 1 Session creation:

```python
apply_dynamic_departure_schedules(session, matrix, config)
validate_toll_reveal_compatibility(session.config)
apply_dynamic_toll_calibrations(session, matrix, config)
subsession.set_group_matrix(matrix)
initialize_group_capacity_sequences(subsession, config)
```

During every round after `apply_round_capacity(group, config)`:

```python
apply_round_toll(group)
```

The selected toll result must be copied to every current-round Player before pages render.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicTollApplicationTests`

Expected: PASS.

### Task 4: Cost Calculation, Export, and Admin Report

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/admin_report.html`

- [ ] **Step 1: Write failing cost/export/report tests**

```python
def test_effective_toll_is_added_to_total_cost(self):
    components = compute_total_cost(queue_delay=2, early_minutes=0, late_minutes=1, toll=3)
    self.assertEqual(components['total_cost'], 12 + 4 + 0 + 3 + 3)

def test_export_contains_effective_toll_metadata(self):
    required = {
        'coarse_toll_source', 'coarse_toll_calibration_capacity',
        'coarse_toll_time_window_spec', 'coarse_toll_points', 'coarse_toll_charge',
        'departure_schedule_first_time', 'departure_schedule_last_time',
    }
    self.assertTrue(required.issubset(EXPORT_HEADERS))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicCostExportTests`

Expected: FAIL on missing fields/helpers.

- [ ] **Step 3: Apply actual toll in `set_results`**

Replace the static session-slot lookup with current Player effective toll helpers:

```python
toll = coarse_toll_for_player_minute(player, departure_minute)
total_cost = round(
    C.FIXED_TRAVEL_TIME_COST
    + C.QUEUE_COST_PER_MINUTE * queue_delay
    + C.EARLY_COST_PER_MINUTE * early_minutes
    + C.LATE_COST_PER_MINUTE * late_minutes
    + toll,
    2,
)
player.coarse_toll_charge = cu(toll)
```

- [ ] **Step 4: Extend export and admin aggregation**

Append effective toll and schedule fields to `EXPORT_HEADERS` and `export_row_for_player`. Add `coarse_toll_time_window_spec`, `coarse_toll_points` and average `coarse_toll_charge` to each admin round row and template columns.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicCostExportTests`

Expected: PASS.

### Task 5: Participant Information and Decision UI Sync

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/Introduction.html`
- Modify: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Modify: `dynamic_bottleneck_round/Decision.html`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: Write failing template contract tests**

```python
def test_introduction_contains_cost_queue_toll_and_dynamic_capacity(self):
    html = self.template_text('Introduction.html')
    for text in ['固定行驶成本', '排队成本', '早到成本', '晚到成本', '粗收费', '同一分钟', '瓶颈服务率']:
        self.assertIn(text, html)

def test_decision_wheel_displays_toll_per_time(self):
    html = self.template_text('Decision.html')
    self.assertIn('time-wheel', html)
    self.assertIn('收费 {{ item.toll_charge_label }}', html)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicTemplateTests`

Expected: FAIL on missing synchronized content and wheel markup.

- [ ] **Step 3: Synchronize Introduction and comprehension questions**

Pass the same participant-facing constants used by single bottleneck plus `capacity_states`, draw-mode-specific labels, current schedule range and coarse toll description. Replace the current three-question test with four concrete questions and client-side answer explanations.

- [ ] **Step 4: Replace slider with the stable time wheel**

Use the existing single-bottleneck wheel interaction pattern, but source all rows from dynamic `choice_preview`. Each row must render:

```html
<span class="time-wheel-time">{{ item.time }}</span>
<span class="time-wheel-tag">
  {{ if item.toll_active }}收费 {{ item.toll_charge_label }}{{ else }}无收费{{ endif }}
</span>
```

Keep `departure_minute` as the submitted field and use small previous/next arrow buttons inside the wheel card.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicTemplateTests`

Expected: PASS.

### Task 6: Cost-Centered Results and Sync Page

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/Results.html`
- Modify: `dynamic_bottleneck_round/ResultsSync.html`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: Write failing results-page tests**

```python
def test_results_is_cost_centered_and_combines_group_costs_and_counts(self):
    html = self.template_text('Results.html')
    self.assertIn('本轮成本与用时', html)
    self.assertIn('所有参与者的成本分布', html)
    self.assertIn('粗收费', html)
    self.assertNotIn('最终收益</span>', html)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicResultsTemplateTests`

Expected: FAIL because the current result page still shows payoff and a separate count-only chart.

- [ ] **Step 3: Build current-round cost snapshot helper**

Group current-round players by departure minute and return bars containing time, participant count, shared average total cost, height percentage and current-player marker. Also return the all-player average cost and average-line position.

- [ ] **Step 4: Synchronize result and waiting templates**

Render service-rate history, personal time/cost summary, cost breakdown and one combined cost/count chart. Remove the current-round payoff metric; keep payoff calculation unchanged for `payment_info`. Update `ResultsSync` wording and status hierarchy while preserving `data-poll="{{ poll_interval_ms }}"` and the 3-second backend value.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicResultsTemplateTests`

Expected: PASS.

### Task 7: Settings, Bots, and Full Regression

**Files:**
- Modify: `settings.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing settings and bot assertions**

Assert both dynamic configs enable automatic schedule/toll, disable rewards, expose actual effective toll in before mode, maintain same-time equal costs and reject an after-decision auto-toll test config.

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `python -m unittest dynamic_bottleneck_round.tests`

Expected: FAIL until settings and bot submissions are updated for the wheel/schedule/toll fields.

- [ ] **Step 3: Update dynamic common settings**

Add the approved automatic schedule and toll settings to `DYNAMIC_BOTTLENECK_ROUND_COMMON`. Keep `reward_treatment_enabled=0` and the existing dynamic capacity sequence settings.

- [ ] **Step 4: Run static verification**

```bash
python3 -m py_compile dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/toll_calibration.py dynamic_bottleneck_round/tests.py settings.py
git diff --check -- dynamic_bottleneck_round settings.py
rg -n 'style="[^"]*\{\{' dynamic_bottleneck_round/*.html
```

Expected: compile and diff checks pass; the final `rg` produces no output.

- [ ] **Step 5: Run dynamic app bots**

```bash
OTREE_DATABASE_URL=sqlite:////tmp/dynamic_bottleneck_toll_5.sqlite3 OTREE_ADMIN_PASSWORD=devpass otree test dynamic_bottleneck_round_demo 5
OTREE_DATABASE_URL=sqlite:////tmp/dynamic_bottleneck_toll_60.sqlite3 OTREE_ADMIN_PASSWORD=devpass otree test dynamic_bottleneck_round_demo 60
```

Expected: staggered and same-time cases complete all 10 rounds; 5-person uses exact or cached calibration and 60-person uses large-group mode.

- [ ] **Step 6: Run original app regression**

```bash
OTREE_DATABASE_URL=sqlite:////tmp/single_bottleneck_dynamic_regression.sqlite3 OTREE_ADMIN_PASSWORD=devpass otree test single_bottleneck_demo 5
```

Expected: all existing `single_bottleneck` bot cases pass unchanged.

- [ ] **Step 7: Inspect final diff**

Confirm `single_bottleneck` business files are unchanged, no user database was reset, and only the approved dynamic app/settings/docs files are part of this implementation.
