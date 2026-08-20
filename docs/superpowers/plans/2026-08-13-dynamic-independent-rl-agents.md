# Dynamic Independent RL Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow 1-5 independently learning RL virtual participants per group to join humans and DeepSeek Agents in `dynamic_bottleneck_round` without changing `single_bottleneck`.

**Architecture:** Keep RL Agents outside the oTree `Player` model and extend the existing virtual-decision record channel to support both `deepseek_api_agent` and `rl_agent`. Each RL Agent receives a stable existing persona, keeps an independent tabular state in `participant.vars`, chooses locally before settlement, and joins the same point-queue, toll, cost, result, report, and export pipeline as every other actor. The existing `deepseek_fallback_rl` remains a fallback decision source for a DeepSeek actor and never increases actor count.

**Tech Stack:** Python 3, oTree, standard-library `unittest`, existing pure-Python tabular RL policy, oTree templates with vanilla JavaScript.

---

## File Structure

- Modify `settings.py`: environment defaults and Session config values for independent RL Agents.
- Modify `_templates/otree/CreateSession.html`: independent RL enable/count controls for the two dynamic configs.
- Modify `dynamic_bottleneck_round/agents/personas.py`: stable persona assignment for API and RL namespaces.
- Modify `dynamic_bottleneck_round/agents/rl_fallback.py`: make the existing local policy reusable through explicit policy version and decision-source parameters while preserving fallback defaults.
- Create `dynamic_bottleneck_round/agents/independent_rl_agent.py`: independent RL policy facade and constants; no oTree ORM access.
- Modify `dynamic_bottleneck_round/__init__.py`: config validation, actor counting, RL state orchestration, generic virtual-record storage, settlement, export, and report integration.
- Modify `dynamic_bottleneck_round/admin_report.html`: separate API Agent, RL Agent, and fallback metrics.
- Modify `dynamic_bottleneck_round/agents/test_rl_fallback.py`: policy parameterization regression tests.
- Create `dynamic_bottleneck_round/agents/test_independent_rl_agent.py`: isolated independent-policy tests.
- Modify `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`: config, storage, settlement, export, and admin integration tests.
- Modify `dynamic_bottleneck_round/tests.py`: oTree bot assertions for virtual actor inclusion and anonymity.

### Task 1: Add Independent RL Session Configuration

**Files:**
- Modify: `settings.py:20-45,80-115`
- Modify: `dynamic_bottleneck_round/__init__.py:55-70,450-515`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py:14-78`

- [ ] **Step 1: Write failing configuration and actor-count tests**

Add tests covering disabled mode, valid counts 1-5, invalid values, combined actor counts, and fallback non-counting:

```python
def test_dynamic_configs_default_to_independent_rl_off(self):
    configs = [
        config for config in settings.SESSION_CONFIGS
        if config['name'].startswith('dynamic_bottleneck_round')
    ]
    for config in configs:
        self.assertEqual(str(config['rl_agent_enabled']).lower(), '0')
        self.assertEqual(config['rl_agent_count_per_group'], '1')
        self.assertEqual(
            config['rl_agent_policy_version'],
            'dynamic_independent_rl_v1',
        )

def test_independent_rl_count_validation(self):
    for count in (1, 5, '3'):
        session = self.make_session('off', 1)
        session.config.update({
            'rl_agent_enabled': '1',
            'rl_agent_count_per_group': count,
        })
        self.assertEqual(app.validate_rl_agent_count(session), int(count))

    for count in (0, 6, '2.5', True):
        session = self.make_session('off', 1)
        session.config.update({
            'rl_agent_enabled': '1',
            'rl_agent_count_per_group': count,
        })
        with self.assertRaisesRegex(ValueError, '1 到 5'):
            app.validate_rl_agent_count(session)

def test_effective_actor_count_includes_each_real_actor_once(self):
    session = self.make_session('active', 2)
    session.config.update({
        'rl_fallback_enabled': '1',
        'rl_agent_enabled': '1',
        'rl_agent_count_per_group': 3,
    })
    self.assertEqual(app.effective_group_actor_count(session, 20), 25)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentConfigTests -v
