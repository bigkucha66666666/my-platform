# Dynamic Bottleneck Warmup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two isolated warmup rounds before the existing 60-round dynamic bottleneck experiment, with explicit start/end prompts and no contamination of formal exports, payoff, or Agent learning.

**Architecture:** Keep oTree's round model and increase the app total to 62 rounds. Central phase helpers map raw rounds 1–2 to warmup rounds and raw rounds 3–62 to formal rounds 1–60; capacity generation, exports, reports, payment, templates, and Agent history all use those helpers.

**Tech Stack:** Python 3, oTree, Django-style oTree templates, `unittest`, oTree bots.

---

### Task 1: Define round phases and warmup capacity

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `settings.py`

- [ ] Add failing tests asserting 2 warmup rounds, 60 formal rounds, 62 total rounds, raw/formal mapping, and `dynamic_warmup_capacity=2` validation.
- [ ] Run the focused unit tests and confirm failures are caused by missing phase helpers.
- [ ] Add `WARMUP_ROUNDS`, `FORMAL_ROUNDS`, phase/mapping helpers, and warmup capacity parsing; keep `NUM_ROUNDS=62`.
- [ ] Generate only 60 formal capacity records and apply fixed capacity 2 to raw rounds 1–2.
- [ ] Re-run focused tests until green.

### Task 2: Add explicit transition pages and participant-facing numbering

**Files:**
- Create: `dynamic_bottleneck_round/WarmupStart.html`
- Create: `dynamic_bottleneck_round/FormalStart.html`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/RoundStartSync.html`
- Modify: `dynamic_bottleneck_round/Decision.html`
- Modify: `dynamic_bottleneck_round/Results.html`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] Add failing tests for `WarmupStart` only on raw round 1 and `FormalStart` only on raw round 3.
- [ ] Add template tests for the start/end messages and warmup/formal round labels.
- [ ] Implement both pages and insert them around the existing synchronized round flow.
- [ ] Pass shared phase labels into round-start, decision, recovery, and results templates.
- [ ] Re-run page and template tests until green.

### Task 3: Isolate payoff, export, report, and Agent learning

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] Add failing tests proving warmup payoff is zero and final payoff sums formal rounds only.
- [ ] Add failing tests proving custom export and admin rows omit raw rounds 1–2 and renumber formal rounds 1–60.
- [ ] Add failing tests proving warmup does not update LLM memory, RL shadow, or independent RL state, and formal round 1 receives no warmup history.
- [ ] Implement the isolation guards and formal round renumbering in export/report helpers.
- [ ] Re-run dynamic app and Agent tests until green.

### Task 4: Update bot flow and complete regression

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `settings.py`

- [ ] Update the bot to submit `WarmupStart` in raw round 1 and `FormalStart` in raw round 3.
- [ ] Assert participant-facing labels, fixed warmup capacity, formal capacity variability, and formal-only Agent observation counts.
- [ ] Run Python compilation and `git diff --check`.
- [ ] Run the complete dynamic unit/Agent suite.
- [ ] Run the 62-round `dynamic_bottleneck_round_demo` oTree bot.
- [ ] Run the existing `single_bottleneck` compatibility tests.
