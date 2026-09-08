# Accident-Risk Dynamic Bottleneck Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rejected integer/Markov backend inside `dynamic_bottleneck_round` with the approved iid accident-risk, I0/I1/I2, fixed-20-actor, continuous-service backend while preserving the current participant-page structure.

**Architecture:** Put the new stochastic-capacity domain logic in a focused pure-Python module and let the existing oTree app orchestrate it. Generate or load one immutable 60-round accident sequence per Session, pass every decision-maker through one public-information projection, and settle all actors with a float-capacity batch queue. Keep synchronization, dropout recovery, LLM prefetch, RL state storage, page classes, and templates in place.

**Tech Stack:** Python 3, oTree, standard-library `dataclasses`, `random`, `json`, `unittest`; existing DeepSeek and local RL adapters.

---

## File map

- Create `dynamic_bottleneck_round/accident_capacity.py`: accident configuration, sequence generation/loading, record validation, and information projection.
- Create `dynamic_bottleneck_round/test_accident_capacity.py`: pure unit tests for the new domain module.
- Create `dynamic_bottleneck_round/generate_accident_sequence_bank.py`: reproducible S01–S05 bank generator and validator.
- Replace `dynamic_bottleneck_round/capacity_sequence_bank.json`: frozen 60-round accident records and metadata.
- Modify `dynamic_bottleneck_round/__init__.py`: oTree fields, Session validation, lifecycle integration, float queueing, cost settlement, Agent contexts, snapshots, and export.
- Modify `dynamic_bottleneck_round/tests.py`: app-level contracts for rounds, composition, queueing, lifecycle, costs, export, and settings.
- Modify `dynamic_bottleneck_round/agents/rl_fallback.py`: accept float capacity scenarios and iid accident beliefs instead of integer Markov transitions.
- Modify `dynamic_bottleneck_round/agents/test_rl_fallback.py`: float-capacity and information-condition policy tests.
- Modify `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`: common-context and hidden-information regression tests.
- Modify `settings.py`: replace rejected dynamic-capacity settings with accident-risk settings.
- Do not modify participant templates or `dynamic_bottleneck_survey` in this implementation.

### Task 1: Introduce the pure accident-risk capacity domain

**Files:**
- Create: `dynamic_bottleneck_round/accident_capacity.py`
- Create: `dynamic_bottleneck_round/test_accident_capacity.py`

- [ ] **Step 1: Write failing configuration and generation tests**

Add tests that define the public API before production code exists:

```python
import unittest

from dynamic_bottleneck_round.accident_capacity import (
    INFO_I0,
    INFO_I1,
    INFO_I2,
    AccidentRiskConfigError,
    generate_accident_sequence,
    parse_accident_risk_config,
)


class AccidentRiskConfigTests(unittest.TestCase):
    def test_parses_approved_default_parameters(self):
        config = parse_accident_risk_config({})
        self.assertEqual(config.normal_capacity, 4.0)
        self.assertEqual(config.incident_probability, 0.20)
        self.assertEqual(config.loss_alpha, 6.83057)
        self.assertEqual(config.loss_beta, 4.05907)
        self.assertEqual(config.information_condition, INFO_I0)

    def test_rejects_invalid_information_condition(self):
        with self.assertRaisesRegex(AccidentRiskConfigError, 'I0、I1 或 I2'):
            parse_accident_risk_config({'accident_information_condition': 'I3'})

    def test_seeded_sequence_is_reproducible_and_uses_float_capacity(self):
        config = parse_accident_risk_config({
            'accident_sequence_seed': 2026090801,
            'accident_information_condition': INFO_I2,
        })
        left = generate_accident_sequence(config, rounds=60, sequence_id='auto')
        right = generate_accident_sequence(config, rounds=60, sequence_id='auto')
        self.assertEqual(left, right)
        self.assertEqual(len(left), 60)
        for index, record in enumerate(left, start=1):
            self.assertEqual(record['formal_round_number'], index)
            self.assertGreater(record['actual_capacity'], 0)
            if record['incident_occurred']:
                self.assertGreater(record['capacity_loss_ratio'], 0)
                self.assertLess(record['capacity_loss_ratio'], 1)
                self.assertAlmostEqual(
                    record['actual_capacity'],
                    4 * (1 - record['capacity_loss_ratio']),
                    places=10,
                )
            else:
                self.assertEqual(record['capacity_loss_ratio'], 0.0)
                self.assertEqual(record['actual_capacity'], 4.0)
```

