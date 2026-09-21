# Truncated Normal Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the active dynamic-bottleneck uniform capacity mechanism with a frozen, stratified truncated-normal mechanism while preserving treatment matching, information isolation, and continuous-capacity settlement.

**Architecture:** Keep distribution mathematics and sequence validation in `stochastic_capacity.py`, generate immutable S01-S05 data with a standalone standard-library script, and expose only a filtered public capacity context to pages and Agents. The main oTree app consumes the new neutral API, exports both base-normal and truncated-distribution parameters, and retains the existing settlement pipeline unchanged.

**Tech Stack:** Python 3.10 standard library (`statistics.NormalDist`, `random`, `json`, `statistics`), oTree, unittest, HTML templates, conda environment `otree_env`.

**Version-control constraint:** Do not create Git commits. The user will manage version control.

---

## File map

- Modify `dynamic_bottleneck_round/stochastic_capacity.py`: truncated-normal configuration, mathematics, generation, public context, and strict bank loader.
- Modify `dynamic_bottleneck_round/test_stochastic_capacity.py`: unit and corruption tests for the new contract.
- Create `dynamic_bottleneck_round/generate_truncated_normal_capacity_sequence_bank.py`: reproducible bank builder.
- Create `dynamic_bottleneck_round/truncated_normal_capacity_sequence_bank.json`: frozen S01-S05 records.
- Retain `dynamic_bottleneck_round/uniform_capacity_sequence_bank.json`: historical artifact only.
- Modify `settings.py`: active distribution parameters and Liu-REL policy version.
- Modify `dynamic_bottleneck_round/__init__.py`: neutral imports, v3 cache, context, snapshots, exports, and display values.
- Modify `dynamic_bottleneck_round/agents/liu_rel_agent.py`: policy semantic version and truncated-distribution kernel bandwidth input.
- Modify Agent tests under `dynamic_bottleneck_round/agents/`: policy/context contract.
- Modify participant templates `Introduction.html`, `ComprehensionCheck.html`, `FormalStart.html`, `Decision.html`, `Results.html` and `admin_report.html`: truncated-normal explanations and `人/分钟` units.
- Modify `dynamic_bottleneck_round/tests.py`: integration, export, page, settlement, and Bot contracts.

### Task 1: Define truncated-normal mathematics and configuration

**Files:**
- Modify: `dynamic_bottleneck_round/test_stochastic_capacity.py`
- Modify: `dynamic_bottleneck_round/stochastic_capacity.py`

- [ ] **Step 1: Replace configuration tests with the approved contract**

Add assertions equivalent to:

```python
config = parse_stochastic_capacity_config({})
self.assertEqual(config.distribution, 'truncated_normal')
self.assertEqual(config.capacity_mu, 2.665)
self.assertEqual(config.capacity_sigma, 0.80)
self.assertAlmostEqual(config.truncated_mean, 2.665, places=12)
self.assertAlmostEqual(config.truncated_standard_deviation, 0.6371680565, places=9)
```

Test that `uniform`, altered approved parameters, booleans, `nan`, `inf`, invalid bounds, I2, and legacy accident fields raise `StochasticCapacityConfigError`.

- [ ] **Step 2: Add failing mathematical tests**

Test standard-normal CDF/inverse-CDF round trips, truncated quantiles, and levels:

```python
self.assertAlmostEqual(truncated_normal_quantile(1 / 3), 2.355, delta=0.002)
self.assertAlmostEqual(truncated_normal_quantile(2 / 3), 2.975, delta=0.002)
self.assertEqual(capacity_level(2.35), 'low')
self.assertEqual(capacity_level(2.36), 'medium')
self.assertEqual(capacity_level(2.98), 'high')
```

- [ ] **Step 3: Run focused tests and verify the expected failure**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.test_stochastic_capacity.StochasticCapacityConfigTests dynamic_bottleneck_round.test_stochastic_capacity.CapacityLevelTests -v
```

Expected: failures because truncated-normal fields and quantile helpers do not yet exist.

- [ ] **Step 4: Implement the standard-library distribution core**

Define the approved constants and immutable config:

```python
from statistics import NormalDist

CAPACITY_DISTRIBUTION = 'truncated_normal'
CAPACITY_MU = 2.665
CAPACITY_SIGMA = 0.80
CAPACITY_MIN = 1.33
CAPACITY_MAX = 4.00
SEQUENCE_MECHANISM = 'truncated_normal_stochastic_capacity_v2'
SEQUENCE_BANK_FILE = 'truncated_normal_capacity_sequence_bank.json'