```

Expected: failures because `rl_agent_enabled`, `rl_agent_count_per_group`, and `validate_rl_agent_count()` do not exist.

- [ ] **Step 3: Add settings defaults**

Add environment-backed defaults without changing the current default behavior:

```python
DYNAMIC_BOTTLENECK_RL_AGENT_ENABLED = str(
    environ.get('DYNAMIC_BOTTLENECK_RL_AGENT_ENABLED', '0') or '0'
).strip()
DYNAMIC_BOTTLENECK_RL_AGENT_COUNT = str(
    environ.get('DYNAMIC_BOTTLENECK_RL_AGENT_COUNT_PER_GROUP', '1') or '1'
).strip()
DYNAMIC_BOTTLENECK_RL_AGENT_POLICY_VERSION = 'dynamic_independent_rl_v1'
```

Add these keys to `DYNAMIC_BOTTLENECK_ROUND_COMMON`:

```python
rl_agent_enabled=DYNAMIC_BOTTLENECK_RL_AGENT_ENABLED,
rl_agent_count_per_group=DYNAMIC_BOTTLENECK_RL_AGENT_COUNT,
rl_agent_policy_version=DYNAMIC_BOTTLENECK_RL_AGENT_POLICY_VERSION,
```

- [ ] **Step 4: Implement strict independent RL validation and effective count**

Add constants and helpers in `dynamic_bottleneck_round/__init__.py`:

```python
RL_AGENT_TYPE = 'rl_agent'
RL_AGENT_COUNT_MIN = 1
RL_AGENT_COUNT_MAX = 5
RL_AGENT_COUNT_ERROR = '每组独立 RL Agent 数量必须是 1 到 5 之间的整数。'


def rl_agent_enabled(session) -> bool:
    return config_flag(session.config.get('rl_agent_enabled', 0))


def rl_agent_count_per_group(session) -> int:
    if not rl_agent_enabled(session):
        return 0
    return max(0, config_int(session.config.get('rl_agent_count_per_group', 0), 0))


