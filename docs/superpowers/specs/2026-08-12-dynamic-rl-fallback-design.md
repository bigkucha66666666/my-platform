# Dynamic Bottleneck RL Fallback Design

## Goal

Add an optional local reinforcement-learning fallback for DeepSeek Agents in
`dynamic_bottleneck_round`. When enabled, the policy learns in shadow mode on
every completed round and immediately supplies a departure-time choice if the
DeepSeek request fails. When disabled, the existing deterministic lowest-cost
fallback remains unchanged.

## Information Boundary

The RL fallback receives only information available to a human participant at
decision time: published capacity states and probabilities, completed prior
round outcomes, prior anonymous departure distributions, experiment cost rules,
and its stable persona. With `capacity_reveal_timing='after_decision'`, the
current round capacity, hidden generation phase, and true transition matrix are
excluded.

Each Agent starts with the public capacity prior and an empty Q table in every
new Session. State is stored per group and Agent in `participant.vars`; no model
or database migration is required.

## Learning And Choice

The local policy combines:

1. A transition belief estimated from previously revealed capacities with
   smoothed counts.
2. A model-based expected-cost estimate for every legal departure slot.
3. A small tabular Q-learning correction learned from the Agent's realized
   choices and costs.
4. Stable persona effects for capacity risk aversion, choice inertia, and
   adaptation speed.

The reward is negative final choice cost. A normal DeepSeek choice is still
used in settlement, but its realized outcome updates the shadow policy. If the
API fails, the already prepared local RL action is returned with
`decision_source='deepseek_fallback_rl'`. The network worker never accesses the
oTree ORM.

## Administration And Reporting

Both dynamic Session configs expose `rl_fallback_enabled`, defaulting to off.
The Create Session page shows a subordinate switch only when Agent mode is
enabled. Reports and exports distinguish RL takeover from the legacy fallback.
No additional HTTP port or external service is created because the policy runs
inside the oTree process in milliseconds.

## Failure Behavior

If RL state is absent or invalid, it is rebuilt from an empty state. If the RL
choice cannot be produced, the existing lowest-schedule-cost fallback remains
the final safety net. Turning the feature off preserves current behavior.