@dataclass(frozen=True)
class StochasticCapacityConfig:
    distribution: str = CAPACITY_DISTRIBUTION
    capacity_mu: float = CAPACITY_MU
    capacity_sigma: float = CAPACITY_SIGMA
    capacity_min: float = CAPACITY_MIN
    capacity_max: float = CAPACITY_MAX
    seed: int = 2026091101
    information_condition: str = INFO_I0
```

Implement density/CDF helpers, the truncated mean/variance formula, and:

```python
def truncated_normal_quantile(probability, config=None):
    config = config or StochasticCapacityConfig()
    normal = NormalDist()
    lower = normal.cdf((config.capacity_min - config.capacity_mu) / config.capacity_sigma)
    upper = normal.cdf((config.capacity_max - config.capacity_mu) / config.capacity_sigma)
    return config.capacity_mu + config.capacity_sigma * normal.inv_cdf(
        lower + probability * (upper - lower)
    )
```

Derive level thresholds from quantiles rather than declaring 2.36 and 2.98 as independent constants.

- [ ] **Step 5: Run focused tests and verify they pass**

Run the command from Step 3. Expected: all selected tests pass.

### Task 2: Generate and strictly validate S01-S05

**Files:**
- Modify: `dynamic_bottleneck_round/test_stochastic_capacity.py`
- Modify: `dynamic_bottleneck_round/stochastic_capacity.py`
- Create: `dynamic_bottleneck_round/generate_truncated_normal_capacity_sequence_bank.py`
- Create: `dynamic_bottleneck_round/truncated_normal_capacity_sequence_bank.json`

- [ ] **Step 1: Write failing generation and bank-loader tests**

Require deterministic CDF strata and the new record key:

```python
left = generate_stratified_capacity_sequence(config, rounds=30, sequence_id='S01')
right = generate_stratified_capacity_sequence(config, rounds=30, sequence_id='S01')
self.assertEqual(left, right)
self.assertEqual(
    {row['quantile_stratum_index'] for row in left},
    set(range(1, 31)),
)
self.assertTrue(all(row['distribution'] == 'truncated_normal' for row in left))
```

Mutate bank version, mechanism, distribution, mu, sigma, bounds, seed, round number, stratum, capacity, derived level, mean, and population standard deviation one at a time; every mutation must fail loading.

- [ ] **Step 2: Run tests and verify failures reference the old uniform contract**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.test_stochastic_capacity.StratifiedSequenceTests dynamic_bottleneck_round.test_stochastic_capacity.TruncatedNormalSequenceBankTests -v
```

Expected: failure because the generator uses equal-width capacity strata and the new loader/file do not exist.

- [ ] **Step 3: Implement equal-probability CDF-stratum generation**

For each `i` from 1 through `rounds`, draw `u` inside `((i-1)/rounds, i/rounds)`, map it using `truncated_normal_quantile`, clamp, round to two decimals, attach `quantile_stratum_index=i`, then shuffle with the same seeded RNG. Store all approved distribution parameters in each record.

- [ ] **Step 4: Implement a strict neutral bank loader**

Expose:

```python
def load_stochastic_capacity_sequence_bank(path=None):
    ...
```

Require version 2, the v2 mechanism, exactly S01-S05, 30 rounds each, each stratum once, approved parameters, correct regenerated records for each seed, and exact derived summary metadata. Comparing each checked-in sequence to a fresh deterministic regeneration makes any altered capacity or order fail validation.

- [ ] **Step 5: Implement and run the bank generator**

Use seeds `2026091101` through `2026091105`. Serialize stable UTF-8 JSON with top-level theoretical metadata and per-sequence `mean_actual_capacity`, `population_standard_deviation`, `min_actual_capacity`, and `max_actual_capacity`.

Run:

```bash
conda run -n otree_env python dynamic_bottleneck_round/generate_truncated_normal_capacity_sequence_bank.py
```

Expected: a deterministic `truncated_normal_capacity_sequence_bank.json` containing five 30-round sequences.

- [ ] **Step 6: Run sequence tests and inspect statistics**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.test_stochastic_capacity -v
```

Expected: all tests pass; each sequence mean lies within 2.635-2.695, each contains 30 distinct strata, and S01-S05 are not identical.

### Task 3: Wire the new mechanism into Sessions and cache v3

**Files:**
- Modify: `settings.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing Session integration tests**

Assert the common config contains:

