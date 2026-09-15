# Dynamic Comprehension Information Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the disabled-toll comprehension item in `dynamic_bottleneck_round` with an I0/I1-specific information-boundary check, record the result without requiring a perfect score, and shorten warmup to three rounds.

**Architecture:** Add pure answer-key and scoring helpers in the existing oTree app module so browser feedback and stored scoring share the same condition-dependent answer. Store answer fields, score, and attempt count on the round-1 Player, mirror score metadata into participant vars for formal-round custom export, and use the existing warmup/formal boundary helpers to shift the boundary from raw round 6 to raw round 4.

**Tech Stack:** Python 3, oTree, unittest, oTree browser bots, HTML/CSS/JavaScript

---

### Task 1: Lock the new comprehension contract with failing tests

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`

- [x] Add tests proving I0 expects answer `a`, I1 expects answer `b`, completed wrong answers may continue, incomplete answers are rejected, the template contains no toll question, and the next button says `进入练习阶段`.
- [x] Run `conda run -n otree_env python -m unittest dynamic_bottleneck_round.tests.DynamicPresentationContextTests dynamic_bottleneck_round.tests.TemplateContractTests -v` and confirm failures for the missing answer key, missing server validation, and old toll template.

### Task 2: Implement the server-side answer key, validation, persistence, and export

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`

- [x] Add four answer fields plus score and attempt-count fields to `Player`.
- [x] Add pure helpers that return `b/a/c/a` for I0, `b/a/c/b` for I1, and calculate the number correct.
- [x] Configure `ComprehensionCheck` as a player form, require four completed answers plus one check attempt in `error_message`, and save the actual score in `before_next_page`.
- [x] Expose `comprehension_q4_correct` and condition-specific feedback to the template.
- [x] Add `comprehension_score` and `comprehension_attempts` to human rows in this app's custom export, while leaving virtual-agent rows blank.
- [x] Re-run the focused tests and confirm the backend contract passes.

### Task 3: Replace the template item and enforce the client-side gate

**Files:**
- Modify: `dynamic_bottleneck_round/ComprehensionCheck.html`

- [x] Rename radio inputs to the Player form field names and add the hidden attempt counter.
- [x] Replace the toll context and fourth question with the I0/I1 information-boundary content supplied by the backend.
- [x] Increment attempts on each check, unlock after all four questions have answers regardless of score, and update all transition text to the three-round practice phase.
- [x] Run the focused unit tests and inspect the template contract.

### Task 4: Update and verify the end-to-end bot flow

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`

- [x] Submit condition-specific answers and an attempt count from `PlayerBot`, including one completed 3/4 case that must proceed.
- [x] Assert the stored actual score, attempt count, and participant metadata after leaving the comprehension page.
- [x] Run `conda run -n otree_env python -m unittest discover -s dynamic_bottleneck_round -t . -q` (232 tests passed).
- [x] Run the configured I0 demo oTree bot session with 5 participants and all 3 bot cases; cover the I1 key and persistence path in focused unit tests. No database reset was run.

### Task 5: Shorten the warmup phase

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Modify: `settings.py`

- [x] Set `C.WARMUP_ROUNDS = 3`, keep `C.FORMAL_ROUNDS = 30`, and assert `C.NUM_ROUNDS == 33`.
- [x] Replace the five-value warmup sequence with low/medium/high representatives `(1.33, 2.67, 4.00)`.
- [x] Make first-formal-round RL/LLM test fixtures depend on `C.WARMUP_ROUNDS + 1` instead of raw round 6.
- [x] Update participant-facing and administrator-facing text from five to three warmup rounds.
- [x] Run all 232 tests under `dynamic_bottleneck_round`.

### Task 6: Review scope and regression risk

**Files:**
- Review: `dynamic_bottleneck_round/__init__.py`
- Review: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Review: `dynamic_bottleneck_round/tests.py`

- [x] Confirm only `dynamic_bottleneck_round` runtime files plus its `settings.py` description were changed and unrelated dirty-worktree files were preserved.
- [x] Confirm no database-reset command was run.
- [x] Review the diff for bypasses, stale toll wording, I0/I1 inversion, first-formal-round boundaries, and human/agent export separation.