- [ ] **Step 2: Run the tests and verify the import fails**

Run: `python -m unittest dynamic_bottleneck_round.test_accident_capacity -v`

Expected: `ERROR` with `ModuleNotFoundError: No module named 'dynamic_bottleneck_round.accident_capacity'`.

- [ ] **Step 3: Implement the minimal configuration and generator**

Create a frozen configuration dataclass with these exact fields and defaults:

```python
@dataclass(frozen=True)
class AccidentRiskConfig:
    normal_capacity: float = 4.0
    incident_probability: float = 0.20
    loss_alpha: float = 6.83057
    loss_beta: float = 4.05907
    seed: int = 2026090801
    information_condition: str = 'I0'
```

Implement `parse_accident_risk_config(session_config)` so booleans are rejected as numbers, all numeric values are finite, normal capacity and Beta shapes are positive, probability lies in `[0, 1]`, and information condition is normalized to uppercase and restricted to I0/I1/I2.

Implement `generate_accident_sequence(config, *, rounds, sequence_id)` with one local `random.Random(config.seed)`. Call `rng.random()` once per round for the Bernoulli event and call `rng.betavariate(alpha, beta)` only when the event occurs. Serialize ratios and capacity with `round(value, 12)` and include `formal_round_number`, `incident_occurred`, `capacity_loss_ratio`, `remaining_capacity_ratio`, `actual_capacity`, `sequence_id`, and `sequence_seed`.

- [ ] **Step 4: Run the pure tests and verify green**

Run: `python -m unittest dynamic_bottleneck_round.test_accident_capacity -v`

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit the isolated domain module**

```bash
git add dynamic_bottleneck_round/accident_capacity.py dynamic_bottleneck_round/test_accident_capacity.py
git commit -m "feat: add accident-risk capacity domain"
```

### Task 2: Replace the old sequence bank with frozen accident sequences

**Files:**
- Create: `dynamic_bottleneck_round/generate_accident_sequence_bank.py`
- Modify: `dynamic_bottleneck_round/capacity_sequence_bank.json`
- Modify: `dynamic_bottleneck_round/accident_capacity.py`
- Modify: `dynamic_bottleneck_round/test_accident_capacity.py`

- [ ] **Step 1: Write failing bank validation tests**

Add tests asserting that `load_accident_sequence_bank()` returns exactly S01–S05, each record has exactly 60 ordered rounds, every record satisfies the capacity identity, and duplicate IDs, missing rounds, non-positive capacity, or inconsistent ratios raise `AccidentRiskConfigError`.

Use a temporary JSON path in malformed-bank tests and expose `load_accident_sequence_bank(path=None)` so tests never rewrite the checked-in bank.

- [ ] **Step 2: Run the focused tests and verify the missing API failure**

Run: `python -m unittest dynamic_bottleneck_round.test_accident_capacity.AccidentSequenceBankTests -v`

Expected: `ImportError` for `load_accident_sequence_bank` or assertion failures against the old Markov bank schema.

- [ ] **Step 3: Implement bank loading and validation**

The checked-in JSON root must contain:

```json
{
  "version": 2,
  "mechanism": "iid_accident_capacity_loss_beta",
  "formal_rounds": 60,
  "normal_capacity": 4.0,
  "incident_probability": 0.2,
  "loss_distribution": {"name": "beta", "alpha": 6.83057, "beta": 4.05907},
  "sequences": []
}
```

