# Accident Bottleneck 2×2, 30-Round Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current 2×3, 60-formal-round, 20-actor accident bottleneck runtime with the approved 2×2, 30-formal-round, 30-actor design, including visible I1 accident status on the departure decision page.

**Architecture:** Keep accident generation, settlement, and page flow intact while narrowing the public information contract to I0/I1, truncating the frozen bank deterministically, and enforcing four explicit production Session presets. Treat Human, LLM, and Liu-REL inputs as consumers of one condition-limited public context so the I1 signal is visible without leaking loss severity or actual capacity.

**Tech Stack:** Python 3 in conda environment `otree`, oTree, Django templates, `unittest`, JSON sequence bank.

---

## File Structure

- Modify `dynamic_bottleneck_round/accident_capacity.py`: define the legal information conditions and validate 30-round frozen sequences.
- Modify `dynamic_bottleneck_round/capacity_sequence_bank.json`: retain exactly the first 30 records of S01–S05 and recompute metadata.
- Modify `dynamic_bottleneck_round/generate_accident_sequence_bank.py`: generate future banks with 30 formal rounds.
- Modify `dynamic_bottleneck_round/__init__.py`: set round constants, recognize all production presets, enforce actor composition, build public context, and remove I2 from RL integration.
- Modify `dynamic_bottleneck_round/agents/liu_rel_agent.py`: expose only I0/I1 and retire capacity-bandwidth behavior.
- Modify `dynamic_bottleneck_round/agents/independent_rl_agent.py`: align the wrapper and policy version with Liu-REL v2.
- Modify `settings.py`: provide four explicit formal Session configs with the correct Human/LLM/RL defaults and 30-round payoff metadata.
- Modify `_templates/otree/includes/DynamicSessionControls.html`: recognize the new config names and describe 30-round fixed sequences while preserving existing sequence-selector edits.
- Modify `dynamic_bottleneck_round/Decision.html`: render the I1 Boolean accident signal before submission without numeric capacity.
- Modify `dynamic_bottleneck_round/FormalStart.html`: change formal-round copy to 30.
- Modify `dynamic_bottleneck_survey/__init__.py`: constrain round-reference choices to 1–30.
- Modify tests in `dynamic_bottleneck_round/test_accident_capacity.py`, `dynamic_bottleneck_round/tests.py`, `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`, `dynamic_bottleneck_round/agents/test_independent_rl_agent.py`, `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`, and `dynamic_bottleneck_survey/tests.py`.

### Task 1: Narrow Accident Information and Frozen Sequences

**Files:**
- Modify: `dynamic_bottleneck_round/test_accident_capacity.py`
- Modify: `dynamic_bottleneck_round/accident_capacity.py`
- Modify: `dynamic_bottleneck_round/generate_accident_sequence_bank.py`
- Modify: `dynamic_bottleneck_round/capacity_sequence_bank.json`

- [ ] **Step 1: Write failing tests for I2 rejection and 30-record banks**

Add assertions equivalent to:

```python
def test_rejects_i2_information_condition(self):
    with self.assertRaisesRegex(
        AccidentRiskConfigError,
        'accident_information_condition 必须是 I0 或 I1',
    ):
        parse_accident_risk_config({'accident_information_condition': 'I2'})

def test_frozen_bank_contains_exactly_thirty_rounds(self):
    bank = load_accident_sequence_bank()
    self.assertEqual(set(bank), {'S01', 'S02', 'S03', 'S04', 'S05'})
    for records in bank.values():
        self.assertEqual(len(records), 30)
        self.assertEqual(
            [record['formal_round_number'] for record in records],
            list(range(1, 31)),
        )
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.test_accident_capacity -v
```

Expected: failures report that I2 is still accepted and bank sequences still contain 60 rounds.

- [ ] **Step 3: Implement the two-condition and 30-round contract**

Use these constants and branches in `accident_capacity.py`:

```python
INFO_I0 = 'I0'
INFO_I1 = 'I1'
INFORMATION_CONDITIONS = {INFO_I0, INFO_I1}
FORMAL_ROUNDS = 30

if information_condition not in INFORMATION_CONDITIONS:
    raise AccidentRiskConfigError(
        'accident_information_condition 必须是 I0 或 I1。'
    )

context['capacity_revealed'] = bool(after_decision or warmup)
if config.information_condition == INFO_I1:
    context['incident_occurred'] = bool(record['incident_occurred'])
```

