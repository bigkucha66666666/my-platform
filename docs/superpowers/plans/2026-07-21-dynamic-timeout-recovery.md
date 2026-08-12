# Dynamic Timeout Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit participant-confirmed recovery gate after timeout or inferred disconnect.

**Architecture:** Keep the current deadline fallback and locked automatic choice. Add one page that reads the participant dropout state and clears it only after an explicit form submission; persist round-level event and recovery metadata for export.

**Tech Stack:** oTree, Python, oTree templates, unittest/bots.

---

### Task 1: Recovery state tests

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] Add tests for timeout persistence, explicit recovery, historical-state preservation, page ordering, template behavior, and export fields.
- [ ] Run the focused tests and confirm they fail because the recovery API and page do not exist.

### Task 2: Recovery state implementation

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `settings.py`

- [ ] Add round-level dropout/recovery fields and participant recovery history fields.
- [ ] Replace automatic disconnect restoration with an explicit confirmation helper.
- [ ] Insert `RecoveryGate` after `Decision` and ensure the current automatic choice remains locked.
- [ ] Add recovery metadata to custom export.
- [ ] Run focused tests and confirm they pass.

### Task 3: Recovery page

**Files:**
- Create: `dynamic_bottleneck_round/RecoveryGate.html`

- [ ] Add a no-timeout, no-JavaScript confirmation page explaining the locked current-round choice.
- [ ] Run template contract tests.

### Task 4: Regression verification

**Files:**
- Test: `dynamic_bottleneck_round/tests.py`

- [ ] Run Python compilation and all dynamic app unit tests.
- [ ] Run the normal 5-person oTree bot flow.
- [ ] Run `git diff --check` and verify no single-bottleneck runtime files were changed.