def validate_rl_agent_count(session) -> int:
    if not rl_agent_enabled(session):
        session.config = {**session.config, 'rl_agent_enabled': '0'}
        return 0
    raw_count = session.config.get('rl_agent_count_per_group', 0)
    if isinstance(raw_count, bool):
        raise ValueError(RL_AGENT_COUNT_ERROR)
    try:
        count = int(str(raw_count).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(RL_AGENT_COUNT_ERROR) from exc
    if str(raw_count).strip() != str(count):
        raise ValueError(RL_AGENT_COUNT_ERROR)
    if not RL_AGENT_COUNT_MIN <= count <= RL_AGENT_COUNT_MAX:
        raise ValueError(RL_AGENT_COUNT_ERROR)
    session.config = {
        **session.config,
        'rl_agent_enabled': '1',
        'rl_agent_count_per_group': count,
    }
    return count


def effective_group_actor_count(session, human_count) -> int:
    return (
        int(human_count)
        + api_agent_count_per_group(session)
        + rl_agent_count_per_group(session)
    )
```

Call `validate_rl_agent_count()` beside `validate_api_agent_count()` in first-round `creating_session()`.

- [ ] **Step 5: Run configuration tests**

Run the command from Step 2. Expected: all `DynamicAgentConfigTests` pass.

### Task 2: Add Stable RL Persona Namespaces

**Files:**
- Modify: `dynamic_bottleneck_round/agents/personas.py:1-145`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing persona isolation tests**

```python
def test_rl_personas_are_stable_and_namespaced(self):
    session = SimpleNamespace(code='SESSION_PERSONA', vars={})
    first = app.initialize_rl_agent_personas(session, ['G01'], 2)
    second = app.initialize_rl_agent_personas(session, ['G01'], 2)

    self.assertEqual(first, second)
    self.assertEqual(set(first), {'G01_RL_01', 'G01_RL_02'})
    self.assertNotEqual(
        app.get_or_create_rl_agent_persona(session, 'G01', 'G01_RL_01'),
        {},
    )
```

- [ ] **Step 2: Run the test and verify missing helpers fail**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentConfigTests.test_rl_personas_are_stable_and_namespaced -v
```

Expected: error for missing RL persona helpers.

- [ ] **Step 3: Generalize persona initialization without changing API IDs**

Introduce an internal initializer and retain existing wrappers:

```python
def _initialize_agent_personas(session, group_labels, agent_count, actor_code):
    stored = session.vars.get(API_AGENT_PERSONA_SESSION_VAR, {})
    personas = deepcopy(stored) if isinstance(stored, dict) else {}
    selected = {}
    for group_label in sorted({str(label) for label in group_labels if label}):
        for index in range(1, max(0, int(agent_count)) + 1):
            agent_id = f'{group_label}_{actor_code}_{index:02d}'
            if not _valid_persona(personas.get(agent_id)):
                personas[agent_id] = _persona_snapshot(
                    str(session.code), group_label, agent_id,
                )
            selected[agent_id] = deepcopy(personas[agent_id])
    session.vars[API_AGENT_PERSONA_SESSION_VAR] = personas
    return selected


def initialize_api_agent_personas(session, group_labels, agent_count):
    return _initialize_agent_personas(session, group_labels, agent_count, 'API')


def initialize_rl_agent_personas(session, group_labels, agent_count):
    return _initialize_agent_personas(session, group_labels, agent_count, 'RL')


def get_or_create_rl_agent_persona(session, group_label, agent_id):
    return get_or_create_api_agent_persona(session, group_label, agent_id)
```

Import the two RL helpers in the app and initialize them in `creating_session()` when the validated RL count is nonzero.

- [ ] **Step 4: Run persona and existing API persona tests**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration \
  dynamic_bottleneck_round.agents.test_rl_fallback -v
```

Expected: all tests pass and existing `G01_API_01` IDs are unchanged.

### Task 3: Make the Local RL Policy Reusable

**Files:**
- Modify: `dynamic_bottleneck_round/agents/rl_fallback.py:1-215`
- Create: `dynamic_bottleneck_round/agents/independent_rl_agent.py`
- Modify: `dynamic_bottleneck_round/agents/test_rl_fallback.py`
- Create: `dynamic_bottleneck_round/agents/test_independent_rl_agent.py`

- [ ] **Step 1: Write failing policy-isolation tests**

Test that fallback defaults remain unchanged and independent choices carry their own identity:

```python
def test_independent_choice_uses_independent_policy_identity(self):
    state = initial_independent_rl_state(self.capacity_states)
    choice = choose_independent_rl_departure(
        state=state,
        available_slots=self.available_slots,
        cost_parameters=self.cost_parameters,
        capacity_states=self.capacity_states,
        tolls=[],
        rewards=[],
        persona=self.persona,
    )
    self.assertEqual(choice['decision_source'], 'rl_policy')
    self.assertEqual(choice['policy_version'], 'dynamic_independent_rl_v1')

def test_independent_state_is_not_valid_fallback_state(self):
    independent = initial_independent_rl_state(self.capacity_states)
    fallback = valid_or_initial_state(independent, self.capacity_states)
    self.assertEqual(fallback['policy_version'], RL_POLICY_VERSION)
    self.assertEqual(fallback['rounds_observed'], 0)
```

- [ ] **Step 2: Run policy tests and verify they fail**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_rl_fallback \
  dynamic_bottleneck_round.agents.test_independent_rl_agent -v
```

Expected: independent module import failure.

- [ ] **Step 3: Parameterize policy version and source with backward-compatible defaults**

Change the existing functions to accept keyword-only identity arguments:

```python
def initial_rl_state(capacity_states, *, policy_version=RL_POLICY_VERSION):
    return {
        'policy_version': policy_version,
        # existing state fields unchanged
    }


def valid_or_initial_state(
    state,
    capacity_states,
    *,
    policy_version=RL_POLICY_VERSION,
):
    if not isinstance(state, dict):
        return initial_rl_state(capacity_states, policy_version=policy_version)
    if state.get('policy_version') != policy_version:
        return initial_rl_state(capacity_states, policy_version=policy_version)
    # existing capacity validation unchanged


def choose_rl_departure(
    *,
    state,
    available_slots,
    cost_parameters,
    capacity_states,
    tolls,
    rewards,
    persona,
    known_current_capacity=None,
    policy_version=RL_POLICY_VERSION,
    decision_source='deepseek_fallback_rl',
):
    current = valid_or_initial_state(
        state,
        capacity_states,
        policy_version=policy_version,
    )
    # ranking logic unchanged
    return {
        'departure_slot': selected_slot,
        'decision_source': decision_source,
        'policy_version': policy_version,
        # existing diagnostics unchanged
    }
```

- [ ] **Step 4: Add the independent policy facade**

Create `independent_rl_agent.py`:

```python
from .rl_fallback import (
    choose_rl_departure,
    initial_rl_state,
    observe_rl_outcome,
    valid_or_initial_state,
)

INDEPENDENT_RL_POLICY_VERSION = 'dynamic_independent_rl_v1'


def initial_independent_rl_state(capacity_states):
    return initial_rl_state(
        capacity_states,
        policy_version=INDEPENDENT_RL_POLICY_VERSION,
    )


def valid_or_initial_independent_rl_state(state, capacity_states):
    return valid_or_initial_state(
        state,
        capacity_states,
        policy_version=INDEPENDENT_RL_POLICY_VERSION,
    )


def choose_independent_rl_departure(**kwargs):
    return choose_rl_departure(
        **kwargs,
        policy_version=INDEPENDENT_RL_POLICY_VERSION,
        decision_source='rl_policy',
    )


def observe_independent_rl_outcome(state, **kwargs):
    return observe_rl_outcome(state, **kwargs)
```

- [ ] **Step 5: Run all isolated RL tests**

Run the command from Step 2. Expected: all tests pass, including existing fallback-source assertions.

### Task 4: Generalize Virtual Decision Storage

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py:60-70,1680-1715,2040-2135`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing mixed-record storage tests**

```python
def test_virtual_record_storage_merges_api_and_rl_records(self):
    group, participant = self.make_record_group()
    rl_record = {'actor_type': 'rl_agent', 'agent_id': 'G01_RL_01'}
    api_record = {'actor_type': 'deepseek_api_agent', 'agent_id': 'G01_API_01'}

    app.save_virtual_decisions_for_group(group, [rl_record])
    app.save_virtual_decisions_for_group(group, [api_record])

    records = app.active_virtual_decisions_for_group(group)
    self.assertEqual(
        {(row['actor_type'], row['agent_id']) for row in records},
        {
            ('rl_agent', 'G01_RL_01'),
            ('deepseek_api_agent', 'G01_API_01'),
        },
    )

def test_api_lookup_ignores_existing_rl_records(self):
    group, _ = self.make_record_group()
    app.save_virtual_decisions_for_group(
        group,
        [{'actor_type': 'rl_agent', 'agent_id': 'G01_RL_01'}],
    )
    self.assertEqual(app.active_agent_decisions_for_group(group), [])
```

- [ ] **Step 2: Run storage tests and verify missing generic helpers fail**

Run the relevant test class. Expected: errors for `save_virtual_decisions_for_group()` and `active_virtual_decisions_for_group()`.

- [ ] **Step 3: Introduce a generic virtual-record store with merge semantics**

Keep the existing participant-var key for backward compatibility, but remove the API-mode guard from generic reads:

```python
def virtual_decision_identity(record):
    return (
        str(record.get('actor_type', API_AGENT_TYPE_DEEPSEEK)),
        str(record.get('agent_id', '')),
    )


def active_virtual_decisions_for_group(group):
    players = group.get_players()
    if not players:
        return []
    stored = players[0].participant.vars.get(
        AGENT_DECISIONS_PARTICIPANT_VAR,
        {},
    )
    records = stored.get(str(group.round_number), []) if isinstance(stored, dict) else []
    return deepcopy(records) if isinstance(records, list) else []


def save_virtual_decisions_for_group(group, records):
    players = group.get_players()
    if not players:
        return
    stored = deepcopy(players[0].participant.vars.get(
        AGENT_DECISIONS_PARTICIPANT_VAR,
        {},
    ))
    round_key = str(group.round_number)
    merged = {
        virtual_decision_identity(row): deepcopy(row)
        for row in stored.get(round_key, [])
    }
    for record in records:
        merged[virtual_decision_identity(record)] = deepcopy(record)
    stored[round_key] = list(merged.values())
    players[0].participant.vars[AGENT_DECISIONS_PARTICIPANT_VAR] = stored


def active_agent_decisions_for_group(group):
    return [
        record for record in active_virtual_decisions_for_group(group)
        if record.get('actor_type', API_AGENT_TYPE_DEEPSEEK)
        == API_AGENT_TYPE_DEEPSEEK
    ]


def save_agent_decisions_for_group(group, records):
    save_virtual_decisions_for_group(group, records)
```

- [ ] **Step 4: Run mixed storage and existing API prefetch tests**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentDecisionTests -v
```

Expected: all tests pass and saved RL records do not suppress DeepSeek prefetch.

### Task 5: Generate Independent RL Choices And Update Separate State

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py:20-35,60-75,1715-1920,2550-2620`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing choice, information-boundary, and state-isolation tests**

Add tests for two RL Agents in the same group:

```python
def test_independent_rl_records_are_created_once_with_distinct_state(self):
    group, participant = self.make_rl_group(reveal_timing='after_decision', count=2)

    first = app.prepare_independent_rl_decisions_for_group(group)
    second = app.prepare_independent_rl_decisions_for_group(group)

    self.assertEqual(first, second)
    self.assertEqual({row['agent_id'] for row in first}, {'G01_RL_01', 'G01_RL_02'})
    self.assertTrue(all(row['actor_type'] == 'rl_agent' for row in first))
    self.assertTrue(all(row['decision_source'] == 'rl_policy' for row in first))
    self.assertTrue(all('actual_capacity' not in row['context_json'] for row in first))

def test_independent_rl_states_update_once_and_do_not_share_q_tables(self):
    group, participant = self.make_rl_group(reveal_timing='after_decision', count=2)
    records = app.prepare_independent_rl_decisions_for_group(group)
    records[0]['total_cost'] = 5
    records[1]['total_cost'] = 17

    app.update_independent_rl_states(group, records)
    app.update_independent_rl_states(group, records)

    states = participant.vars[app.INDEPENDENT_RL_STATE_PARTICIPANT_VAR]
    self.assertEqual(states['G01_RL_01']['rounds_observed'], 1)
    self.assertEqual(states['G01_RL_02']['rounds_observed'], 1)
    self.assertIsNot(states['G01_RL_01']['q_values'], states['G01_RL_02']['q_values'])
```

- [ ] **Step 2: Run focused tests and verify helper failures**

Run the new test class/methods. Expected: missing preparation and update functions.

- [ ] **Step 3: Add independent RL state storage helpers**

Use a separate participant-var key from the DeepSeek shadow state:

```python
INDEPENDENT_RL_STATE_PARTICIPANT_VAR = (
    'dynamic_bottleneck_round_independent_rl_state_v1'
)


def independent_rl_state_store(group):
    players = group.get_players()
    if not players:
        return {}, None
    participant = players[0].participant
    stored = participant.vars.get(INDEPENDENT_RL_STATE_PARTICIPANT_VAR, {})
    return deepcopy(stored) if isinstance(stored, dict) else {}, participant
```

- [ ] **Step 4: Build idempotent local RL records from the human-visible choice set**

Implement `prepare_independent_rl_decisions_for_group(group)` using the same schedule, toll, reward, capacity-context, and previous anonymous distribution builders as the API choice set. For each `Gxx_RL_nn`:

```python
choice = choose_independent_rl_departure(
    state=state,
    available_slots=choice_set.available_slots,
    cost_parameters=choice_set.cost_parameters,
    capacity_states=choice_set.capacity_context['capacity_states'],
    tolls=choice_set.tolls,
    rewards=choice_set.rewards,
    persona=persona,
    known_current_capacity=(
        group.dynamic_capacity
        if choice_set.capacity_context.get('capacity_revealed')
        else None
    ),
)
```

Construct records with:

```python
{
    'actor_type': RL_AGENT_TYPE,
    'agent_type': RL_AGENT_TYPE,
    'agent_id': agent_id,
    'persona_id': persona['persona_id'],
    'persona_label': persona['label'],
    'departure_slot': choice['departure_slot'],
    'departure_minute': departure_minute_for_slot(choice['departure_slot'], schedule),
    'decision_source': choice['decision_source'],
    'policy_version': choice['policy_version'],
    'context_json': json.dumps({
        'capacity_context': choice_set.capacity_context,
        'belief': choice['belief'],
        'rounds_observed': choice['rounds_observed'],
    }, ensure_ascii=False, sort_keys=True),
    'state_updated': False,
}
```

On a local policy exception, create a legal lowest-schedule-cost record with `decision_source='rl_fallback_lowest_schedule_cost'`. Save records through `save_virtual_decisions_for_group()` and return existing RL records on repeat calls.

- [ ] **Step 5: Update each RL Agent exactly once after settlement**

Implement `update_independent_rl_states(group, records)`:

```python
for record in records:
    if record.get('actor_type') != RL_AGENT_TYPE or record.get('state_updated'):
        continue
    agent_id = record['agent_id']
    persona = get_or_create_rl_agent_persona(
        group.session,
        group_label_for_group(group),
        agent_id,
    )
    state = valid_or_initial_independent_rl_state(
        states.get(agent_id),
        capacity_states,
    )
    states[agent_id] = observe_independent_rl_outcome(
        state,
        revealed_capacity=group.dynamic_capacity,
        departure_slot=record['departure_slot'],
        total_cost=record['total_cost'],
        anonymous_slot_counts=completed_anonymous_slot_counts(group),
        persona=persona,
    )
    record['state_updated'] = True
```

Persist the state map and re-save updated virtual records. Call preparation during round-start synchronization so local choices exist before human choices; retain an idempotent preparation call in settlement as a safety net.

- [ ] **Step 6: Run RL orchestration and reveal tests**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_independent_rl_agent \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v
```

Expected: all tests pass; no API call is made for an independent RL Agent.

### Task 6: Include RL Agents In Schedule, Toll Calibration, And Settlement

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py:620-710,1285-1350,1500-1630`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Write failing combined-settlement tests**

Create a deterministic group with one human, one API Agent, and two RL Agents, then assert:

```python
def test_settlement_includes_human_api_and_independent_rl(self):
    group = self.make_combined_actor_group()
    api = self.make_virtual_record('deepseek_api_agent', 'G01_API_01', slot=2)
    rl = [
        self.make_virtual_record('rl_agent', 'G01_RL_01', slot=2),
        self.make_virtual_record('rl_agent', 'G01_RL_02', slot=3),
    ]
    with (
        patch.object(app, 'collect_api_agent_prefetch', return_value=[api]),
        patch.object(app, 'prepare_independent_rl_decisions_for_group', return_value=rl),
    ):
        self.assertTrue(app._set_results_locked(group))

    saved = app.active_virtual_decisions_for_group(group)
    self.assertEqual(len(saved), 3)
    self.assertTrue(all('total_cost' in row for row in saved))
    self.assertEqual(group.human.slot_load, 3)
```

Also assert automatic departure-window and toll-calibration calls receive `human + API + RL`, while `rl_fallback_enabled=1` adds zero actors.

- [ ] **Step 2: Run combined settlement tests and verify failures**

Expected: RL records are absent from `actors_by_minute` or overwritten by API records.

- [ ] **Step 3: Combine virtual records without changing DeepSeek pending behavior**

At the top of `_set_results_locked()`:

```python
api_records = collect_api_agent_prefetch(group)
if api_records is API_AGENT_DECISIONS_PENDING:
    return False
rl_records = prepare_independent_rl_decisions_for_group(group)
virtual_records = [*api_records, *rl_records]
```

Insert every virtual record using its own `actor_type`:

```python
for record in virtual_records:
    actors_by_minute.setdefault(record['departure_minute'], []).append({
        'actor_type': record['actor_type'],
        'source': record,
        'reference_player': reference_player,
        'departure_slot': record['departure_slot'],
    })
```

Treat every non-human record as a mapping when assigning result values. After settlement:

```python
update_rl_shadow_states(group, api_records)
update_independent_rl_states(group, rl_records)
save_virtual_decisions_for_group(group, virtual_records)
```

- [ ] **Step 4: Verify effective count flows into schedule and toll calibration**

Retain existing `effective_group_actor_count()` call sites and assert they now include both virtual types. Do not add a second RL-specific adjustment at either call site.

- [ ] **Step 5: Run combined integration tests**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v
```

Expected: combined actor settlement, point-queue equality, and prior API-only cases all pass.

### Task 7: Add Create Session Controls

**Files:**
- Modify: `_templates/otree/CreateSession.html:1-250`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] **Step 1: Extend the failing template test**

