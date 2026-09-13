# Room Label Treatment Group Implementation Plan

> **Execution:** Implement this plan inline, task by task, with test-driven development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Room participant labels deterministically select H/HA treatment groups regardless of entry order, and add paired I0/I1 Session presets with explicit label ranges.

**Architecture:** Add a pure label-assignment module that loads the configured Room label file, validates it, and binds labels to the already ordered oTree Participant records before the round-1 group matrix is created. Keep oTree Room unchanged: its existing exact-label lookup will resolve each arriving label to the pre-bound Participant. Expose the derived contiguous ranges in the formal Session controls and audit the expected label at the access gate.

**Tech Stack:** Python 3.10, oTree, unittest, oTree templates with vanilla JavaScript, conda environment `otree_env`.

---

### Task 1: Pure sequential label assignment domain

**Files:**
- Create: `dynamic_bottleneck_round/room_label_assignment.py`
- Create: `dynamic_bottleneck_round/test_room_label_assignment.py`

- [ ] **Step 1: Write failing tests for label loading and grouping ranges**

```python
def test_builds_contiguous_ranges_from_treatment_human_counts():
    treatments = {'G01': {'human': 30}, 'G02': {'human': 10}}
    result = build_sequential_label_plan(
        ['P001', 'P002', 'P003', 'P004'],
        {'G01': {'human': 3}, 'G02': {'human': 1}},
    )
    assert result['G01'] == ['P001', 'P002', 'P003']
    assert result['G02'] == ['P004']

def test_rejects_duplicate_or_insufficient_labels(self):
    with self.assertRaises(RoomLabelAssignmentError):
        build_sequential_label_plan(
            ['P001', 'P001'],
            {'G01': {'human': 2}},
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `conda run -n otree_env python -m unittest -q dynamic_bottleneck_round.test_room_label_assignment`

Expected: import failure because `room_label_assignment.py` does not exist.

- [ ] **Step 3: Implement the pure loader and plan builder**

```python
class RoomLabelAssignmentError(ValueError):
    pass

def load_room_labels(path):
    labels = Path(path).read_text(encoding='utf-8').split()
    if len(labels) != len(set(labels)):
        raise RoomLabelAssignmentError('Room 标签文件中存在重复标签。')
    return labels

def build_sequential_label_plan(labels, treatments):
    required = sum(int(item['human']) for item in treatments.values())
    if len(labels) < required:
        raise RoomLabelAssignmentError('Room 标签数量不足。')
    result, start = {}, 0
    for group_label, treatment in treatments.items():
        end = start + int(treatment['human'])
        result[group_label] = list(labels[start:end])
        start = end
    return result
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `conda run -n otree_env python -m unittest -q dynamic_bottleneck_round.test_room_label_assignment`

Expected: all tests pass.

### Task 2: Bind labels before formal group creation

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Create: `dynamic_bottleneck_round/test_room_label_integration.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_formal_matrix_prebinds_labels_before_grouping(self):
    players = make_players(40)
    plan = assign_formal_room_labels(players, treatments, labels)
    assert [p.participant.label for p in players[:30]] == labels[:30]
    assert [p.participant.label for p in players[30:]] == labels[30:40]
    assert players[39].participant.vars['expected_room_label'] == 'P040'

def test_label_lookup_is_independent_of_entry_order(self):
    assign_formal_room_labels(players, treatments, labels)
    assert player_by_label(players, 'P040') in matrix[1]
    assert player_by_label(players, 'P001') in matrix[0]
```

- [ ] **Step 2: Run focused lifecycle tests and verify RED**

Expected: `assign_formal_room_labels` is missing.

- [ ] **Step 3: Implement formal label binding**

```python
def assign_formal_room_labels(players, treatments, session_config):
    if session_config.get('participant_label_assignment') != 'sequential':
        raise ValueError('participant_label_assignment 必须为 sequential。')
    labels = load_room_labels(session_config['participant_label_file'])
    plan = build_sequential_label_plan(labels, treatments)
    ordered = [label for values in plan.values() for label in values]
    if len(players) != len(ordered):
        raise ValueError('Session Human 数量与标签分组计划不一致。')
    for player, label in zip(players, ordered):
        player.participant.set_label(label)
        player.participant.vars['expected_room_label'] = label
    return plan
```

