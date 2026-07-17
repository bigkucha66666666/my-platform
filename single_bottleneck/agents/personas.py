"""Stable built-in personas for single-bottleneck API Agents."""

from __future__ import annotations

import hashlib
from copy import deepcopy


API_AGENT_PERSONA_SESSION_VAR = "single_bottleneck_agent_personas_v1"
PERSONA_LIBRARY_VERSION = "bottleneck_persona_v1"


def _persona(persona_id, label, **traits):
    return {
        "persona_id": persona_id,
        "persona_version": PERSONA_LIBRARY_VERSION,
        "label": label,
        "traits": traits,
    }


PERSONA_LIBRARY = (
    _persona(
        "balanced_v1",
        "balanced",
        queue_aversion=5,
        early_arrival_aversion=5,
        late_arrival_aversion=7,
        toll_sensitivity=5,
        reward_sensitivity=5,
        risk_aversion=5,
        choice_inertia=5,
        adaptation_speed=5,
    ),
    _persona(
        "queue_averse_v1",
        "queue_averse",
        queue_aversion=9,
        early_arrival_aversion=4,
        late_arrival_aversion=8,
        toll_sensitivity=5,
        reward_sensitivity=6,
        risk_aversion=7,
        choice_inertia=5,
        adaptation_speed=6,
    ),
    _persona(
        "punctuality_first_v1",
        "punctuality_first",
        queue_aversion=6,
        early_arrival_aversion=7,
        late_arrival_aversion=10,
        toll_sensitivity=4,
        reward_sensitivity=4,
        risk_aversion=6,
        choice_inertia=6,
        adaptation_speed=5,
    ),
    _persona(
        "toll_sensitive_v1",
        "toll_sensitive",
        queue_aversion=5,
        early_arrival_aversion=5,
        late_arrival_aversion=7,
        toll_sensitivity=10,
        reward_sensitivity=9,
        risk_aversion=6,
        choice_inertia=4,
        adaptation_speed=6,
    ),
    _persona(
        "adaptive_v1",
        "adaptive",
        queue_aversion=6,
        early_arrival_aversion=5,
        late_arrival_aversion=8,
        toll_sensitivity=6,
        reward_sensitivity=6,
        risk_aversion=4,
        choice_inertia=2,
        adaptation_speed=9,
    ),
)


def _stable_persona_index(session_code: str, group_label: str, agent_id: str) -> int:
    assignment_key = "|".join(
        [session_code, group_label, agent_id, PERSONA_LIBRARY_VERSION]
    )
    digest = hashlib.sha256(assignment_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % len(PERSONA_LIBRARY)


def get_or_create_api_agent_persona(session, group_label: str, agent_id: str):
    stored_value = session.vars.get(API_AGENT_PERSONA_SESSION_VAR, {})
    store = deepcopy(stored_value) if isinstance(stored_value, dict) else {}

    existing = store.get(agent_id)
    if isinstance(existing, dict) and existing.get("persona_version"):
        return deepcopy(existing)

    index = _stable_persona_index(str(session.code), group_label, agent_id)
    snapshot = deepcopy(PERSONA_LIBRARY[index])
    store[agent_id] = snapshot
    session.vars[API_AGENT_PERSONA_SESSION_VAR] = store
    return deepcopy(snapshot)
