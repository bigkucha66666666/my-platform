# Dynamic Capacity 20+40 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `dynamic_bottleneck_round` to a reproducible 60-round Random-to-Markov experiment with an optional exact manual capacity sequence.

**Architecture:** Extend the existing immutable capacity config and sequence generator instead of adding a second scheduling path. Keep round records as the single source used by groups, players, reports, Human pages, and Agents; make participant-facing disclosure depend only on reveal timing, not generation internals.

**Tech Stack:** Python, oTree, unittest/oTree bots, HTML templates.

---

### Task 1: Configuration and sequence generation

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] Add failing tests for `phased_markov`, transition-matrix validation, reproducibility and manual sequence validation.
- [ ] Run focused unittest classes and verify failures are caused by unsupported modes and missing fields.
- [ ] Extend `DynamicCapacityConfig`, parsing, generation and round-record probability logic.
- [ ] Run focused tests until green without changing legacy `iid` and `balanced_shuffle` behavior.

### Task 2: 60-round defaults and disclosure

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/Introduction.html`
- Modify: `dynamic_bottleneck_round/Decision.html`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `settings.py`

- [ ] Add failing assertions for 60 rounds, after-decision reveal, hidden probabilities and a service-rate-independent manual toll.
- [ ] Change `C.NUM_ROUNDS`, dynamic session defaults and payoff metadata.
- [ ] Remove probabilities and phase details from participant-facing Introduction and Decision templates.
- [ ] Verify Human and Agent decision contexts do not contain the current capacity or transition matrix.

### Task 3: Regression and flow verification

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py` only if assertions must reflect the new public-information contract.

- [ ] Run Python compilation and focused unit tests.
- [ ] Run all dynamic app unit and Agent tests.
- [ ] Run `otree test dynamic_bottleneck_round_demo 5` through all 60 rounds.
- [ ] Run `otree test single_bottleneck_demo 5` to prove isolation.
- [ ] Run `git diff --check` and inspect the final diff for accidental unrelated edits.
