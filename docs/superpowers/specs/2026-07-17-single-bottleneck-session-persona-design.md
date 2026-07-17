# Single-Bottleneck Session Persona Design

## Objective

Add stable behavioral personas to the existing DeepSeek virtual participants without changing the oTree database schema. A persona must remain unchanged for the same Agent throughout one Session, be included in every DeepSeek decision context, and remain auditable after the decision.

This change applies only to API Agents in `shadow` or `active` mode. Human participants, settlement formulas, result-page presentation, and completed rounds remain unchanged.

## Storage Decision

Store a complete persona snapshot in `session.vars` under a versioned key:

```python
session.vars["single_bottleneck_agent_personas_v1"] = {
    "G01_API_01": {
        "persona_id": "queue_averse_v1",
        "persona_version": "bottleneck_persona_v1",
        "label": "queue_averse",
        "traits": {
            "queue_aversion": 9,
            "early_arrival_aversion": 4,
            "late_arrival_aversion": 8,
            "toll_sensitivity": 5,
            "reward_sensitivity": 6,
            "risk_aversion": 7,
            "choice_inertia": 5,
            "adaptation_speed": 6
        }
    }
}
```

The stored value is a JSON-serializable dictionary. Saving the full snapshot instead of only `persona_id` prevents a later code change to the built-in library from modifying an ongoing Session.

No oTree model field or table is added. No database reset or migration is required.

## Persona Library

Version `bottleneck_persona_v1` contains five built-in personas:

1. `balanced_v1`: moderate sensitivity across all dimensions.
2. `queue_averse_v1`: strongly avoids expected queueing.
3. `punctuality_first_v1`: strongly avoids late arrival and values arriving near the target time.
4. `toll_sensitive_v1`: strongly responds to tolls and monetary rewards.
5. `adaptive_v1`: changes choices readily after poor outcomes and has low choice inertia.

Each trait uses an integer scale from 1 to 10. Persona labels are internal context only and are not displayed on participant-facing result pages.

## Assignment

The helper `get_or_create_api_agent_persona(session, group_label, agent_id)` owns persona assignment.

1. Read the versioned persona dictionary from `session.vars`.
2. Return the existing complete snapshot when `agent_id` is already present.
3. For a new Agent, calculate a stable SHA-256 digest from the Session code, group label, Agent ID, and persona-library version.
4. Convert the digest to a library index and store a deep copy of that persona.
5. Return the stored snapshot.

Python's built-in `hash()` is not used because its process-level randomization would make assignments unstable across server restarts.

Assignment is lazy: the first Agent decision in a Session creates the snapshot. Repeated calls and later rounds read the existing snapshot. A completed decision is never recalculated.

## Decision Context Integration

`AgentChoiceSet` gains two JSON-serializable fields:

```python
agent_id: str = ""
persona: Mapping[str, object] = field(default_factory=dict)
```

`api_agent_choice_set_for_group` accepts the Agent ID and persona snapshot. Because Agents can have different personas, `create_api_agent_decisions_for_group` builds an individual choice set inside the Agent loop rather than sharing one choice set across all Agents.

The existing `build_chat_completion_payload` serializes the complete `AgentChoiceSet`, so the Agent identity and persona are included in the DeepSeek prompt. The system instruction explicitly tells DeepSeek to treat the persona as stable behavioral preferences, not as additional experiment facts.

The existing `AgentDecision.context_json` receives the same serialized choice set. This preserves the persona actually used for each decision without adding a database field. The custom CSV export is not changed in this iteration; persona data can be recovered from `context_json` or added to export output later without changing the database schema.

## Compatibility

- Agent mode `off`: no persona is created and behavior is unchanged.
- Agent mode `shadow`: a persona is created and included in the recorded shadow decision.
- Agent mode `active`: the persona affects the Agent choice, while existing queue settlement and result inclusion remain unchanged.
- Existing completed Sessions: stored decisions and results are not modified.
- Existing in-progress Sessions: only decisions made after deployment use personas.
- Missing persona storage: initialized lazily.
- Existing stored snapshot: remains authoritative even if the built-in library changes.

The implementation does not add or modify `Player`, `Group`, `Subsession`, or `AgentDecision` fields.

## Error Handling

Persona lookup must not prevent a round from settling. If the session variable is missing, it is initialized. If its top-level value is malformed, it is replaced with a new empty versioned dictionary before assignment. Built-in persona definitions are constants and validated by tests.

DeepSeek API errors continue to use the existing fallback mechanism. The fallback does not use Persona in this iteration; changing fallback behavior is a separate behavioral-calibration task.

## Tests

Tests are written before implementation and cover:

1. Repeated lookups return the same persona snapshot for the same Session, group, and Agent.
2. The stored snapshot remains unchanged after repeated access.
3. Persona values are JSON serializable and trait values are between 1 and 10.
4. The DeepSeek request context contains `agent_id`, `persona_id`, and persona traits.
5. Agent mode `off` does not initialize persona state through the decision path.
6. Existing response parsing, API fallback, settlement, and result tests continue to pass.

## Out of Scope

- New database models or fields.
- Persona inference from human pilot data.
- Learned persona-loading embeddings.
- Online modification of Persona during an active Session.
- Participant-facing disclosure of Agent identity or Persona.
- Persona-aware RL fallback.
- Changes to historical result calculations or CSV columns.