```python
def test_create_session_page_has_independent_rl_controls(self):
    html = Path('_templates/otree/CreateSession.html').read_text(encoding='utf-8')
    self.assertIn('是否加入独立 RL 参与者', html)
    self.assertIn('独立 RL 参与者数量', html)
    self.assertIn('rl_agent_enabled', html)
    self.assertIn('rl_agent_count_per_group', html)
    self.assertIn('不会增加 API 等待时间', html)
```

- [ ] **Step 2: Run the template test and verify failure**

Run `DynamicAgentAdminTemplateTests`. Expected: missing independent RL labels and suffixes.

- [ ] **Step 3: Add independent controls and hidden config synchronization**

Add a separate control below the DeepSeek controls:

```html
<label class="form-check">
  <input id="dynamic-independent-rl-enabled" type="checkbox">
  <span>是否加入独立 RL 参与者</span>
</label>
<div id="dynamic-independent-rl-count-wrap" hidden>
  <label for="dynamic-independent-rl-count">独立 RL 参与者数量（每组）</label>
  <input id="dynamic-independent-rl-count" type="number" min="1" max="5" step="1" value="1">
  <small>本地独立学习，不调用 API，不会增加 API 等待时间。</small>
</div>
<input id="dynamic-independent-rl-mode" type="hidden" value="0">
```

