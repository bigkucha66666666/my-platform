# Single-Bottleneck Session Persona Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every DeepSeek API Agent a stable built-in behavioral persona stored in `session.vars` and included in each decision context without changing the oTree database schema.

**Architecture:** A focused `personas.py` module owns the versioned library, deterministic assignment, snapshot copying, and session storage. The API payload remains generated from `AgentChoiceSet`; two new context fields carry Agent identity and Persona. The oTree integration resolves a Persona inside the per-Agent loop so multiple Agents no longer share an identical context.

**Tech Stack:** Python 3, oTree 5/6-compatible APIs, dataclasses, `hashlib.sha256`, `unittest`, `unittest.mock`

---

## File Structure

- Create `single_bottleneck/agents/personas.py`: Persona constants, validation-friendly JSON snapshots, deterministic assignment, and `session.vars` persistence.
- Create `single_bottleneck/agents/test_personas.py`: isolated unit tests for assignment stability, copy isolation, malformed storage recovery, and JSON-safe trait ranges.
- Modify `single_bottleneck/agents/deepseek_shadow_agent.py`: add Agent identity and Persona to `AgentChoiceSet`, and clarify how DeepSeek should use the Persona.
- Modify `single_bottleneck/agents/test_deepseek_shadow_agent.py`: verify that Persona information reaches the API request and recorded context.
- Modify `single_bottleneck/__init__.py`: resolve one Persona per Agent and build one choice set per Agent.
- Create `single_bottleneck/agents/test_persona_integration.py`: test active-mode integration and off-mode non-initialization without touching the existing dirty `single_bottleneck/tests.py`.

### Task 1: Built-In Persona Library and Session Storage

**Files:**
- Create: `single_bottleneck/agents/personas.py`
- Create: `single_bottleneck/agents/test_personas.py`

- [ ] **Step 1: Write failing Persona storage tests**

Create `single_bottleneck/agents/test_personas.py`:

```python
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from personas import (
    API_AGENT_PERSONA_SESSION_VAR,
    PERSONA_LIBRARY,
    PERSONA_LIBRARY_VERSION,
    get_or_create_api_agent_persona,
)


class PersonaStorageTests(unittest.TestCase):
    def make_session(self):
        return SimpleNamespace(code="SESSION_A", vars={})

    def test_same_agent_keeps_same_persona_snapshot(self):
        session = self.make_session()

        first = get_or_create_api_agent_persona(session, "G01", "G01_API_01")
        second = get_or_create_api_agent_persona(session, "G01", "G01_API_01")

        self.assertEqual(first, second)
        self.assertEqual(
            session.vars[API_AGENT_PERSONA_SESSION_VAR]["G01_API_01"],
            first,
        )

    def test_returned_persona_cannot_mutate_stored_snapshot(self):
        session = self.make_session()
        first = get_or_create_api_agent_persona(session, "G01", "G01_API_01")
        original_queue_aversion = first["traits"]["queue_aversion"]

        first["traits"]["queue_aversion"] = 99
        second = get_or_create_api_agent_persona(session, "G01", "G01_API_01")

        self.assertEqual(second["traits"]["queue_aversion"], original_queue_aversion)

    def test_persona_library_is_json_serializable_and_bounded(self):
        json.dumps(PERSONA_LIBRARY)

        self.assertEqual(PERSONA_LIBRARY_VERSION, "bottleneck_persona_v1")
        self.assertEqual(len(PERSONA_LIBRARY), 5)
        for persona in PERSONA_LIBRARY:
            self.assertEqual(persona["persona_version"], PERSONA_LIBRARY_VERSION)
            self.assertTrue(persona["persona_id"].endswith("_v1"))
            for value in persona["traits"].values():
                self.assertIsInstance(value, int)
                self.assertGreaterEqual(value, 1)
                self.assertLessEqual(value, 10)

    def test_malformed_session_store_is_reinitialized(self):
        session = self.make_session()
        session.vars[API_AGENT_PERSONA_SESSION_VAR] = "invalid"

        persona = get_or_create_api_agent_persona(session, "G01", "G01_API_01")

        self.assertIsInstance(session.vars[API_AGENT_PERSONA_SESSION_VAR], dict)
        self.assertEqual(
            session.vars[API_AGENT_PERSONA_SESSION_VAR]["G01_API_01"],
            persona,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
conda run -n otree_env python single_bottleneck/agents/test_personas.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'personas'` because the Persona module does not exist.

