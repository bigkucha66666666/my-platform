# Accident Bottleneck Liu-REL Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the independent dynamic-bottleneck RL participant with the incident-conditioned Liu-REL model specified in `2026-09-09-accident-bottleneck-liu-rel-agent-design.md`, without changing the LLM fallback, experiment UI, accident process, queue, cost, or payment logic.

**Architecture:** Put all Liu-REL statistics, interpolation, Softmax, state validation, experience filtering, and stable random sampling in a new oTree-independent pure module. Keep the current independent-RL wrapper API, but make it delegate only to Liu-REL. Adapt the oTree integration to pass only information available under I0/I1/I2 and append each RL participant's own settled experience once.

**Tech Stack:** Python 3.10, oTree 6.0.13, standard-library `dataclasses`, `hashlib`, `math`, `random`, `unittest`.

---

### Task 1: Pure Liu-REL state and statistics

**Files:**
- Create: `dynamic_bottleneck_round/agents/liu_rel_agent.py`
- Create: `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`

- [ ] **Step 1: Write failing state and statistics tests**

Add tests for `dynamic_liu_rel_incident_v1`, version reset, duplicate-round protection, warmup update rejection, weighted population mean/standard deviation, and the exact propensity equation `mean - lambda * standard_deviation`.

```python
state = initial_liu_rel_state()
self.assertEqual(state['policy_version'], 'dynamic_liu_rel_incident_v1')
self.assertEqual(state['experiences'], [])

stats = weighted_cost_statistics_by_slot(experiences, rel_lambda=0.25)
self.assertAlmostEqual(stats[3]['propensity'], mean - 0.25 * population_std)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.agents.test_liu_rel_agent -q
```

Expected: import failure because `liu_rel_agent.py` does not exist.

- [ ] **Step 3: Implement serializable state and weighted statistics**

Implement these pure interfaces:

```python
LIU_REL_POLICY_VERSION = 'dynamic_liu_rel_incident_v1'

def initial_liu_rel_state() -> dict: ...
def valid_or_initial_liu_rel_state(state) -> dict: ...
def append_liu_rel_experience(state, *, formal_round_number, departure_slot,
                              total_cost, incident_occurred, actual_capacity,
                              decision_source='') -> dict: ...
def weighted_cost_statistics_by_slot(weighted_experiences, *, rel_lambda) -> dict: ...
```

Use deep copies, finite-number validation, `ddof=0`, no counterfactual observations, and one record per formal round.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run the command from Step 2. Expected: the Task 1 test subset passes.

- [ ] **Step 5: Commit**

```bash
git add dynamic_bottleneck_round/agents/liu_rel_agent.py dynamic_bottleneck_round/agents/test_liu_rel_agent.py
git commit -m "feat: add Liu REL experience model"
```

### Task 2: Information conditioning, interpolation, and Softmax

**Files:**
- Modify: `dynamic_bottleneck_round/agents/liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`

- [ ] **Step 1: Write failing policy-mechanics tests**

Cover I0 ignoring current accident/capacity, I1 same-status selection and I0 fallback, I2 Gaussian-kernel weights and I1/I0/uniform fallback, interpolation, one-step extrapolation with constant tails, finite normalized Softmax probabilities, and greater probability for lower propensity.

```python
propensities = interpolate_propensities(
    [1, 2, 3, 4, 5],
    {2: {'propensity': 10.0}, 4: {'propensity': 14.0}},
)
self.assertEqual(propensities, {1: 8.0, 2: 10.0, 3: 12.0, 4: 14.0, 5: 16.0})
```

- [ ] **Step 2: Run focused tests and confirm RED**

Expected: missing selection, interpolation, or probability functions.

- [ ] **Step 3: Implement information-conditioned mechanics**

Implement:

```python
def select_information_conditioned_experiences(...): ...
def interpolate_propensities(available_slot_numbers, statistics_by_slot): ...
def liu_rel_choice_probabilities(propensities, *, rel_eta, phi_floor=1e-9): ...
```

Return the selected weighted experiences plus an explicit context level. Never consult current capacity for I0 or I1. For I2, calculate `exp(-((current_capacity-history_capacity)**2)/(2*h*h))`; fall back in the required I2 → I1 → I0 → uniform order.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Expected: all mechanics tests pass.

- [ ] **Step 5: Commit**

```bash
git add dynamic_bottleneck_round/agents/liu_rel_agent.py dynamic_bottleneck_round/agents/test_liu_rel_agent.py
git commit -m "feat: implement incident-conditioned Liu REL policy"
```

### Task 3: Stable stochastic decisions and audit output

**Files:**
- Modify: `dynamic_bottleneck_round/agents/liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`

- [ ] **Step 1: Write failing sampling tests**

Cover uniform warmup, uniform first two formal rounds, sparse-history uniform choice, identical-input reproducibility, distinct Agent seed fingerprints, probability-based sampling, and audit fields without a full seed.

- [ ] **Step 2: Run focused tests and confirm RED**

Expected: missing `choose_liu_rel_departure()` or missing audit fields.

- [ ] **Step 3: Implement deterministic local sampling**

Implement:

```python
def choose_liu_rel_departure(*, state, available_slots, formal_round_number,
                             information_condition, rel_lambda, rel_eta,
                             rel_capacity_bandwidth, session_code, group_id,
                             agent_id, rel_random_seed,
                             rel_initial_uniform_rounds=2, warmup=False,
                             current_incident_occurred=None,
                             current_actual_capacity=None) -> dict: ...
```