Change bank metadata and validation from 60 to `FORMAL_ROUNDS`. Update the generator to call `generate_accident_sequence(..., rounds=FORMAL_ROUNDS, ...)`. Mechanically truncate every sequence to `rounds[:30]`, replace `formal_rounds` with 30, set `incident_rounds` from retained records, and calculate `mean_actual_capacity = sum(actual_capacity) / 30`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same unittest command. Expected: all accident-capacity tests pass.

- [ ] **Step 5: Commit the accident contract**

```bash
git add dynamic_bottleneck_round/accident_capacity.py dynamic_bottleneck_round/generate_accident_sequence_bank.py dynamic_bottleneck_round/capacity_sequence_bank.json dynamic_bottleneck_round/test_accident_capacity.py
git commit -m "feat: narrow accident design to I0 and I1"
```

### Task 2: Enforce 30-Round, 30-Actor Production Sessions

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: Write failing round and composition tests**

Add or replace tests with:

```python
def test_five_warmups_precede_thirty_formal_rounds(self):
    self.assertEqual(C.WARMUP_ROUNDS, 5)
    self.assertEqual(C.FORMAL_ROUNDS, 30)
    self.assertEqual(C.NUM_ROUNDS, 35)
    self.assertEqual(formal_round_number(6), 1)
    self.assertEqual(formal_round_number(35), 30)

def test_formal_compositions_are_fixed_at_thirty_actors(self):
    human = self.make_session('dynamic_bottleneck_round_prod_h_i0')
    mixed = self.make_session(
        'dynamic_bottleneck_round_prod_ha_i1', api_count=10, rl_count=10
    )
    self.assertEqual(validate_formal_actor_composition(human, [[object()] * 30]), 'H')
    self.assertEqual(validate_formal_actor_composition(mixed, [[object()] * 10]), 'HA')
```

Also assert that 20/0/0, 16/2/2, 30/10/10, multiple groups, and any use of `group_agent_spec` are rejected for production configs.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.tests.AccidentExperimentContractTests -v
```

Expected: old constants and old 20-actor validation cause failures.

- [ ] **Step 3: Implement formal config recognition and composition validation**

Add one shared production-name set and helper:

```python
FORMAL_SESSION_CONFIG_NAMES = {
    'dynamic_bottleneck_round_prod_h_i0',
    'dynamic_bottleneck_round_prod_h_i1',
    'dynamic_bottleneck_round_prod_ha_i0',
    'dynamic_bottleneck_round_prod_ha_i1',
}

def is_formal_session(session):
    return session.config.get('name') in FORMAL_SESSION_CONFIG_NAMES
```

Set `C.FORMAL_ROUNDS = 30`. Replace every exact comparison with the old production name by `is_formal_session(...)`. Validate only `(30, 0, 0)` as `H` and `(10, 10, 10)` as `HA`, retaining the one-group and empty-`group_agent_spec` restrictions.

- [ ] **Step 4: Run the complete app tests and verify GREEN**

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.tests -v
```

Expected: all `dynamic_bottleneck_round.tests` pass.

- [ ] **Step 5: Commit the runtime contract**

```bash
git add dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/tests.py
git commit -m "feat: enforce thirty-round thirty-actor sessions"
```

### Task 3: Add Four Safe Formal Session Presets

**Files:**
- Modify: `settings.py`
- Modify: `_templates/otree/includes/DynamicSessionControls.html`
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_survey/tests.py`

- [ ] **Step 1: Write failing tests for the four configs**

Assert the exact map:

```python
expected = {
    'dynamic_bottleneck_round_prod_h_i0': (30, 0, 0, 'I0'),
    'dynamic_bottleneck_round_prod_h_i1': (30, 0, 0, 'I1'),
    'dynamic_bottleneck_round_prod_ha_i0': (10, 10, 10, 'I0'),
    'dynamic_bottleneck_round_prod_ha_i1': (10, 10, 10, 'I1'),
}
for name, (humans, llm, rl, condition) in expected.items():
    config = configs[name]
    self.assertEqual(config['num_demo_participants'], humans)
    self.assertEqual(config['api_agent_count_per_group'], llm)
    self.assertEqual(config['rl_agent_count_per_group'], rl)
    self.assertEqual(config['accident_information_condition'], condition)
    self.assertEqual(config['payoff_rounds'], 30)
    self.assertEqual(config['dynamic_capacity_sequence_preset'], 'S01')
