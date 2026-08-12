# Dynamic Bottleneck RL Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an administrator-controlled, local shadow-learning RL fallback for dynamic bottleneck DeepSeek Agents.

**Architecture:** A focused pure-Python RL module owns belief/Q-state updates and local action selection. The oTree app prepares an immutable RL candidate before starting the API worker, records its metadata with the Agent decision, and updates the group-scoped shadow state only after settlement. Existing API and heuristic behavior remains the fallback chain when the feature is disabled or RL cannot choose.

**Tech Stack:** Python standard library, oTree `participant.vars`/session config, unittest, existing HTML/JavaScript Create Session override.

---

### Task 1: Specify RL policy behavior with failing tests

**Files:**
- Create: `dynamic_bottleneck_round/agents/test_rl_fallback.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Test initial public prior, transition learning, legal deterministic action,
  persona influence, and negative-cost Q updates.
- [ ] Test feature-off legacy fallback, feature-on RL takeover, normal API shadow
  metadata, and current-capacity non-disclosure.
- [ ] Run focused tests and confirm failures are caused by missing RL interfaces.

### Task 2: Implement the isolated local learner

**Files:**
- Create: `dynamic_bottleneck_round/agents/rl_fallback.py`

- [ ] Add validated serializable state initialization and capacity observations.
- [ ] Add belief-state construction and tabular Q update using negative cost.
- [ ] Add legal action selection from expected cost, learned Q correction, and
  persona risk/inertia parameters.
- [ ] Run focused pure-policy tests to green.

### Task 3: Integrate shadow preparation and post-result learning

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/agents/deepseek_agent.py`

- [ ] Store RL state per group/Agent in the first human participant's vars.
- [ ] Prepare and freeze RL candidates before API worker submission.
- [ ] Replace API failure choices with RL candidates only when enabled.
- [ ] Update shadow state after every settled Agent outcome exactly once.
- [ ] Preserve the deterministic heuristic as disabled-mode and final-error fallback.

### Task 4: Add manager control and reporting

**Files:**
- Modify: `settings.py`
- Modify: `_templates/otree/CreateSession.html`
- Modify: `dynamic_bottleneck_round/admin_report.html`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] Add `rl_fallback_enabled=0` to both dynamic configs.
- [ ] Add a subordinate Create Session switch and submit it to the selected config.
- [ ] Add RL-enabled and RL-takeover counts to the report and actual decision source
  to custom export records.

### Task 5: Verify stability

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py` only if flow assertions require it.

- [ ] Run focused RL and Agent unit tests.
- [ ] Run the full dynamic unit suite and static compilation.
- [ ] Run dynamic Agent-active, dynamic Agent-off, and original single-bottleneck bots.
- [ ] Run `git diff --check`, inspect the final diff, and correct review findings.
