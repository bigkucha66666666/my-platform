# Dynamic Bottleneck Independent RL Agents Design

## Goal

Extend `dynamic_bottleneck_round` so independently learning RL Agents can join
the experiment alongside human participants and DeepSeek Agents. RL Agents are
virtual actors rather than oTree `Player` rows. They choose departure times,
consume bottleneck capacity, pay tolls, incur costs, and appear anonymously in
participant-facing aggregate results.

The existing `rl_fallback_enabled` behavior remains separate. A DeepSeek Agent
whose API request fails can still use its shadow RL fallback, but that fallback
does not create another actor.

## Configuration And Actor Count

Add the following Session config values:

```python
rl_agent_enabled=0
rl_agent_count_per_group=0
rl_agent_policy_version='dynamic_independent_rl_v1'
```

The Create Session form exposes an independent RL switch and count field. The
count must be a non-negative integer within the same operational safety limit
used for virtual Agents. Setting the switch off makes the effective count zero.

The effective number of bottleneck actors is:

```text
human participants + DeepSeek Agents + independent RL Agents
```

`rl_fallback_enabled` never changes this count. Dynamic departure-window
generation and coarse-toll calibration use the effective actor count, so the
calibrated experiment matches the actors that actually enter settlement.

## Identity, Persona, And State

Each RL Agent has a stable group-local ID such as `rl-1` and receives one of the
existing five personas using the same deterministic assignment rules as
DeepSeek Agents. Persona assignment remains fixed for the Session.

Every RL Agent maintains its own policy state. Q tables, transition counts,
previous action, and completed outcomes are not shared between RL Agents. State
is stored in the group's representative human participant's `participant.vars`,
keyed by Session, group, and RL Agent ID. This avoids database migrations and
keeps virtual actors independent from oTree page routing.

RL state is rebuilt from an empty policy if absent or malformed. It never falls
back to another RL Agent's state.

## Information Boundary

An independent RL Agent receives only information available to a human at the
time of decision:

- published capacity states and probabilities;
- previously revealed capacities and completed outcomes;
- previous anonymous departure distributions;
- current toll information visible to humans;
- experiment cost rules and its stable persona.

With `capacity_reveal_timing='before_decision'`, the current capacity is
included. With `capacity_reveal_timing='after_decision'`, current capacity,
hidden generation phase, and true transition mechanism are excluded. The RL
Agent learns any temporal pattern only from revealed history.

## Decision And Learning Flow

At round preparation, each independent RL Agent computes a local departure-time
choice from its own observable state. No API call or background worker is
needed. The policy reuses the current model-based expected-cost estimate and
tabular Q correction, but emits:

```text
actor_type = rl_agent
decision_source = rl_policy
```

DeepSeek prefetch continues independently. Participant waiting behavior remains
tied only to unresolved DeepSeek API requests; local RL computation must not
hold participants on a waiting page.

When all human and DeepSeek choices are available, settlement combines all
three actor types by departure minute. The current point-queue rule, dynamic
capacity, toll, schedule costs, and payoff calculation apply identically.

After settlement, each RL Agent updates only its own policy using its realized
choice and `reward = -total_cost`. The update occurs once per completed round
and is guarded by the existing results lock and persisted round records, making
repeated page polling idempotent.

## Records, Results, And Export

Independent RL decisions use the existing virtual decision-record channel,
extended to support multiple actor types. Each record contains at least:

- Session, group, round, Agent ID, actor type, and persona;
- departure slot/minute and decision source;
- observable decision context or a privacy-safe context summary;
- queue delay, arrival time, early/late time, toll, total cost, and payoff;
- RL policy version and learning diagnostics needed for reproducibility.

Participant-facing distributions and average-cost figures include humans,
DeepSeek Agents, and independent RL Agents anonymously. They do not disclose
which points belong to virtual actors.

Custom export emits one row per independent RL Agent per round with
`actor_type='rl_agent'`. DeepSeek rows remain `deepseek_api_agent`; a DeepSeek
choice made by its fallback remains a DeepSeek row with
`decision_source='deepseek_fallback_rl'`.

The admin report separately displays human, DeepSeek, and independent RL counts,
plus RL decisions, mean costs, and policy version. This prevents fallback usage
from being mistaken for an additional participant.

## Failure Handling

- Invalid RL count or mode rejects Session creation with a clear error.
- Failure to load an RL state initializes only that Agent's state.
- Failure to produce a legal RL action uses the deterministic
  lowest-schedule-cost choice for that same RL actor and records
  `decision_source='rl_fallback_lowest_schedule_cost'`.
- RL state is updated only after a completed settlement record exists.
- DeepSeek failures continue to use the existing shadow fallback and do not
  affect independent RL Agent state.

## Testing

Automated coverage must verify:

1. Effective actor count equals humans plus DeepSeek plus independent RL Agents.
2. Enabling DeepSeek RL fallback does not add actors.
3. Each RL Agent has a stable persona and independent learning state.
4. Independent RL choices affect queueing, toll calibration, costs, and payoff.
5. `after_decision` hides current capacity from RL decision context.
6. RL updates exactly once after settlement and persists across rounds.
7. Participant-facing aggregate results include RL anonymously.
8. Export and admin reports distinguish all actor and decision-source types.
9. Invalid counts are rejected and disabled mode preserves current behavior.
10. Dynamic app bots pass with RL only, DeepSeek only, both enabled, and both
    disabled; original `single_bottleneck` tests remain unchanged.

## Non-Goals

- No real oTree `Player` is created for an RL Agent.
- No external RL service, HTTP port, neural network, or new dependency is added.
- RL Agents do not access private participant identities or hidden capacity
  information.
- The current random-capacity process is not changed by this feature.
