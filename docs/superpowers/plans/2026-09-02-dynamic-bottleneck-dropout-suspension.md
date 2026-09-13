# Dynamic Bottleneck Dropout Suspension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suspend participants after two consecutive missed decisions, keep them in congestion through an auditable proxy choice, and stop them delaying later round starts.

**Architecture:** Store cross-round dropout state and per-round audit snapshots in `participant.vars` to avoid a database schema change. Centralize manual/missed/suspended transitions in helper functions, then make round synchronization count only non-suspended participants while assigning suspended proxy choices as soon as a round starts.

**Tech Stack:** Python, oTree pages/models, `unittest`, oTree bots.

---

### Task 1: Lock State Transitions With Tests

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`

- [x] Add tests proving the first miss keeps a participant active, the second consecutive miss suspends them, duplicate processing is idempotent, and a manual decision resets the streak.
- [x] Add a test proving suspended participants are excluded from round readiness and receive a proxy based on their last manual choice.
- [x] Run the focused tests and confirm they fail because the new helpers and behavior do not exist.

### Task 2: Implement Suspension and Proxy Selection

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/RoundStartSync.html`

- [x] Add participant-state helpers and a two-miss suspension threshold.
- [x] Record successful manual choices and use the latest one for timeout, disconnect, and suspended proxy decisions.
- [x] Exclude suspended participants from the readiness denominator and prefill their proxy decisions when a round starts.
- [x] Preserve the existing manual recovery gate and clear suspension only after explicit confirmation.
- [x] Run focused tests until green.

### Task 3: Add Per-Round Audit Export

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [x] Add export columns for consecutive missed decisions, suspension state, and automatic-choice strategy.
- [x] Populate human rows from a round-keyed audit snapshot and leave Agent rows neutral.
- [x] Verify export tests pass without adding oTree model fields.

### Task 4: Regression Verification

**Files:**
- Test: `dynamic_bottleneck_round/tests.py`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [x] Run Python compilation and `git diff --check`.
- [x] Run the dynamic app unit and Agent suites.
- [x] Run the complete `dynamic_bottleneck_round_demo` bot flow.
- [x] Run targeted `single_bottleneck` compatibility tests.