Extend JavaScript source maps for:

```javascript
const rlAgentEnabledSuffix = ".rl_agent_enabled";
const rlAgentCountSuffix = ".rl_agent_count_per_group";
```

Store per-config values, remove original table-row names, restore names only for the selected dynamic config, validate count only when enabled, and keep this switch independent from `dynamic-agent-enabled` and `dynamic-rl-fallback-enabled`.

- [ ] **Step 4: Run template compatibility tests**

Run:

```bash
python -m unittest \
  admin_template_compatibility_tests \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentAdminTemplateTests -v
```

Expected: all tests pass; template still extends `otree/BaseAdminRegular.html`.

### Task 8: Update Anonymous Results, Export, And Admin Report

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py:2140-2465,2765-2778`
- Modify: `dynamic_bottleneck_round/admin_report.html:1-30`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_round/tests.py:1000-1160`

- [ ] **Step 1: Write failing aggregation and export tests**

```python
def test_results_include_rl_agents_without_exposing_actor_type(self):
    snapshot = app.result_current_round_cost_snapshot(self.player)
    self.assertEqual(sum(item['participant_count'] for item in snapshot['bars']), 4)
    self.assertNotIn('rl_agent', json.dumps(snapshot, ensure_ascii=False))

def test_export_distinguishes_api_rl_and_fallback(self):
    rl_row = app.export_row_for_agent_record(self.rl_record, self.reference_player)
    api_row = app.export_row_for_agent_record(self.api_record, self.reference_player)
    self.assertEqual(rl_row[app.EXPORT_HEADERS.index('actor_type')], 'rl_agent')
    self.assertEqual(
        api_row[app.EXPORT_HEADERS.index('actor_type')],
        'deepseek_api_agent',
    )
```

