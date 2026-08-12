# Dynamic Agent Prefetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow participants to enter `ResultsSync` immediately while dynamic-bottleneck Agent API requests finish in the background.

**Architecture:** Prepare Agent request payloads on the oTree request thread when a round starts, submit only pure network work to a process-level executor, and collect completed choices during result polling. Existing result locking remains responsible for one-time persistence and cost calculation.

**Tech Stack:** Python 3.10, `concurrent.futures`, oTree Pages, `unittest`.

---

### Task 1: Specify non-blocking task behavior

**Files:**
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Add a test proving that starting the same group-round twice returns one Future and invokes one generation task.
- [ ] Add a test proving that an unfinished Future makes collection return a pending sentinel without blocking.
- [ ] Add a test proving that a finished Future is converted to records once and removed from the registry.
- [ ] Run `python -m unittest dynamic_bottleneck_round.agents.test_dynamic_agent_integration` and verify the new tests fail because the task APIs do not exist.

### Task 2: Implement Agent prefetch registry

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] Add one bounded process-level executor and a lock-protected group-round task registry.
- [ ] Split Agent handling into request preparation, background choice generation, and request-thread record construction.
- [ ] Implement idempotent start and non-blocking collect helpers.
- [ ] Keep `create_api_agent_decisions_for_group()` as a synchronous compatibility wrapper for existing tests and tools.
- [ ] Run the focused Agent unit tests and verify they pass.

### Task 3: Integrate round start and result polling

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/ResultsSync.html`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] Start prefetch after `maybe_start_round()` reports that the round has started.
- [ ] Make `_set_results_locked()` return without setting `results_ready` while Agent choices are pending.
- [ ] Change waiting-page copy so it covers both human synchronization and Agent calculation.
- [ ] Extend bot assertions to confirm the waiting page remains valid before completion.

### Task 4: Regression verification

**Files:**
- Test: `dynamic_bottleneck_round/tests.py`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Run Python compilation and `git diff --check`.
- [ ] Run focused unit tests.
- [ ] Run dynamic bottleneck bots with Agent off.
- [ ] Run dynamic bottleneck bots with Agent active and deterministic fallback.
- [ ] Run the original `single_bottleneck_demo` bot regression.