```python
self.assertEqual(common['capacity_distribution'], 'truncated_normal')
self.assertEqual(common['capacity_mu'], 2.665)
self.assertEqual(common['capacity_sigma'], 0.80)
```

Test that formal Sessions load only S01-S05 from the new bank, Demo `auto` uses truncated generation, and a cached v2 uniform record is ignored rather than reused.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.tests.DynamicSessionConfigTests dynamic_bottleneck_round.tests.DynamicCapacitySequenceTests -v
```

Expected: failures showing uniform settings, old loader imports, or the v2 cache key.

- [ ] **Step 3: Update settings and runtime imports**

Set:

```python
capacity_distribution='truncated_normal'
capacity_mu=2.665
capacity_sigma=0.80
DYNAMIC_BOTTLENECK_RL_AGENT_POLICY_VERSION = (
    'dynamic_liu_rel_truncated_normal_capacity_v2'
)
```

Replace the uniform-specific loader import/call with `load_stochastic_capacity_sequence_bank`. Change `CAPACITY_SEQUENCE_SESSION_VAR` to `dynamic_bottleneck_round_capacity_sequence_v3`. Preserve the formal `auto` rejection and Demo allowance.

- [ ] **Step 4: Run focused integration tests**

Run the command from Step 2. Expected: all selected tests pass.

### Task 4: Keep public information equal and update Liu-REL semantics

**Files:**
- Modify: `dynamic_bottleneck_round/stochastic_capacity.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/agents/liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_independent_rl_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Add failing public-context and Agent tests**

For I0 before decision, assert the presence of distribution, mu, sigma, bounds, truncated mean and truncated standard deviation, and the absence of `actual_capacity`, `capacity_level`, `quantile_stratum_index`, `sequence_id`, `sequence_seed`, and future records. For I1 assert that only current actual capacity and its derived level are added.

Assert:

```python
self.assertEqual(LIU_REL_POLICY_VERSION, 'dynamic_liu_rel_truncated_normal_capacity_v2')
self.assertAlmostEqual(CAPACITY_KERNEL_BANDWIDTH, 0.6371680565, places=9)
```

- [ ] **Step 2: Run Agent tests and verify expected failures**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.agents.test_liu_rel_agent dynamic_bottleneck_round.agents.test_independent_rl_agent dynamic_bottleneck_round.agents.test_dynamic_agent_integration -q
```

Expected: failures from the uniform policy version/bandwidth and missing context fields.

- [ ] **Step 3: Implement public context and Agent version changes**

Build all Human, LLM, and RL capacity information from `stochastic_capacity_public_context`. Change only capacity-distribution semantics; retain `rel_initial_uniform_rounds` and `liu_rel_uniform_initial` because they describe action exploration. Use the config-derived truncated standard deviation for the I1 capacity kernel.

- [ ] **Step 4: Run Agent tests and verify they pass**

Run the command from Step 2. Expected: all Agent tests pass with no I0 leakage.

### Task 5: Add complete metadata to snapshots, reports, and export

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/admin_report.html`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing export and report tests**

Extend expected export headers with:

```python
'capacity_mu',
'capacity_sigma',
'capacity_truncated_mean',
'capacity_truncated_sd',
```

Assert human and Agent rows carry identical distribution metadata and retain full-precision settlement/output values. Assert the admin report labels service rate with `人/分钟`.

- [ ] **Step 2: Run export/report tests and verify failure**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.tests.DynamicCostExportTests dynamic_bottleneck_round.tests.DynamicAdminReportTests -v
```

Expected: failures because the new fields and labels are absent.

- [ ] **Step 3: Implement metadata propagation**

Add the four fields to capacity snapshots, `EXPORT_HEADERS`, human export rows, virtual-Agent export rows, and admin data structures. Export base sigma and actual truncated standard deviation as distinct numeric columns. Do not round `actual_capacity` before settlement; only use the approved formatting at presentation/export boundaries.

- [ ] **Step 4: Run export/report tests**

Run the command from Step 2. Expected: all selected tests pass.

### Task 6: Replace participant-facing distribution text and units

**Files:**
- Modify: `dynamic_bottleneck_round/Introduction.html`
- Modify: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Modify: `dynamic_bottleneck_round/FormalStart.html`
- Modify: `dynamic_bottleneck_round/Decision.html`
- Modify: `dynamic_bottleneck_round/Results.html`
- Modify: `dynamic_bottleneck_round/admin_report.html`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] **Step 1: Write failing template-contract tests**

Assert active participant templates do not contain `均匀分布`, `区间内各等长子区间等概率`, or the old equal-width thresholds. Assert they contain `中心值`, `中心附近更常见`, and `人/分钟`. Specifically assert Results contains:

```html
{{ dynamic_capacity }} 人/分钟
```

Update comprehension Q4 expected text so I0 knows the truncated-normal distribution but not the current capacity, while I1 knows both.

- [ ] **Step 2: Run page tests and verify old wording fails them**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.tests.TemplateContractTests dynamic_bottleneck_round.tests.ComprehensionContractTests -v
```