```

Assert the old `dynamic_bottleneck_round_prod` is absent and all four new names appear in the admin controls. Assert the old A/B/C/D card labels and 20 Human / 15 Human + 5 Agent summaries are absent, while the four new treatment labels and exact 30-subject compositions are present.

- [ ] **Step 2: Run config tests and verify RED**

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.tests dynamic_bottleneck_survey.tests dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v
```

Expected: failures identify the missing four configs and remaining old name/round metadata.

- [ ] **Step 3: Implement settings and admin-control defaults**

Replace the single production dict with four dicts using the shared dynamic configuration. H configs set `api_agent_mode='off'`, both Agent counts to 0, and `num_demo_participants=30`. HA configs set `api_agent_mode='active'`, both Agent counts to 10, `rl_agent_enabled=1`, and `num_demo_participants=10`. All four set S01 and their exact I0/I1 condition. Set payoff label/rounds to 30. In production validation, map each config name to its exact required `(actor_composition, information_condition)` pair and reject manual values that conflict with the selected name.

In `DynamicSessionControls.html`, replace the old A/B/C/D cards with H-I0, H-I1, HA-I0, and HA-I1 cards. Each card must switch the `session_config` dropdown to its matching formal config, dispatch the normal change synchronization, set Human login seats to 30 or 10, and show `30 Human` or `10 Human + 10 LLM + 10 RL` plus `总主体数 30`. Make the dynamic config set contain the demo plus all four production names, change Agent input maxima from 5 to 10, and change fixed-sequence help text to `固定30轮序列`. Preserve the existing custom sequence selector and its form-field synchronization; remove the old cross-group `group_agent_spec` presets and the custom D card.

- [ ] **Step 4: Re-run config tests and verify GREEN**

Run the same command. Expected: all selected modules pass.

- [ ] **Step 5: Commit Session presets**

```bash
git add settings.py _templates/otree/includes/DynamicSessionControls.html dynamic_bottleneck_round/tests.py dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py dynamic_bottleneck_survey/tests.py
git commit -m "feat: add four accident treatment presets"
```

### Task 4: Show I1 Accident Status on the Decision Page

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/Decision.html`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: Write failing public-context and template tests**

Exercise both Boolean states and I0:

```python
def test_i1_decision_context_exposes_only_boolean_signal(self):
    context = decision_context(condition='I1', incident=True, capacity=1.5)
    self.assertTrue(context['incident_status_revealed'])
    self.assertEqual(context['incident_status_text'], '本轮发生事故')
    self.assertNotIn('actual_capacity', context)
    self.assertNotIn('capacity_loss_ratio', context)

def test_i0_decision_context_hides_current_status(self):
    context = decision_context(condition='I0', incident=True, capacity=1.5)
    self.assertFalse(context['incident_status_revealed'])
    self.assertNotIn('incident_occurred', context)
```

Add template assertions for `本轮发生事故`, `本轮未发生事故`, and an I1-only conditional block.

- [ ] **Step 2: Run the Decision tests and verify RED**

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.tests.DecisionTemplateTests -v
```

Expected: the current template only distinguishes exact capacity reveal and does not display I1 status.

- [ ] **Step 3: Implement explicit Boolean status display**

Derive template-only presentation fields from the already-filtered public context:

```python
incident_status_revealed = (
    public_context['information_condition'] == INFO_I1
    and 'incident_occurred' in public_context
)
context.update({
    'incident_status_revealed': incident_status_revealed,
    'incident_status_text': (
        '本轮发生事故'
        if incident_status_revealed and public_context['incident_occurred']
        else '本轮未发生事故'
    ) if incident_status_revealed else '',
})
```

Render a status panel under `{{ if incident_status_revealed }}`. Do not render `actual_capacity`, loss ratio, or remaining ratio in that block. Keep I0's generic pre-decision uncertainty panel.

- [ ] **Step 4: Verify Decision behavior GREEN**

Run the focused class and then `dynamic_bottleneck_round.tests`. Expected: both pass.

- [ ] **Step 5: Commit the I1 disclosure**

```bash
git add dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/Decision.html dynamic_bottleneck_round/tests.py
git commit -m "feat: disclose I1 accident status before choice"
```

### Task 5: Retire I2 from Liu-REL

**Files:**
- Modify: `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_independent_rl_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_round/agents/liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/agents/independent_rl_agent.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `settings.py`

- [ ] **Step 1: Write failing v2 and I2-rejection tests**

Add tests equivalent to:

```python
def test_i2_is_rejected(self):
    with self.assertRaisesRegex(LiuRELAlgorithmError, 'I0 or I1'):
        select_information_conditioned_experiences(
            [], information_condition='I2', current_incident_occurred=True
        )