Each sequence record contains `id`, `generation_seed`, `incident_rounds`, `mean_actual_capacity`, and a `rounds` array holding the exact seven fields from Task 1. Validation must recalculate `remaining_capacity_ratio` and `actual_capacity` from the stored loss ratio with `math.isclose(..., rel_tol=0, abs_tol=1e-9)`.

- [ ] **Step 4: Add a deterministic bank generator**

The generator imports the Task 1 domain functions, uses seeds `2026090801` through `2026090805`, maps them to S01–S05, and writes UTF-8 JSON with `ensure_ascii=False`, `indent=2`, and a trailing newline. It must refuse a bank where any sequence has zero incident rounds or where two sequences are identical.

Run: `python dynamic_bottleneck_round/generate_accident_sequence_bank.py --output /tmp/accident_capacity_sequence_bank.json`

Expected: writes a version-2 bank containing five validated 60-round sequences.

- [ ] **Step 5: Replace and verify the checked-in bank**

Use the generator's deterministic output to replace `dynamic_bottleneck_round/capacity_sequence_bank.json`, then run:

`python -m unittest dynamic_bottleneck_round.test_accident_capacity -v`

Expected: all pure domain and bank tests pass; no `manual_sequence_spec`, `phased_markov`, transition matrix, or integer capacity list remains in the JSON.

- [ ] **Step 6: Commit the frozen sequence bank**

```bash
git add dynamic_bottleneck_round/accident_capacity.py dynamic_bottleneck_round/test_accident_capacity.py dynamic_bottleneck_round/generate_accident_sequence_bank.py dynamic_bottleneck_round/capacity_sequence_bank.json
git commit -m "feat: freeze accident-risk sequence bank"
```

### Task 3: Replace settings, rounds, action space, and production composition rules