Add report assertions for separate `api_agent_count`, `independent_rl_agent_count`, and `rl_fallback_count`.

- [ ] **Step 2: Run aggregation/export tests and verify failures**

Expected: current helpers filter by API mode or hard-code `actor_type='api_agent'`.

- [ ] **Step 3: Switch participant-facing aggregations to all virtual records**

Replace aggregation-only calls to `active_agent_decisions_for_group()` with `active_virtual_decisions_for_group()` in departure counts and cost snapshots. Do not add actor labels to template variables.

- [ ] **Step 4: Export the record's actual actor identity**

Extend export headers with:

```python
'agent_persona_id',
'agent_persona_label',
'rl_policy_version',
'rl_rounds_observed',
```

Populate values dynamically:

```python
'actor_type': record.get('actor_type', API_AGENT_TYPE_DEEPSEEK),
'agent_type': record.get('agent_type', record.get('actor_type', '')),
'agent_persona_id': record.get('persona_id', ''),
'agent_persona_label': record.get('persona_label', ''),
'rl_policy_version': record.get('policy_version', ''),
'rl_rounds_observed': record.get('rounds_observed', ''),
```

For human rows, append empty values for the new fields. Iterate all virtual records in `agent_decisions_for_players()`.

- [ ] **Step 5: Separate admin metrics**

