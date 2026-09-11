# Uniform Stochastic Capacity Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the accident/Beta-loss backend with frozen randomized-stratified `U(1.33, 4.00)` capacity sequences while preserving the approved 2 × 2 Human/Agent design, queueing model, costs, and 5 + 30 round structure.

**Architecture:** Add a standalone pure `stochastic_capacity.py` module as the sole authority for configuration, sequence generation/loading, capacity levels, and public I0/I1 contexts. The oTree app stores one frozen sequence per Session and exposes the same context contract to Human, LLM, and RL consumers; I1 Liu-REL uses a fixed Gaussian capacity kernel, while I0 never receives the current capacity. Existing accident artifacts remain only as historical files and are not imported by the new runtime.

**Tech Stack:** Python 3 in conda environment `otree_env`, oTree, unittest, oTree templates, JSON frozen sequence bank, JavaScript Session creation controls.

---

### Task 1: Pure stochastic-capacity domain module

**Files:**
- Create: `dynamic_bottleneck_round/stochastic_capacity.py`
- Create: `dynamic_bottleneck_round/test_stochastic_capacity.py`
- Create: `dynamic_bottleneck_round/uniform_capacity_sequence_bank.json`
- Create: `dynamic_bottleneck_round/generate_uniform_capacity_sequence_bank.py`

- [ ] **Step 1: Write failing configuration and generator tests**

Add tests for strict `uniform`, exact 1.33/4.00 bounds, I0/I1, legacy-field rejection, deterministic randomized-stratified generation, two-decimal capacities, all-strata coverage, and derived low/medium/high labels.

- [ ] **Step 2: Verify RED**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.test_stochastic_capacity -v
```

Expected: import failure because `stochastic_capacity.py` does not exist.

- [ ] **Step 3: Implement the pure domain API**

Provide:

```python
@dataclass(frozen=True)
class StochasticCapacityConfig:
    distribution: str = 'uniform'
    capacity_min: float = 1.33
    capacity_max: float = 4.00
    seed: int = 2026091101
    information_condition: str = 'I0'

def parse_stochastic_capacity_config(session_config): ...
def capacity_level(actual_capacity): ...
def generate_stratified_capacity_sequence(config, *, rounds, sequence_id): ...
def stochastic_capacity_public_context(config, record, *, after_decision=False, warmup=False): ...
def load_uniform_capacity_sequence_bank(path=None): ...
```

The generator samples one value from each equal-probability stratum, rounds to two decimals, then shuffles with the same seeded RNG. The loader validates the mechanism, bounds, round numbers, seeds, two-decimal values, labels, and exact S01–S05 set.

- [ ] **Step 4: Verify GREEN and generate the bank**

Run the focused tests, generate five frozen 30-round sequences with seeds 2026091101–2026091105, then rerun the tests.

- [ ] **Step 5: Commit the domain layer**

Stage only the four files above and commit `feat: add uniform stochastic capacity domain`.

### Task 2: Session lifecycle and treatment integration

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing lifecycle tests**

Cover one frozen Session sequence shared across groups, exact two-decimal settlement capacity, five public fixed warmup capacities, formal S01–S05 enforcement, group-specific I0/I1, and explicit rejection of old accident config fields.

- [ ] **Step 2: Verify RED**

Run the new lifecycle test classes and confirm failures reference missing stochastic-capacity behavior.

- [ ] **Step 3: Switch runtime ownership**

Replace accident imports/helpers/session keys with stochastic equivalents. Keep `dynamic_capacity` only as the queue engine’s numeric field, add capacity level/sequence metadata, and stop reading or writing incident/loss fields. `capacity_sequence_for_session()` loads S01–S05 for formal Sessions and allows deterministic auto generation only for demo Sessions.

- [ ] **Step 4: Verify GREEN**

Run `dynamic_bottleneck_round.tests` and fix only failures caused by the new approved contract.

- [ ] **Step 5: Commit lifecycle changes**

Commit `feat: use uniform capacity sequences in dynamic bottleneck`.

### Task 3: Human information and experiment copy

**Files:**
- Modify: `dynamic_bottleneck_round/Introduction.html`
- Modify: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Modify: `dynamic_bottleneck_round/WarmupStart.html`
- Modify: `dynamic_bottleneck_round/FormalStart.html`
- Modify: `dynamic_bottleneck_round/Decision.html`
- Modify: `dynamic_bottleneck_round/Results.html`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing context/template tests**

Assert I0 contains distribution/bounds but no current capacity, capacity level, color, or icon hint; I1 contains exactly the two-decimal settlement capacity and deterministic level; both receive identical realized capacity feedback after settlement; all accident wording is absent.

- [ ] **Step 2: Verify RED**

Run focused presentation and template tests and confirm the old incident wording causes the expected failures.

- [ ] **Step 3: Implement one public context path**

Use `stochastic_capacity_public_context()` for page contexts. I0 says the rate will be revealed after all choices; I1 shows `本轮瓶颈服务率：X.XX 主体/分钟` and a low/medium/high label. Warmup remains explicitly marked and publicly shows its fixed capacity.

- [ ] **Step 4: Verify GREEN**

Run presentation/template tests and inspect rendered values for two-decimal formatting.

- [ ] **Step 5: Commit Human-facing changes**

Commit `feat: present I0 and I1 uniform capacity information`.

### Task 4: LLM, fallback, and Liu-REL parity

**Files:**
- Modify: `dynamic_bottleneck_round/agents/deepseek_agent.py`
- Modify: `dynamic_bottleneck_round/agents/rl_fallback.py`
- Modify: `dynamic_bottleneck_round/agents/independent_rl_agent.py`
- Modify: `dynamic_bottleneck_round/agents/liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_round/agents/test_rl_fallback.py`
- Modify: `dynamic_bottleneck_round/agents/test_independent_rl_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: Write failing parity and kernel tests**