Call this after `configure_formal_treatments()` and before `build_treatment_group_matrix()` in round 1. Save the label plan in `session.vars` for audit and UI/report consumers.

- [ ] **Step 4: Verify focused and existing treatment tests GREEN**

Run: `conda run -n otree_env python -m unittest -q dynamic_bottleneck_round.tests`

Expected: all tests pass.

### Task 3: Validate labels at the access gate

**Files:**
- Modify: `access_gate/__init__.py`
- Create: `access_gate/test_label_validation.py`

- [ ] **Step 1: Write failing validation tests**

```python
def test_rejects_label_that_differs_from_expected_room_label():
    player.participant.label = 'P040'
    player.participant.vars['expected_room_label'] = 'P001'
    error = AccessGate.error_message(player, {'access_password': 'gate'})
    assert 'P001' in error
```

- [ ] **Step 2: Verify RED**

Expected: current access gate accepts the correct password without checking the label.

- [ ] **Step 3: Add non-mutating label validation**

```python
expected_label = player.participant.vars.get('expected_room_label')
if expected_label and player.participant.label != expected_label:
    return f'当前入口标签不正确，请使用 {expected_label} 进入实验房间。'
```

- [ ] **Step 4: Verify access-gate tests GREEN**

Run: `conda run -n otree_env python -m unittest -q access_gate.test_label_validation`

Expected: all tests pass.

### Task 4: Add paired Session presets and label-range summaries

**Files:**
- Modify: `settings.py`
- Modify: `_templates/otree/includes/DynamicSessionControls.html`
- Create: `dynamic_bottleneck_round/test_room_label_admin.py`

- [ ] **Step 1: Write failing settings/template tests**

Assert that the formal config contains `participant_label_file` and `participant_label_assignment`, and that the template contains paired I0/I1 buttons, 40 Human totals, and P001–P030/P031–P040 summaries.

- [ ] **Step 2: Verify RED**

Run the two focused unittest classes and confirm the paired controls are missing.

- [ ] **Step 3: Implement paired presets and automatic range display**

Add formal defaults:

```python
participant_label_file='_rooms/econ101.txt',
participant_label_assignment='sequential',
```

Add preset payloads equivalent to:

```javascript
'PAIR-I0': { treatments: ['H-I0', 'HA-I0'], humans: 40 },
'PAIR-I1': { treatments: ['H-I1', 'HA-I1'], humans: 40 },
```

Derive every displayed range from cumulative Human counts, not hard-coded group numbers. Keep existing single and custom options available.

- [ ] **Step 4: Verify settings and template tests GREEN**

Expected: the formal page reports the exact treatment and label range for every group.

### Task 5: Regression and full-flow verification

**Files:**
- Modify only if a failing test identifies an in-scope defect.

- [ ] **Step 1: Run all unit tests**

Run: `conda run -n otree_env python -m unittest discover -q`

Expected: all tests pass.

- [ ] **Step 2: Run compile and diff checks**

Run: `conda run -n otree_env python -m compileall -q access_gate dynamic_bottleneck_round`

Run: `git diff --check`

Expected: no output and exit status 0.

- [ ] **Step 3: Create a 40-Human paired formal Session against an isolated SQLite database**

Use `DATABASE_URL` pointing to a temporary SQLite file. Configure `G01:H-I0;G02:HA-I0`, verify 30/10 Human membership, P001–P030/P031–P040 labels, shared capacity, and independent groups.

- [ ] **Step 4: Run the oTree Bot flow**

Run the formal workflow in `otree_env` with isolated test data and verify access gate, all 35 rounds, survey, payment page, and Room-compatible labels.

- [ ] **Step 5: Commit only in-scope files**

Stage the new label module/tests, access gate, dynamic backend/tests, Session controls, settings, and plan. Do not stage unrelated paper outputs or source files already present in the worktree.