- [ ] **Step 3: Implement the Persona library and stable session assignment**

Create `single_bottleneck/agents/personas.py`:

```python
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
```

- [ ] **Step 4: Run the Persona tests and verify GREEN**

Run:

```bash
conda run -n otree_env python single_bottleneck/agents/test_personas.py
```

Expected: `Ran 4 tests` and `OK`.

- [ ] **Step 5: Commit the Persona module**

```bash
git add single_bottleneck/agents/personas.py single_bottleneck/agents/test_personas.py
git commit -m "feat: add stable API agent personas"
```

### Task 2: Include Persona in the DeepSeek Decision Context

**Files:**
- Modify: `single_bottleneck/agents/deepseek_shadow_agent.py:51-109`
- Modify: `single_bottleneck/agents/test_deepseek_shadow_agent.py:35-59`

- [ ] **Step 1: Write a failing payload test**

In `test_payload_requires_json_departure_slot`, add the Agent identity and Persona when constructing `AgentChoiceSet`:

```python
agent_id="G01_API_01",
persona={
    "persona_id": "queue_averse_v1",
    "persona_version": "bottleneck_persona_v1",
    "label": "queue_averse",
    "traits": {"queue_aversion": 9, "choice_inertia": 5},
},
```

Then add these assertions after `body` is created:

```python
self.assertIn("G01_API_01", body)
self.assertIn("queue_averse_v1", body)
self.assertIn("queue_aversion", body)
self.assertIn("stable behavioral preferences", body)
```

- [ ] **Step 2: Run the payload test and verify RED**

Run:

```bash
conda run -n otree_env python single_bottleneck/agents/test_deepseek_shadow_agent.py
```

Expected: FAIL because `AgentChoiceSet.__init__()` does not accept `agent_id` or `persona`.

- [ ] **Step 3: Add the Persona fields and prompt instruction**

Append these fields after `history` in `AgentChoiceSet`:

```python
agent_id: str = ""
persona: Mapping[str, object] = field(default_factory=dict)
```

Extend the system message in `build_chat_completion_payload`:

```python
"When a persona is present in the context, treat its 1-10 trait scores as "
"stable behavioral preferences for this Agent and remain consistent with them. "
```

Keep the existing legal-slot and JSON-only requirements unchanged.

- [ ] **Step 4: Run the DeepSeek unit tests and verify GREEN**

Run:

```bash
conda run -n otree_env python single_bottleneck/agents/test_deepseek_shadow_agent.py
```

Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Commit the context changes**

```bash
git add single_bottleneck/agents/deepseek_shadow_agent.py single_bottleneck/agents/test_deepseek_shadow_agent.py
git commit -m "feat: pass personas to DeepSeek agent"
```

### Task 3: Resolve Persona Per Agent in oTree

**Files:**
- Modify: `single_bottleneck/__init__.py:10-17,1171-1253`
- Create: `single_bottleneck/agents/test_persona_integration.py`

- [ ] **Step 1: Write failing active/off integration tests**

Create `single_bottleneck/agents/test_persona_integration.py`:

```python
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import single_bottleneck as app
from single_bottleneck.agents.personas import API_AGENT_PERSONA_SESSION_VAR


class PersonaIntegrationTests(unittest.TestCase):
    def make_group_and_player(self, mode="active"):
        session = SimpleNamespace(
            code="SESSION_A",
            vars={},
            config={
                "api_agent_mode": mode,
                "api_agent_count_per_group": 1,
            },
        )
        group = SimpleNamespace(
            session=session,
            round_number=1,
            id_in_subsession=1,
        )
        player = SimpleNamespace(
            participant=SimpleNamespace(
                vars={"assigned_group_label": "G01"},
            )
        )
        return group, player

    def test_active_mode_assigns_persona_and_passes_it_to_choice_set(self):
        group, player = self.make_group_and_player()
        captured = []

        def fake_choice_set(_group, _player, *, agent_id="", persona=None):
            captured.append((agent_id, persona))
            return SimpleNamespace()

        choice = SimpleNamespace(
            departure_slot=11,
            decision_source="deepseek_api",
            fallback_used=False,
            latency_ms=12,
            reason="test",
            raw_response_json="{}",
            context_json="{}",
        )

        with (
            patch.object(app, "api_agent_choice_set_for_group", side_effect=fake_choice_set),
            patch.object(app, "choose_shadow_departure", return_value=choice),
            patch.object(
                app.AgentDecision,
                "create",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            patch.object(app, "departure_minute_for_slot", return_value=474),
            patch.object(app, "minute_to_clock", return_value="07:54"),
        ):
            decisions = app.create_api_agent_decisions_for_group(
                group,
                [player],
                schedule={},
            )

        self.assertEqual(len(decisions), 1)
        self.assertIn(API_AGENT_PERSONA_SESSION_VAR, group.session.vars)
        self.assertEqual(captured[0][0], "G01_API_01")
        self.assertIn("persona_id", captured[0][1])

    def test_off_mode_does_not_initialize_persona_store(self):
        group, player = self.make_group_and_player(mode="off")

        decisions = app.create_api_agent_decisions_for_group(
            group,
            [player],
            schedule={},
        )

        self.assertEqual(decisions, [])
        self.assertNotIn(API_AGENT_PERSONA_SESSION_VAR, group.session.vars)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the integration tests and verify RED**

Run:

```bash
conda run -n otree_env python -m unittest single_bottleneck.agents.test_persona_integration
```

Expected: FAIL because active mode does not initialize Persona state and does not pass Persona data to the choice-set builder.

- [ ] **Step 3: Import the Persona helper into the app**

Add this import near the existing DeepSeek import in `single_bottleneck/__init__.py`:

```python
from .agents.personas import get_or_create_api_agent_persona
```

- [ ] **Step 4: Parameterize the choice-set builder**

Change the signature:

```python
def api_agent_choice_set_for_group(
    group: Group,
    reference_player: Player,
    *,
    agent_id: str = "",
    persona=None,
):
```

Pass the new fields into `AgentChoiceSet`:

```python
agent_id=agent_id,
persona=persona or {},
```

- [ ] **Step 5: Build an individualized choice set inside the Agent loop**

Remove the shared `choice_set` assignment before the loop. Inside the loop, immediately after constructing `agent_id`, add:

```python
persona = get_or_create_api_agent_persona(
    group.session,
    group_label,
    agent_id,
)
choice_set = api_agent_choice_set_for_group(
    group,
    reference_player,
    agent_id=agent_id,
    persona=persona,
)
```

Leave `mode == off`, AgentDecision creation, active settlement, and result display logic unchanged.

- [ ] **Step 6: Run the integration tests and verify GREEN**

Run:

```bash
conda run -n otree_env python -m unittest single_bottleneck.agents.test_persona_integration
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 7: Commit the oTree integration**

```bash
git add single_bottleneck/__init__.py single_bottleneck/agents/test_persona_integration.py
git commit -m "feat: assign personas to bottleneck agents"
```

### Task 4: Regression Verification

**Files:**
- Verify only; no planned source changes.

- [ ] **Step 1: Run all focused Agent tests**

```bash
conda run -n otree_env python single_bottleneck/agents/test_personas.py
conda run -n otree_env python single_bottleneck/agents/test_deepseek_shadow_agent.py
conda run -n otree_env python single_bottleneck/agents/test_rl_single_bottleneck.py
conda run -n otree_env python -m unittest single_bottleneck.agents.test_persona_integration
```

Expected: all test commands report `OK`.

- [ ] **Step 2: Run the existing focused settlement test**

```bash
conda run -n otree_env python -m unittest single_bottleneck.tests.AgentResultCostTests
```

Expected: `Ran 1 test` and `OK`.

- [ ] **Step 3: Compile the application**

```bash
conda run -n otree_env python -m compileall settings.py single_bottleneck
```

Expected: command exits with status 0 and reports no syntax errors.

- [ ] **Step 4: Inspect the final diff**

```bash
git diff --check HEAD~3..HEAD
git status --short
```

Expected: no whitespace errors. The pre-existing modifications to `single_bottleneck/Introduction.html`, `single_bottleneck/tests.py`, and `single_bottleneck/机制说明文档.md` remain untouched and uncommitted.