Assert I0 Agent inputs omit capacity and level, I1 inputs match Human values, no future sequence is exposed, Gaussian weights equal `exp(-((current-historical)^2)/(2*h^2))`, `h=(4.00-1.33)/sqrt(12)`, sparse I1 falls back to all I0 history, and the new policy is `dynamic_liu_rel_uniform_capacity_v1`.

- [ ] **Step 2: Verify RED**

Run the four Agent test modules and confirm failures originate from incident-conditioned APIs/versioning.

- [ ] **Step 3: Implement minimal Agent changes**

Remove incident booleans from experience/state and prompt inputs. Pass current capacity only when the public I1 context contains it. Weight I1 history by the frozen Gaussian kernel, preserve shared lambda/eta/exploration rules, and keep each Agent’s state/random stream isolated.

- [ ] **Step 4: Verify GREEN**

Run all Agent tests, including API-failure fallback information-boundary tests.

- [ ] **Step 5: Commit Agent changes**

Commit `feat: condition agents on public stochastic capacity`.

### Task 5: Session configuration, admin controls, reports, survey, and export

**Files:**
- Modify: `settings.py`
- Modify: `_templates/otree/includes/DynamicSessionControls.html`
- Modify: `dynamic_bottleneck_round/admin_report.html`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_survey/__init__.py`
- Modify: `dynamic_bottleneck_survey/*.html`
- Modify: `dynamic_bottleneck_survey/tests.py`

- [ ] **Step 1: Write failing contract tests**

Require one homepage scenario, in-Session H-I0/H-I1/HA-I0/HA-I1/custom choices, new capacity config fields, S01–S05 only for formal creation, new export columns, no accident columns/wording, and unchanged 30/10+10+10 actor counts.

- [ ] **Step 2: Verify RED**

Run settings, admin-template, export, and survey test classes and observe failures against the old accident contract.

- [ ] **Step 3: Update all configuration and audit surfaces**

Replace accident config fields with `capacity_distribution`, `capacity_min`, `capacity_max`, `capacity_sequence_id`, `capacity_sequence_seed`, and `capacity_information_condition`. Keep one formal scenario and configure treatment/groups inside its creation form. Export `capacity_level` and `capacity_revealed_before_decision`, remove accident/loss columns, and make the admin report and survey capacity-information based.

- [ ] **Step 4: Verify GREEN**

Run focused backend, template, export, and survey tests.

- [ ] **Step 5: Commit integration surfaces**

Commit `feat: expose uniform capacity experiment configuration`.

### Task 6: Regression and full-flow verification

**Files:**
- Modify only if a failing regression exposes a missing approved change.

- [ ] **Step 1: Run the full targeted unittest suite**

```bash
conda run -n otree_env python -m unittest \
  dynamic_bottleneck_round.test_stochastic_capacity \
  dynamic_bottleneck_round.tests \
  dynamic_bottleneck_round.agents.test_liu_rel_agent \
  dynamic_bottleneck_round.agents.test_independent_rl_agent \
  dynamic_bottleneck_round.agents.test_rl_fallback \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration \
  dynamic_bottleneck_survey.tests \
  single_bottleneck.tests -v
```

Expected: all tests pass with no warnings.

- [ ] **Step 2: Run oTree system checks and bot/full-flow coverage**

Start the app through `conda run -n otree_env`, create formal I0, I1, HA-I0, HA-I1, and four-group matched Sessions, and verify Session creation, waiting, choice, Agent completion, settlement, results, and export.

- [ ] **Step 3: Search for retired runtime concepts**

Search production settings, templates, Agent code, report, survey, and export for `accident`, `incident`, `capacity_loss_ratio`, and `remaining_capacity_ratio`. Historical handoff/spec and retired standalone files may remain, but the new runtime must not import them.

- [ ] **Step 4: Inspect the final diff and commit**

Run `git diff --check`, stage only task files, preserve unrelated user changes, and commit `feat: replace accident backend with stochastic capacity` if final integration changes remain.
