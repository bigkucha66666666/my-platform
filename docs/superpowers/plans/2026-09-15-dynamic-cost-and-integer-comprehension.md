# Dynamic Cost and Integer Comprehension Implementation Plan

> **For agentic workers:** Implement inline with test-driven development. The user subsequently authorized one scoped Git commit for these changes.

**Goal:** Align the dynamic bottleneck late-arrival cost with the original single-bottleneck scenario and make comprehension question 3 use an integer-only example.

**Architecture:** Keep the production cost calculation unchanged and alter its single late-cost constant. Isolate the comprehension example from stochastic-distribution parameters by returning a fixed, pedagogical integer example through the existing `comprehension_queue_example` interface.

**Tech Stack:** Python, oTree, `unittest`, oTree bots, HTML templates.

---

### Task 1: Lock the approved late-arrival cost

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [x] Change existing cost assertions from 5 to 3 and update expected late/total components.
- [x] Run the focused tests and confirm they fail because production still uses 5.
- [x] Set `C.LATE_COST_PER_MINUTE = 3`.
- [x] Re-run the focused tests and confirm they pass.

### Task 2: Replace the fractional comprehension example

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [x] Replace the mean-based example test with assertions for capacity 2, load 6, wait 2, and arrival minute 482.
- [x] Run the focused test and confirm it fails against the current mean-based example.
- [x] Make `comprehension_queue_example` return the fixed integer teaching inputs while retaining the existing calculation function.
- [x] Re-run the focused test and confirm it passes.

### Task 3: Regression verification

**Files:**
- Verify: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Verify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [x] Run all `dynamic_bottleneck_round` unit tests in conda environment `otree_env`.
- [x] Run the `dynamic_bottleneck_round_demo` oTree bot test.
- [x] Run scoped `git diff --check` and inspect only the task-related diff.

## Verification record

- `conda run -n otree_env python -m unittest discover -s dynamic_bottleneck_round -t . -q`: 239 tests passed after all related changes.
- `conda run -n otree_env otree test dynamic_bottleneck_round_demo 5`: passed.