**Files:**
- Modify: `settings.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing settings and constants tests**

Add assertions that both dynamic Session configs contain the new accident keys and no rejected capacity keys. Check the sequence default separately: production uses S01 and demo uses `auto`.

```python
self.assertEqual(config['accident_normal_capacity'], 4.0)
self.assertEqual(config['accident_probability'], 0.20)
self.assertEqual(config['accident_loss_alpha'], 6.83057)
self.assertEqual(config['accident_loss_beta'], 4.05907)
self.assertEqual(config['accident_information_condition'], 'I0')
self.assertEqual(prod_config['dynamic_capacity_sequence_preset'], 'S01')
self.assertEqual(demo_config['dynamic_capacity_sequence_preset'], 'auto')
self.assertNotIn('dynamic_capacity_draw_mode', config)
self.assertNotIn('dynamic_capacity_transition_matrix', config)
```

Add tests for `C.WARMUP_ROUNDS == 5`, `C.FORMAL_ROUNDS == 60`, `C.NUM_ROUNDS == 65`, and a fixed schedule with 16 slots from 07:46 through 08:01.

Add `validate_formal_actor_composition(session, matrix)` tests for exactly 20/0/0 and 16/2/2, and rejection of 20 humans plus agents, 16 humans plus only one Agent type, per-group variation, or any effective total other than20. Add one demo test proving smaller groups remain allowed.

- [ ] **Step 2: Run focused tests and verify expected old-contract failures**

Run: `python -m unittest dynamic_bottleneck_round.tests.SettingsContractTests dynamic_bottleneck_round.tests.DynamicWarmupTests dynamic_bottleneck_round.tests.DynamicActorCompositionTests -v`

Expected: failures showing old 2-round warmup, 21-slot schedule, old settings, and missing formal composition validator.

- [ ] **Step 3: Replace the settings contract**

In `DYNAMIC_BOTTLENECK_ROUND_COMMON`, remove `dynamic_capacity_values`, probabilities, draw mode, random rounds, transition matrix, manual sequence, reveal timing, auto schedule expansion, reward treatment, and coarse-toll configuration. Add the five accident parameters and keep Agent runtime/API settings. Set `dynamic_capacity_sequence_preset='S01'` on the production config and `dynamic_capacity_sequence_preset='auto'` on the demo config.

Update dynamic Session descriptions to say “5轮练习 + 60轮独立事故风险正式实验.” Keep the existing prod and demo config names so access control and later survey routing are not changed in this task.

- [ ] **Step 4: Implement constants, fixed schedule, and composition validation**

Set:

```python
WARMUP_ROUNDS = 5
FORMAL_ROUNDS = 60
NUM_DEPARTURE_SLOTS = 16
FIRST_DEPARTURE_MINUTE = 7 * 60 + 46
FIXED_TRAVEL_TIME_COST = 0
QUEUE_COST_PER_MINUTE = 2
EARLY_COST_PER_MINUTE = 1
LATE_COST_PER_MINUTE = 5
```

Make `static_departure_schedule()` return exactly 16 one-minute slots. Remove use of `build_dynamic_departure_schedule()` from Session creation. Implement strict production composition validation using the existing `api_agent_count_per_group()` and `rl_agent_count_per_group()` accessors, but disallow `group_agent_spec` in production.

- [ ] **Step 5: Run the focused tests and verify green**

Run: `python -m unittest dynamic_bottleneck_round.tests.SettingsContractTests dynamic_bottleneck_round.tests.DynamicWarmupTests dynamic_bottleneck_round.tests.DynamicActorCompositionTests -v`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the experiment contract**

```bash
git add settings.py dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/tests.py
git commit -m "feat: enforce accident experiment contract"
```

### Task 4: Integrate frozen accident records into the oTree lifecycle

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing lifecycle tests**

Cover these behaviors:

- `initialize_group_capacity_sequences()` stores one identical 60-record sequence for all groups;
- prod rejects `auto`, while demo accepts deterministic `auto`;
- selecting S01 loads the exact bank records and ignores legacy integer/Markov keys;
- all five practice rounds use capacity4, loss0, no incident, and do not consume formal records;
- formal round1 reads record index0 and formal round60 reads index59;
- `Group.dynamic_capacity` and `Player.dynamic_capacity` accept non-integer values;
- each Player receives accident fields and sequence metadata.

- [ ] **Step 2: Run lifecycle tests and verify failures**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicAccidentLifecycleTests -v`

Expected: failures because current lifecycle still builds integer capacity records and model fields are integer-only.

- [ ] **Step 3: Replace oTree capacity fields and sequence initialization**

Change Group and Player `dynamic_capacity` fields to `models.FloatField`. Add Boolean/Float/String/Integer fields for incident state, loss ratio, remaining ratio, information condition, sequence ID, and sequence seed. Store the frozen sequence once in `session.vars`, then copy the current record onto Group and Player at round initialization.

Practice records must be explicit synthetic records with `sequence_id='warmup'`, `incident_occurred=False`, loss0, remaining ratio1, capacity4, and the configured information condition.

- [ ] **Step 4: Remove the old capacity engine from the production module**

Delete `DynamicCapacityConfig`, old draw-mode constants, `_balanced_counts`, Markov generation, old manual-sequence parsing, dynamic schedule expansion, and capacity-transition descriptions after every caller has moved to `AccidentRiskConfig`. Retain `dynamic_capacity_sequence_preset` only as the admin/UI field naming the accident sequence.

- [ ] **Step 5: Run lifecycle and pure tests**

Run: `python -m unittest dynamic_bottleneck_round.test_accident_capacity dynamic_bottleneck_round.tests.DynamicAccidentLifecycleTests -v`

Expected: all selected tests pass.

- [ ] **Step 6: Commit lifecycle integration**

```bash
git add dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/tests.py
git commit -m "feat: apply accident sequences to dynamic rounds"
```

