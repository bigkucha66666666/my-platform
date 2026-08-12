# Dynamic Decision and Results Visual Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved soft commute visual direction to the dynamic bottleneck decision and results pages without changing experiment behavior.

**Architecture:** Keep all oTree variables, form fields, template loops, data attributes, and JavaScript behavior intact. Add template-level visual hooks and inline decorative SVGs, then restyle only those hooks with page-local CSS. Template contract tests protect the required visual structure while existing unit and bot tests protect the experiment flow.

**Tech Stack:** oTree templates, HTML, CSS, inline SVG, vanilla JavaScript, Python `unittest`, oTree bots.

---

### Task 1: Lock the visual contract with failing tests

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Add decision-page visual contract assertions**

Extend `test_decision_has_separate_reveal_messages()` with:

```python
self.assertIn('class="capacity-road-scene"', html)
self.assertIn('class="commute-car capacity-car"', html)
self.assertIn('class="picker-card commute-picker"', html)
self.assertIn('class="wheel-arrow wheel-arrow-previous"', html)
self.assertIn('class="wheel-arrow wheel-arrow-next"', html)
self.assertIn('aria-hidden="true"', html)
```

- [ ] **Step 2: Add results-page visual contract assertions**

Extend `test_results_is_cost_centered_with_combined_group_chart()` with:

```python
self.assertIn('class="journey-track"', html)
self.assertIn('class="commute-car journey-car"', html)
self.assertIn('class="journey-route"', html)
self.assertNotIn('bottleneck-route-visual', html)
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.tests.DynamicTemplateContractTests.test_decision_has_separate_reveal_messages \
  dynamic_bottleneck_round.tests.DynamicTemplateContractTests.test_results_is_cost_centered_with_combined_group_chart
```

Expected: both tests fail because the new visual hooks are not present.

### Task 2: Implement the decision-page soft commute design

**Files:**
- Modify: `dynamic_bottleneck_round/Decision.html`
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Add functional visual hooks without changing form behavior**

Keep the current `capacity_revealed` conditional and insert the same road scene in both branches:

```html
<div class="capacity-road-scene" aria-hidden="true">
  <span class="road-dash"></span>
  <svg class="commute-car capacity-car" viewBox="0 0 64 40">
    <path class="car-body" d="M13 26h38l-4-12a6 6 0 0 0-6-4H23a6 6 0 0 0-6 4l-4 12Z"/>
    <path class="car-base" d="M8 25h48v8H8z"/>
    <path class="car-window" d="M22 14h19l2 7H19l3-7Z"/>
    <circle class="car-wheel" cx="18" cy="33" r="5"/>
    <circle class="car-wheel" cx="46" cy="33" r="5"/>
  </svg>
</div>
```

Add `commute-picker`, `wheel-arrow-previous`, and `wheel-arrow-next` classes while preserving the existing IDs.

- [ ] **Step 2: Replace rigid styling with the approved visual system**

Use:

```css
.decision-shell {
  --ink: #18343c;
  --muted: #657b80;
  --line: #d4e2e2;
  --teal: #177d86;
  --teal-dark: #173f49;
  --warm: #e28b3e;
  --surface: #fbfdfc;
  display: grid;
  gap: 16px;
}

.capacity-panel,
.picker-card {
  border: 1px solid var(--line);
  border-radius: 17px;
  box-shadow: 0 10px 28px rgba(27, 69, 75, .08);
}
```

The capacity road uses a dark road surface, dashed center line, and warm car. The selected time row uses a dark teal surface; toll text becomes a warm pill. Arrow controls become circular and remain adjacent to the wheel. The main submit button receives a teal rounded style scoped to `.decision-shell`.

- [ ] **Step 3: Preserve mobile behavior**

At `max-width: 600px`, stack the capacity illustration below the copy, keep the round pill visible, reduce wheel side controls to 32 pixels, and ensure rows remain two-column unless available width requires wrapping.

- [ ] **Step 4: Run the focused decision test and verify GREEN**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.tests.DynamicTemplateContractTests.test_decision_has_separate_reveal_messages
```

Expected: PASS.

### Task 3: Implement the results-page soft commute design

**Files:**
- Modify: `dynamic_bottleneck_round/Results.html`
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Add the car to the existing combined journey bar**

Keep the current departure, arrival, fixed-travel, queue, participant-count, and total-travel values. Wrap the time bar as:

```html
<div class="journey-track">
  <svg class="commute-car journey-car" viewBox="0 0 64 40" aria-hidden="true">
    <path class="car-body" d="M13 26h38l-4-12a6 6 0 0 0-6-4H23a6 6 0 0 0-6 4l-4 12Z"/>
    <path class="car-base" d="M8 25h48v8H8z"/>
    <circle class="car-wheel" cx="18" cy="33" r="5"/>
    <circle class="car-wheel" cx="46" cy="33" r="5"/>
  </svg>
  <div class="journey-route">
    <div class="time-fixed">...</div>
    {{ if queue_delay != '0' }}<div class="time-queue">...</div>{{ endif }}
  </div>
</div>
```

Do not introduce a separate route diagram.

- [ ] **Step 2: Apply rounded hierarchy to overview and cost cards**

Use 17-pixel outer cards, 11-to-13-pixel inner metric cards, subtle teal shadows, an off-white surface, and a dark teal capacity panel. Keep the warm color reserved for total cost and queue segments.

- [ ] **Step 3: Polish the existing group chart without changing its semantics**

Retain:

- `cost_snapshot_bars`
- `data-height`
- `data-bottom`
- the purple average line
- current participant highlight
- participant count bands
- horizontal scrolling

Only update card radius, spacing, surfaces, and selected-state contrast.

- [ ] **Step 4: Run the focused results test and verify GREEN**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.tests.DynamicTemplateContractTests.test_results_is_cost_centered_with_combined_group_chart
```

Expected: PASS.

### Task 4: Run regressions and visual QA

**Files:**
- Verify: `dynamic_bottleneck_round/Decision.html`
- Verify: `dynamic_bottleneck_round/Results.html`
- Verify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Run static checks**

```bash
git diff --check -- \
  dynamic_bottleneck_round/Decision.html \
  dynamic_bottleneck_round/Results.html \
  dynamic_bottleneck_round/tests.py

python3 -m py_compile \
  dynamic_bottleneck_round/__init__.py \
  dynamic_bottleneck_round/tests.py \
  dynamic_bottleneck_round/toll_calibration.py \
  settings.py
```

Expected: no output from `git diff --check`; Python compilation exits successfully.

- [ ] **Step 2: Run dynamic app unit tests**

```bash
python -m unittest dynamic_bottleneck_round.tests
```

Expected: all tests pass.

- [ ] **Step 3: Run isolated oTree bot tests**

Copy the project to a temporary directory while excluding `db.sqlite3`, activate `otree_env`, and run:

```bash
otree test dynamic_bottleneck_round_demo 2
otree test single_bottleneck_demo 5
```

Expected: all dynamic cases complete 10 rounds and both original single-bottleneck cases remain green.

- [ ] **Step 4: Review in a browser**

Check the decision and results pages at desktop width and at approximately 390 pixels:

- no horizontal page overflow;
- capacity copy and road illustration do not overlap;
- wheel clicks and arrow buttons still select the adjacent minute;
- toll badge remains readable;
- vehicle marker does not obscure journey labels;
- result chart remains horizontally scrollable.

- [ ] **Step 5: Do not create a commit unless explicitly requested**

Leave the verified working-tree changes ready for user review.