Expected: failures identifying uniform-distribution text and the old `主体 / 分钟` Results unit.

- [ ] **Step 3: Update all active presentation text**

Use a consistent participant explanation:

```text
每轮瓶颈服务率位于 1.33-4.00 人/分钟，中心值为 2.665。
中心附近的服务率更常见，越接近上下界越少见。
```

Render three capacity levels using truncated tertiles. Keep the page explanation conceptual; do not require participants to distinguish base sigma from truncated standard deviation. Use `人/分钟` consistently for service-rate units on all participant pages and the result/admin display.

- [ ] **Step 4: Run page and comprehension tests**

Run the command from Step 2. Expected: all selected tests pass for both I0 and I1.

### Task 7: Prove settlement is unchanged for fixed capacities

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify only if a regression is found: `dynamic_bottleneck_round/__init__.py`

- [ ] **Step 1: Add parameterized continuous-capacity regression tests**

For capacities 1.50, 2.665, and 3.75, assert:

```python
expected_extra = inherited_wait + load / capacity * C.CAPACITY_WINDOW_MINUTES
self.assertAlmostEqual(actual_extra, expected_extra)
```

Build two otherwise identical groups whose capacity came from old/new distribution labels and verify equal actual capacity plus equal choices yield equal queue delay, arrival, cost, and payoff. Assert simultaneous actors receive identical results.

- [ ] **Step 2: Run settlement tests**

Run:

```bash
conda run -n otree_env python -m unittest dynamic_bottleneck_round.tests.DynamicCapacityQueueTests dynamic_bottleneck_round.tests.DynamicContinuousQueueTests dynamic_bottleneck_round.tests.DynamicCostExportTests -v
```

Expected: tests pass without production settlement changes. If a failure identifies distribution branching inside settlement, remove that branch and rerun until green.

### Task 8: Full verification and audit report

**Files:**
- Modify as needed only within files listed in this plan.

- [ ] **Step 1: Scan active code for obsolete capacity semantics**

Run:

```bash
rg -n "uniform_capacity|uniform stochastic|均匀分布|等宽区间|主体 / 分钟|主体/分钟" dynamic_bottleneck_round settings.py
```

Expected: matches are limited to retained historical filename references in tests/documentation and RL action-exploration identifiers; no active capacity-distribution or participant-page match remains.

- [ ] **Step 2: Validate all fixed sequences and print the audit table**

Run a read-only Python command that loads the bank and prints sequence ID, count, mean, population standard deviation, minimum, and maximum. Expected: five 30-round rows, approved mean range, all values inside 1.33-4.00.

- [ ] **Step 3: Run the complete dynamic app test suite**

Run:

```bash
conda run -n otree_env python -m unittest discover -s dynamic_bottleneck_round -t . -q
```

Expected: all tests pass.

- [ ] **Step 4: Run the complete oTree Bot flow**

Run:

```bash
conda run -n otree_env otree test dynamic_bottleneck_round_demo 5
```

Expected: all configured cases finish 3 warmup plus 30 formal rounds and complete the survey.

- [ ] **Step 5: Verify paired-treatment identity**

Create in-memory test Sessions for I0 and I1 referencing the same S01, retrieve all 30 formal records, and assert their `actual_capacity`, `quantile_stratum_index`, and round order are identical while their pre-decision public contexts differ only by current capacity and level.

- [ ] **Step 6: Inspect the final scoped diff**

Run:

```bash
git diff --check -- settings.py dynamic_bottleneck_round docs/superpowers/specs/2026-09-16-truncated-normal-capacity-design.md docs/superpowers/plans/2026-09-16-truncated-normal-capacity.md
git status --short
```

Expected: no whitespace errors; unrelated existing paper/survey changes remain untouched and uncommitted.

- [ ] **Step 7: Report completion without committing**

Report modified files, S01-S05 statistics, unit/Bot results, I0/I1 leakage checks, and the remaining requirement to calibrate `sigma=0.80` in a Pilot. Do not run `git commit`.
