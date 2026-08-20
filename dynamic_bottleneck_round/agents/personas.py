"""Stable personas scoped to the dynamic bottleneck app."""

from __future__ import annotations

import hashlib
from copy import deepcopy


API_AGENT_PERSONA_SESSION_VAR = 'dynamic_bottleneck_round_agent_personas_v1'
PERSONA_LIBRARY_VERSION = 'dynamic_bottleneck_persona_v1'
PERSONA_TRAIT_NAMES = {
    'queue_aversion',
    'early_arrival_aversion',
    'late_arrival_aversion',
    'toll_sensitivity',
    'reward_sensitivity',
    'capacity_risk_aversion',
    'choice_inertia',
    'adaptation_speed',
}


def _persona(persona_id, label, **traits):
    return {
        'persona_id': persona_id,
        'persona_version': PERSONA_LIBRARY_VERSION,
        'label': label,
        'traits': traits,
    }


PERSONA_LIBRARY = (
    _persona(
        'balanced_v1',
        'balanced',
        queue_aversion=5,
        early_arrival_aversion=5,
        late_arrival_aversion=7,
        toll_sensitivity=5,
        reward_sensitivity=5,
        capacity_risk_aversion=5,
        choice_inertia=5,
        adaptation_speed=5,
    ),
    _persona(
        'queue_averse_v1',
        'queue_averse',
        queue_aversion=9,
        early_arrival_aversion=4,
        late_arrival_aversion=8,
        toll_sensitivity=5,
        reward_sensitivity=6,
        capacity_risk_aversion=8,
        choice_inertia=4,
        adaptation_speed=6,
    ),
    _persona(
        'punctuality_first_v1',
        'punctuality_first',
        queue_aversion=6,
        early_arrival_aversion=7,
        late_arrival_aversion=10,
        toll_sensitivity=4,
        reward_sensitivity=4,
        capacity_risk_aversion=6,
        choice_inertia=6,
        adaptation_speed=5,
    ),
    _persona(
        'toll_sensitive_v1',
        'toll_sensitive',
        queue_aversion=5,
        early_arrival_aversion=5,
        late_arrival_aversion=7,
        toll_sensitivity=10,
        reward_sensitivity=9,
        capacity_risk_aversion=6,
        choice_inertia=4,
        adaptation_speed=6,
    ),
    _persona(
        'adaptive_v1',
        'adaptive',
        queue_aversion=6,
        early_arrival_aversion=5,
        late_arrival_aversion=8,
        toll_sensitivity=6,
        reward_sensitivity=6,
        capacity_risk_aversion=4,
        choice_inertia=2,
        adaptation_speed=9,
    ),
)


def _persona_snapshot(session_code: str, group_label: str, agent_id: str):
    key = '|'.join(
        [session_code, group_label, agent_id, PERSONA_LIBRARY_VERSION]
    )
    index = int.from_bytes(
        hashlib.sha256(key.encode('utf-8')).digest()[:8],
        'big',
    ) % len(PERSONA_LIBRARY)
    return deepcopy(PERSONA_LIBRARY[index])


def _valid_persona(value):
    if not isinstance(value, dict):
        return False
    if not all(value.get(key) for key in ('persona_id', 'persona_version', 'label')):
        return False
    traits = value.get('traits')
    return (
        isinstance(traits, dict)
        and set(traits) == PERSONA_TRAIT_NAMES
        and all(isinstance(score, int) and 1 <= score <= 10 for score in traits.values())
    )


def _initialize_agent_personas(session, group_labels, agent_count, actor_code):
    stored = session.vars.get(API_AGENT_PERSONA_SESSION_VAR, {})
    personas = deepcopy(stored) if isinstance(stored, dict) else {}
    selected = {}
    for group_label in sorted({str(label) for label in group_labels if label}):
        for index in range(1, max(0, int(agent_count)) + 1):
            agent_id = f'{group_label}_{actor_code}_{index:02d}'
            if not _valid_persona(personas.get(agent_id)):
                personas[agent_id] = _persona_snapshot(
                    str(session.code),
                    group_label,
                    agent_id,
                )
            selected[agent_id] = deepcopy(personas[agent_id])
    if personas != stored:
        session.vars[API_AGENT_PERSONA_SESSION_VAR] = personas
    return selected


def initialize_api_agent_personas(session, group_labels, agent_count):
    return _initialize_agent_personas(session, group_labels, agent_count, 'API')


def initialize_rl_agent_personas(session, group_labels, agent_count):
    return _initialize_agent_personas(session, group_labels, agent_count, 'RL')


def get_or_create_api_agent_persona(session, group_label, agent_id):
    stored = session.vars.get(API_AGENT_PERSONA_SESSION_VAR, {})
    personas = deepcopy(stored) if isinstance(stored, dict) else {}
    if _valid_persona(personas.get(agent_id)):
        return deepcopy(personas[agent_id])
    personas[agent_id] = _persona_snapshot(
        str(session.code),
        str(group_label),
        str(agent_id),
    )
    session.vars[API_AGENT_PERSONA_SESSION_VAR] = personas
    return deepcopy(personas[agent_id])


def get_or_create_rl_agent_persona(session, group_label, agent_id):
    return get_or_create_api_agent_persona(session, group_label, agent_id)