### Task 5: Enforce I0/I1/I2 information equality for Human, LLM, and RL

**Files:**
- Modify: `dynamic_bottleneck_round/accident_capacity.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/agents/rl_fallback.py`
- Modify: `dynamic_bottleneck_round/agents/test_rl_fallback.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing information-projection tests**

Define an `accident_public_context(config, record, *, after_decision=False, warmup=False)` contract and assert exact field sets:

```python
base = {
    'information_condition', 'normal_capacity', 'incident_probability',
    'loss_distribution', 'expected_incident_capacity',
    'expected_unconditional_capacity', 'capacity_revealed',
}
self.assertEqual(set(i0), base)
self.assertEqual(set(i1), base | {'incident_occurred'})
self.assertEqual(set(i2), base | {'incident_occurred', 'actual_capacity'})
self.assertNotIn('capacity_loss_ratio', i2)
```

For `after_decision=True`, assert all conditions receive `incident_occurred`, `capacity_loss_ratio`, `remaining_capacity_ratio`, and `actual_capacity`. For warmup, assert only the known normal capacity is exposed and the context is marked `is_warmup=True`.

In Agent integration tests, compare `Decision.vars_for_template(player)` public accident fields with `AgentChoiceSet.capacity_context`; assert equality for every allowed field and absence of future sequence records, random seed, hidden loss, and current human choices.

- [ ] **Step 2: Run the focused tests and verify leakage/contract failures**

Run: `python -m unittest dynamic_bottleneck_round.test_accident_capacity.AccidentInformationTests dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentInformationParityTests -v`

Expected: missing projection API and failures because existing contexts expose integer capacity states/reveal timing.

- [ ] **Step 3: Implement the single projection path**

Compute the two published means from the approved parameters:

```python
expected_loss = loss_alpha / (loss_alpha + loss_beta)
expected_incident_capacity = normal_capacity * (1 - expected_loss)
expected_unconditional_capacity = (
    (1 - incident_probability) * normal_capacity
    + incident_probability * expected_incident_capacity
)
```

Use the projection in `Decision.vars_for_template()` and `api_agent_choice_set_for_group()`. Continue returning legacy template keys such as `capacity_states` and `capacity_reveal_timing` only as presentation-compatible derived values; do not let those compatibility keys reintroduce hidden actual capacity.

- [ ] **Step 4: Replace RL integer/Markov beliefs with float iid accident scenarios**

Store `capacity_scenarios` as float-valued records rather than integer `capacity_values`. Use a normal scenario at capacity4 and an accident-mean scenario at `4 × beta/(alpha+beta)` with probabilities0.8/0.2. I1 collapses to normal or accident-mean based on the published event; I2 uses the exact published float capacity. Remove transition-count learning because formal rounds are iid; keep per-state Q values, anonymous previous-round feedback, cost reward, persona risk adjustment, and independent per-Agent state.

Update `_estimated_cost()` to use `float(capacity)` and the same continuous batch-delay helper semantics as the app.

- [ ] **Step 5: Run Human/Agent parity and RL tests**

Run: `python -m unittest dynamic_bottleneck_round.test_accident_capacity.AccidentInformationTests dynamic_bottleneck_round.agents.test_rl_fallback dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentInformationParityTests -v`

Expected: all selected tests pass; no Agent context contains the hidden fields forbidden by its information condition.

- [ ] **Step 6: Commit information isolation**

```bash
git add dynamic_bottleneck_round/accident_capacity.py dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/agents/rl_fallback.py dynamic_bottleneck_round/agents/test_rl_fallback.py dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py
git commit -m "feat: enforce accident information parity"
```

### Task 6: Replace integer batches and old costs with continuous settlement

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing pure queue and cost tests**

Add exact examples:

```python
self.assertAlmostEqual(
    service_batch_wait_minutes(
        departure_minute=474,
        first_service_start_minute=474,
        load=5,
        capacity=1.5,
    ),
    5 / 1.5 - 1,
)
self.assertAlmostEqual(service_batch_clear_minute(474, 5, 1.5), 474 + 5 / 1.5)
self.assertEqual(
    calculate_cost_components(queue_delay=2, early_minutes=3, late_minutes=4),
    {'fixed_cost': 0, 'queue_cost': 4, 'early_cost': 3,
     'late_cost': 20, 'toll_cost': 0, 'total_cost': 27},
)
```

Add a group settlement case with two departure batches to prove the unrounded first batch clear time feeds the second batch inherited wait. Add a same-time case proving every actor receives the common maximum batch wait.

- [ ] **Step 2: Run the queue tests and verify old integer behavior fails**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicContinuousQueueTests dynamic_bottleneck_round.tests.DynamicCostTests -v`