def test_policy_version_is_v2_without_capacity_bandwidth(self):
    config = validate_liu_rel_session_config(self.session())
    self.assertEqual(config['rel_policy_version'], 'dynamic_liu_rel_incident_v2')
    self.assertNotIn('rel_capacity_bandwidth', config)
```

Update integration expectations so only I0/I1 are iterated and no decision/audit payload contains `current_actual_capacity` or `rel_capacity_bandwidth`.

- [ ] **Step 2: Run Agent tests and verify RED**

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.agents.test_liu_rel_agent dynamic_bottleneck_round.agents.test_independent_rl_agent dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v
```

Expected: I2 is accepted and v1/bandwidth fields remain.

- [ ] **Step 3: Implement the v2 two-condition policy**

Set `INDEPENDENT_RL_POLICY_VERSION = 'dynamic_liu_rel_incident_v2'`, restrict the algorithm condition set to `{'I0', 'I1'}`, delete the I2 Gaussian-kernel branch, remove `current_actual_capacity` and `capacity_bandwidth` from public calls, and retain I1 → I0 → uniform sparse fallback. Remove `rel_capacity_bandwidth` from settings, formal validation, state, and audit output.

- [ ] **Step 4: Run Agent tests and verify GREEN**

Run the same three-module command. Expected: all Agent tests pass with no I2 or current-capacity path.

- [ ] **Step 5: Commit the Liu-REL v2 policy**

```bash
git add dynamic_bottleneck_round/agents/liu_rel_agent.py dynamic_bottleneck_round/agents/independent_rl_agent.py dynamic_bottleneck_round/__init__.py settings.py dynamic_bottleneck_round/agents/test_liu_rel_agent.py dynamic_bottleneck_round/agents/test_independent_rl_agent.py dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py
git commit -m "feat: narrow Liu REL to I0 and I1"
```

### Task 6: Align Runtime Copy, Survey Bounds, and Full Validation

**Files:**
- Modify: `dynamic_bottleneck_round/FormalStart.html`
- Modify: `dynamic_bottleneck_survey/__init__.py`
- Modify: `dynamic_bottleneck_survey/tests.py`
- Modify: any dynamic-app test fixture still encoding the retired contract

- [ ] **Step 1: Write failing round-copy and survey-bound tests**

Assert `FormalStart.html` contains `正式实验共 30 轮` and does not contain `60 轮`; assert the survey reference choices end at `['30', '正式第 30 轮']`.

- [ ] **Step 2: Run survey and app tests and verify RED**

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.tests dynamic_bottleneck_survey.tests -v
```

Expected: stale 60-round text/choices fail.

- [ ] **Step 3: Make the minimal consistency changes**

Replace only runtime references that define or describe the executed round count. Generate survey choices with `range(1, 31)` and label the final entry `正式第 30 轮`. Do not alter questionnaire wording unrelated to the round bound.

- [ ] **Step 4: Run the complete regression suite**

```bash
conda run -n otree python -m unittest dynamic_bottleneck_round.test_accident_capacity dynamic_bottleneck_round.tests dynamic_bottleneck_round.agents.test_liu_rel_agent dynamic_bottleneck_round.agents.test_independent_rl_agent dynamic_bottleneck_round.agents.test_dynamic_agent_integration dynamic_bottleneck_survey.tests single_bottleneck.tests -v
```

Expected: all tests pass without warnings or errors.

- [ ] **Step 5: Run configuration and 35-round smoke checks**

Use oTree's project checks and a deterministic backend simulation in the `otree` environment. Verify all four production configs create with S01 and that a demo reaches raw round 35/formal round 30, saves final payoff once, and never exposes capacity before an I0/I1 decision.

- [ ] **Step 6: Scan for stale executable-contract references**

```bash
rg -n "INFO_I2|I0、I1、I2|I0/I1/I2|dynamic_bottleneck_round_prod'|FORMAL_ROUNDS = 60|正式实验共 60 轮|固定60轮序列" dynamic_bottleneck_round dynamic_bottleneck_survey settings.py _templates/otree/includes/DynamicSessionControls.html
```

Expected: no executable/config/template references to the retired contract; historical migration comments are allowed only when clearly marked.

- [ ] **Step 7: Commit final consistency updates**

```bash
git add dynamic_bottleneck_round dynamic_bottleneck_survey settings.py _templates/otree/includes/DynamicSessionControls.html
git commit -m "test: verify 2x2 accident experiment contract"
```