Build API and RL subsets per round:

```python
api_records = [
    row for row in virtual_records
    if row.get('actor_type', API_AGENT_TYPE_DEEPSEEK)
    == API_AGENT_TYPE_DEEPSEEK
]
rl_records = [
    row for row in virtual_records
    if row.get('actor_type') == RL_AGENT_TYPE
]
```

Report their counts independently. Keep `rl_fallback_count` limited to DeepSeek rows whose source is `deepseek_fallback_rl`. Include all virtual rows in average queue, average cost, average toll, and departure distribution.

Update `admin_report.html` headings so “Agent” becomes “DeepSeek”, add “独立 RL”, and retain “RL 接管” strictly for DeepSeek fallback.

- [ ] **Step 6: Run aggregation, export, report, and bot assertion tests**

Run:

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v
python -m py_compile \
  dynamic_bottleneck_round/__init__.py \
  dynamic_bottleneck_round/tests.py \
  dynamic_bottleneck_round/agents/personas.py \
  dynamic_bottleneck_round/agents/rl_fallback.py \
  dynamic_bottleneck_round/agents/independent_rl_agent.py \
  settings.py
```

Expected: all tests and compilation pass.

### Task 9: Full Regression And Manual Verification

**Files:**
- Verify: all files listed above

- [ ] **Step 1: Run all dynamic Agent/RL unit tests**

```bash
python -m unittest \
  dynamic_bottleneck_round.agents.test_rl_fallback \
  dynamic_bottleneck_round.agents.test_independent_rl_agent \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v