Expected: failures or type errors from integer `ceil`/division and old fixed/toll/late-cost behavior.

- [ ] **Step 3: Implement float settlement**

Validate finite positive capacity and non-negative integer load. Return unrounded float values from `service_batch_wait_minutes()` and `service_batch_clear_minute()`. In `_set_results_locked()`, never round `next_available_minute`; format only values assigned to display labels or exported serialization.

Remove reward and toll from the dynamic app result calculation. Keep compatibility fields at numeric zero. Calculate formal payoff as `max(0, 140 - total_cost)` and practice payoff as0.

- [ ] **Step 4: Run queue, cost, and result tests**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicContinuousQueueTests dynamic_bottleneck_round.tests.DynamicCostTests dynamic_bottleneck_round.tests.DynamicResultCalculationTests -v`

Expected: all selected tests pass.

- [ ] **Step 5: Commit continuous settlement**

```bash
git add dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/tests.py
git commit -m "feat: settle float-capacity bottleneck rounds"
```

### Task 7: Extend snapshots, records, and exports for the new experiment

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing snapshot and export tests**

Assert public post-decision snapshots contain accident event, loss ratio, remaining ratio, actual capacity, information condition, sequence ID, and aggregate outcomes, but no identity or Agent type. Assert Human, LLM, and RL export rows all contain the new columns and format loss/capacity fields to six decimal places without changing the values used in calculations.

Assert `dynamic_capacity_draw_mode`, transition probability, previous capacity, toll calibration, reward treatment, and Markov fields are absent from the new custom export header.

- [ ] **Step 2: Run export tests and verify missing/new stale columns**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicAccidentExportTests dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicPublicFeedbackTests -v`

Expected: failures because current snapshot/export still reflects integer capacity and old treatment metadata.

- [ ] **Step 3: Replace export and snapshot schemas**

Use one `accident_record_values(group_or_player)` helper to avoid mismatched field names between Human and Agent rows. Include `actor_composition` and the combined treatment label (`H-I0`, `HA-I2`, etc.). Preserve Agent audit, dropout, departure, queue, cost, and payoff columns.

Post-decision snapshots must be the only historical environment record passed to Agents. Save one deep-copied snapshot per completed formal round and keep practice history invisible to formal round1.

