"""Stable built-in personas for single-bottleneck API Agents."""

from __future__ import annotations

import hashlib
from copy import deepcopy


API_AGENT_PERSONA_SESSION_VAR = "single_bottleneck_agent_personas_v1"
PERSONA_LIBRARY_VERSION = "bottleneck_persona_v1"
PERSONA_TRAIT_NAMES = {
    "queue_aversion",
    "early_arrival_aversion",
    "late_arrival_aversion",
    "toll_sensitivity",
    "reward_sensitivity",
    "risk_aversion",
    "choice_inertia",
    "adaptation_speed",
}


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


def _is_valid_persona_snapshot(value) -> bool:
    if not isinstance(value, dict):
        return False
    if not all(value.get(key) for key in ("persona_id", "persona_version", "label")):
        return False
    traits = value.get("traits")
    if not isinstance(traits, dict) or set(traits) != PERSONA_TRAIT_NAMES:
        return False
    return all(isinstance(score, int) and 1 <= score <= 10 for score in traits.values())


def _persona_snapshot(session_code: str, group_label: str, agent_id: str):
    index = _stable_persona_index(session_code, group_label, agent_id)
    return deepcopy(PERSONA_LIBRARY[index])


def initialize_api_agent_personas(session, group_labels, agent_count: int):
    stored_value = session.vars.get(API_AGENT_PERSONA_SESSION_VAR, {})
    store = deepcopy(stored_value) if isinstance(stored_value, dict) else {}

    for group_label in sorted({str(label) for label in group_labels if label}):
        for index in range(1, max(0, int(agent_count)) + 1):
            agent_id = f"{group_label}_API_{index:02d}"
            if not _is_valid_persona_snapshot(store.get(agent_id)):
                store[agent_id] = _persona_snapshot(
                    str(session.code),
                    group_label,
                    agent_id,
                )

    if store != stored_value:
        session.vars[API_AGENT_PERSONA_SESSION_VAR] = store
    return deepcopy(store)


def get_or_create_api_agent_persona(session, group_label: str, agent_id: str):
    stored_value = session.vars.get(API_AGENT_PERSONA_SESSION_VAR, {})
    store = deepcopy(stored_value) if isinstance(stored_value, dict) else {}

    existing = store.get(agent_id)
    if _is_valid_persona_snapshot(existing):
        return deepcopy(existing)

    snapshot = _persona_snapshot(str(session.code), group_label, agent_id)
    store[agent_id] = snapshot
    session.vars[API_AGENT_PERSONA_SESSION_VAR] = store
    return deepcopy(snapshot)