```

Expected: all tests pass.

- [ ] **Step 2: Run whitespace and static checks**

```bash
git diff --check -- \
  settings.py \
  _templates/otree/CreateSession.html \
  dynamic_bottleneck_round
python -m py_compile \
  settings.py \
  dynamic_bottleneck_round/__init__.py \
  dynamic_bottleneck_round/tests.py \
  dynamic_bottleneck_round/agents/personas.py \
  dynamic_bottleneck_round/agents/rl_fallback.py \
  dynamic_bottleneck_round/agents/independent_rl_agent.py
```

Expected: no output from `git diff --check`; compilation exits zero.

- [ ] **Step 3: Run dynamic app bots with isolated databases**

```bash
eval "$(conda shell.zsh hook)"
conda activate otree_env
OTREE_DATABASE_URL=sqlite:////tmp/dynamic_independent_rl_demo.sqlite3 \
OTREE_ADMIN_PASSWORD=devpass \
otree test dynamic_bottleneck_round_demo 2
```

Expected: all dynamic bot cases and rounds pass. The configured test fixture must cover independent RL only and combined DeepSeek + RL; API failure may exercise `deepseek_fallback_rl`, but total actor count must remain humans + DeepSeek + independent RL.

- [ ] **Step 4: Run original single-bottleneck regression**

```bash
OTREE_DATABASE_URL=sqlite:////tmp/single_bottleneck_rl_regression.sqlite3 \
OTREE_ADMIN_PASSWORD=devpass \
otree test single_bottleneck_demo 5
```

Expected: existing `single_bottleneck` bot passes unchanged.

- [ ] **Step 5: Manually verify Create Session combinations**

Create four short dynamic Sessions and verify report totals:

```text
DeepSeek off, independent RL off -> only humans
DeepSeek on (2), independent RL off -> humans + 2
DeepSeek off, independent RL on (3) -> humans + 3
DeepSeek on (2), independent RL on (3), fallback on -> humans + 5
```

For the last case, deliberately leave the API key unavailable and verify the two DeepSeek rows use `deepseek_fallback_rl`, the three independent rows use `rl_policy`, and the effective actor count is still humans plus five rather than humans plus seven.

- [ ] **Step 6: Inspect export and participant-facing anonymity**

Download the custom export and verify one row per virtual actor per round, correct actor types, stable IDs/personas, and separate independent Q-state progression. On a participant result page, verify the totals include virtual actors but no `DeepSeek`, `RL Agent`, Agent ID, or persona label is displayed.

---

## Stability Constraints During Implementation

- Do not create oTree `Player` rows for virtual actors.
- Do not modify `single_bottleneck` code or configuration.
- Do not make independent RL wait on the DeepSeek executor.
- Do not expose current capacity to RL under `after_decision`.
- Do not count `deepseek_fallback_rl` as an independent actor.
- Do not share mutable state or Q tables between RL Agent IDs.
- Do not overwrite one virtual actor type when persisting another.
- Do not require `resetdb`; all added state is config or `participant.vars`.