- [ ] **Step 4: Run snapshot/export tests**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicAccidentExportTests dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicPublicFeedbackTests -v`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the research data contract**

```bash
git add dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/tests.py dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py
git commit -m "feat: export accident treatment records"
```

### Task 8: Remove stale backend paths and run full regression

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `settings.py`

- [ ] **Step 1: Add negative legacy-contract tests**

Add tests reading production source/settings/bank and asserting none of these remain in the active backend contract: `phased_markov`, `balanced_shuffle`, `dynamic_capacity_transition_matrix`, `dynamic_capacity_random_rounds`, `manual_sequence_spec`, integer-only capacity conversion, dynamic schedule expansion, or capacity-transition learning.

- [ ] **Step 2: Run negative tests and verify they identify remaining stale paths**

Run: `python -m unittest dynamic_bottleneck_round.tests.DynamicLegacyBackendRemovalTests -v`

Expected: failures naming every still-present legacy path.

- [ ] **Step 3: Remove only the stale dynamic-backend code**

Delete now-unused helpers, imports, constants, settings fields, and test fixtures belonging to the rejected design. Do not edit participant templates, survey files, `single_bottleneck`, synchronization, dropout recovery, or API transport code.

- [ ] **Step 4: Run static checks**

```bash
python -m py_compile dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/accident_capacity.py dynamic_bottleneck_round/generate_accident_sequence_bank.py dynamic_bottleneck_round/agents/deepseek_agent.py dynamic_bottleneck_round/agents/rl_fallback.py settings.py
git diff --check -- dynamic_bottleneck_round settings.py
rg -n 'phased_markov|balanced_shuffle|dynamic_capacity_transition_matrix|manual_sequence_spec' dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/accident_capacity.py dynamic_bottleneck_round/capacity_sequence_bank.json settings.py
```

Expected: compilation and diff checks succeed; the final search has no production-code/settings/bank match. Historical design documents are outside the search scope.

- [ ] **Step 5: Run all dynamic app unit tests**

Run: `python -m unittest dynamic_bottleneck_round.test_accident_capacity dynamic_bottleneck_round.tests dynamic_bottleneck_round.agents.test_rl_fallback dynamic_bottleneck_round.agents.test_independent_rl_agent dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v`

Expected: all tests pass with no warnings or unexpected skips.

- [ ] **Step 6: Run oTree bot tests in fresh temporary databases**

```bash
OTREE_DATABASE_URL=sqlite:////tmp/accident_dynamic_demo.sqlite3 OTREE_ADMIN_PASSWORD=devpass otree test dynamic_bottleneck_round_demo 5
OTREE_DATABASE_URL=sqlite:////tmp/accident_dynamic_prod_h_i0.sqlite3 OTREE_ADMIN_PASSWORD=devpass otree test dynamic_bottleneck_round_prod 20
```

Expected: the five-participant demo and valid20-Human production Session each complete all65 internal rounds. The numeric CLI arguments are participant counts; the bot traverses the configured65 rounds automatically.

- [ ] **Step 7: Run unaffected app regression tests**

Run: `python -m unittest single_bottleneck.tests single_bottleneck.agents.test_deepseek_shadow_agent single_bottleneck.agents.test_persona_integration -v`

Expected: all existing single-bottleneck tests pass unchanged.

- [ ] **Step 8: Inspect the final scope and commit**

```bash
git status --short
git diff --stat HEAD
git diff --check
git add dynamic_bottleneck_round settings.py
git commit -m "refactor: replace dynamic bottleneck backend"
```

Expected: participant templates, `dynamic_bottleneck_survey`, the analysis platform, and unrelated working-tree files are absent from the final implementation diff.

### Task 9: Request code review and close findings

**Files:**
- Review all files changed by Tasks 1–8.

- [ ] **Step 1: Record the review range**

Run `git rev-parse 87facb8` for the design baseline and `git rev-parse HEAD` for the implementation head.

- [ ] **Step 2: Request an independent code review**

Provide the reviewer with the approved design path, this implementation plan, the base/head SHAs, and these priorities: hidden-information leakage, float queue correctness, Session composition validation, sequence reproducibility, Agent parity, stale Markov paths, and unintended template/survey changes.

- [ ] **Step 3: Fix Critical and Important findings test-first**

For every valid finding, add or tighten a failing regression test, observe the expected failure, apply the smallest fix, and rerun the focused and full dynamic suites.

- [ ] **Step 4: Produce final verification evidence**

Run the static checks, full dynamic unit suite, oTree bot suite, and unaffected `single_bottleneck` regression once more. Record exact pass counts and any environment limitation in the final handoff.