Derive a SHA-256 seed from session, group, Agent, formal round, policy version, and configured seed; create a local `random.Random`; sample from the probability vector; expose only a short seed fingerprint. Return the required `decision_source`, propensities, probabilities, selected probability, context level, distinct-slot count, effective observation count, parameters, policy version, and rounds observed.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Expected: all pure Liu-REL tests pass.

- [ ] **Step 5: Commit**

```bash
git add dynamic_bottleneck_round/agents/liu_rel_agent.py dynamic_bottleneck_round/agents/test_liu_rel_agent.py
git commit -m "feat: add reproducible Liu REL sampling"
```

### Task 4: Independent-RL wrapper and frozen parameter contract

**Files:**
- Modify: `dynamic_bottleneck_round/agents/independent_rl_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_independent_rl_agent.py`
- Modify: `settings.py`
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing wrapper and configuration tests**

Assert that the wrapper no longer imports `rl_fallback`, the policy version is `dynamic_liu_rel_incident_v1`, reference demo/Pilot parameters are present, `rel_initial_uniform_rounds` is exactly 2, invalid values fail, and an RL-enabled production Session rejects `rel_parameters_frozen != 1`.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
/opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.agents.test_independent_rl_agent \
  dynamic_bottleneck_round.tests.SettingsContractTests \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentConfigTests -q
```

Expected: old policy identity/imports and absent REL parameters fail.

- [ ] **Step 3: Replace wrapper delegation and parse parameters**

Make the wrapper delegate to `liu_rel_agent.py`, keeping the four public wrapper names. Add validated Session configuration for:

```python
rel_policy_version = 'dynamic_liu_rel_incident_v1'
rel_lambda = 0.25
rel_eta = 14.7445
rel_capacity_bandwidth = 0.560924
rel_random_seed = 2026090901
rel_initial_uniform_rounds = 2
rel_parameters_frozen = 0
```

The numerical defaults are explicitly demo/Pilot reference values. Production HA creation requires the operator to supply calibrated values and set the frozen flag to 1.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Expected: wrapper and configuration tests pass, and `rl_fallback.py` has no diff.

- [ ] **Step 5: Commit**

```bash
git add settings.py dynamic_bottleneck_round/agents/independent_rl_agent.py dynamic_bottleneck_round/agents/test_independent_rl_agent.py dynamic_bottleneck_round/tests.py dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py
git commit -m "feat: configure frozen Liu REL policy"
```

### Task 5: oTree decision and learning integration

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing integration tests**

Cover I0/I1/I2 input whitelists, two independently stored Agent states, warmup decisions without learning, duplicate decision reads, each Agent's own settled experience, duplicate update protection, fallback-experience markers, and full audit content in `agent_context_json`.

- [ ] **Step 2: Run integration tests and confirm RED**

Run:

```bash
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.agents.test_dynamic_agent_integration -q
```

Expected: old `rl_policy` source/state layout and missing REL audit fields fail.

- [ ] **Step 3: Adapt preparation and observation**

In `prepare_independent_rl_decisions_for_group()`, pass the current incident status only for I1/I2 and actual capacity only for I2, along with the stable seed identity and validated parameters. Serialize the returned audit dictionary into `context_json`. On algorithm exceptions preserve `rl_fallback_lowest_schedule_cost`.

In `update_independent_rl_states()`, append only `{formal_round_number, departure_slot, total_cost, incident_occurred, actual_capacity, decision_source}` for the matching independent RL Agent after settlement. Do not consume anonymous group outcomes or persona traits.

- [ ] **Step 4: Run integration and pure tests and confirm GREEN**

Run:

```bash
/opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.agents.test_liu_rel_agent \
  dynamic_bottleneck_round.agents.test_independent_rl_agent \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py
git commit -m "feat: integrate Liu REL participants"
```

### Task 6: Regression, oTree, and review verification

**Files:**
- Verify only: `dynamic_bottleneck_round/agents/rl_fallback.py`
- Verify only: Human/LLM templates and survey apps

- [ ] **Step 1: Run all dynamic tests**

```bash
/opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.test_accident_capacity \
  dynamic_bottleneck_round.tests \
  dynamic_bottleneck_round.agents.test_liu_rel_agent \
  dynamic_bottleneck_round.agents.test_rl_fallback \
  dynamic_bottleneck_round.agents.test_independent_rl_agent \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration -q
```

- [ ] **Step 2: Run unaffected single-bottleneck regressions**

```bash
/opt/anaconda3/envs/otree_env/bin/python -m unittest \
  single_bottleneck.tests \
  single_bottleneck.agents.test_deepseek_shadow_agent \
  single_bottleneck.agents.test_persona_integration -q
```

- [ ] **Step 3: Run full demo and static checks**

Run `otree test dynamic_bottleneck_round_demo 5`, `py_compile`, and `git diff --check`. Confirm no diff in `rl_fallback.py`, templates, surveys, accident generation, queue, cost, or payment logic.

- [ ] **Step 4: Request independent code review**

Ask the reviewer to compare the implementation against the handoff specification, with special attention to information leakage, weighted statistics, interpolation boundaries, stable randomness, state isolation, and frozen production parameters. Resolve all Critical and Important findings.

- [ ] **Step 5: Final commit and handoff**

```bash
git add dynamic_bottleneck_round settings.py docs/superpowers/plans/2026-09-10-accident-bottleneck-liu-rel-agent.md
git commit -m "test: verify Liu REL accident integration"
```

Report changed files, exact test counts, validation commands, and the unresolved requirement that formal `rel_lambda`, `rel_eta`, and `rel_capacity_bandwidth` still require independent Pilot calibration before setting `rel_parameters_frozen=1`.
