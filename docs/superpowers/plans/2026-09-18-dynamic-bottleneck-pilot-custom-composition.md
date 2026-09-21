# Dynamic Bottleneck Pilot Custom Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user chose inline execution and explicitly prohibited Git commits, which overrides the skill's commit steps.

**Goal:** Add an isolated pilot Session mode with configurable Human, API and RL counts per treatment group, while preserving formal presets, Room labels and analyzable records.

**Architecture:** Parse one strict pilot group specification into the existing treatment and per-group Agent metadata; reuse the formal matrix and sequential Room assignment. The admin creation page writes that specification and the Human seat total. Export and report read the stored plan but count actual participation separately.

**Tech Stack:** Python/oTree, JavaScript in the oTree admin template, unittest, oTree Bot.

---

## File map

- `settings.py`: declare pilot mode config defaults for dynamic bottleneck only.
- `dynamic_bottleneck_round/__init__.py`: strict parser, Session setup, export fields, report counts.
- `_templates/otree/includes/DynamicSessionControls.html`: pilot-mode group builder and field synchronization.
- `dynamic_bottleneck_round/tests.py`: backend regression tests.
- `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`: admin-template contract tests.
- `dynamic_bottleneck_round/admin_report.html`: display plan and actual counts.

### Task 1: Strict pilot configuration and matrix

- [ ] Add tests in `dynamic_bottleneck_round/tests.py` for `parse_pilot_group_spec('G01:H-I0,human=12,api=0,rl=0;G02:HA-I0,human=8,api=10,rl=10')` yielding group totals 12 and 28; reject H agents, HA without agents, Human 0, total 31, API/RL 11, duplicate/noncontiguous labels and malformed integers. Test pilot+preview conflict, as well as formal fixed parsing unchanged.
- [ ] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.tests.PilotCompositionTests -v`; expect missing parser failure.
- [ ] Add `pilot_mode_enabled=0` and `pilot_group_spec=''` to `DYNAMIC_BOTTLENECK_ROUND_COMMON` in `settings.py`. Add `is_pilot_session`, `parse_pilot_group_spec` and pilot branch to `configure_formal_treatments` in `dynamic_bottleneck_round/__init__.py`. Parse exact `GNN:TREATMENT,human=N,api=N,rl=N`; reuse `TREATMENT_DEFINITIONS` only for treatment metadata, override three counts. Use the maximum configured API/RL count for global validation, but retain each group's actual counts in `GROUP_AGENT_COUNTS_SESSION_VAR`. Reject pilot+preview conflict. In composition validation, compare actual counts to the parsed plan, with a pilot-specific error.
- [ ] Rerun the focused test; expect pass. Run existing formal grouping and Room tests; expect pass.

### Task 2: Creation-page pilot controls

- [ ] Add static-template tests in `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py` asserting controls `pilot-mode-enabled`, `pilot-group-count`, `pilot-group-rows`, `pilot-group-totals`, source fields `pilot_mode_enabled` and `pilot_group_spec`, and a create-button validity gate. Run the focused test; expect failure.
- [ ] Add a separate pilot builder in `_templates/otree/includes/DynamicSessionControls.html`, visible only for `dynamic_bottleneck_round_prod`. Each row has treatment, Human (1–30), API (0–10), RL (0–10); show per-group total, total Human seats, Room Pxxx range. On pilot enable, clear preview and formal selected state; on formal preset/preview enable, clear pilot flag and spec. Synchronize `pilot_group_spec`, `num_participants`, sequential Room settings and Agent modes. Block creation when any H group has Agents, HA lacks Agents, group total exceeds 30, or total Human exceeds the Room label count (currently 100); backend remains authoritative.
- [ ] Rerun focused static tests and a JavaScript syntax check via extracting the `<script>` contents to a temporary file then `node --check`; expect pass.

### Task 3: Export and admin observability

- [ ] Add tests to `dynamic_bottleneck_round/tests.py` proving Human/Agent rows share pilot flag and planned counts, Human has Room label/access flag, Agent has blanks; admin round row distinguishes admitted Human, manual choices, automatic choices, no choice and API/RL decision records. Run focused tests; expect failure.
- [ ] Extend `EXPORT_HEADERS` and both export row functions. Read planned counts from group treatment metadata, not from actual decisions. In `build_admin_report_rows`, count actual Human entry from participant `access_granted`, classify choice by `decision_source` only for players with departure choices, and keep Agent record counts separate. Extend `vars_for_admin_report` with pilot flag and planned group rows. Render these in `dynamic_bottleneck_round/admin_report.html`.
- [ ] Rerun export/report tests; expect pass. Check formal rows retain pilot=false and preview rows retain preview=true.

### Task 4: Full verification

- [ ] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.tests dynamic_bottleneck_round.agents.test_dynamic_agent_integration -v`; expect all pass.
- [ ] Run relevant oTree Bot through `/opt/anaconda3/envs/otree_env/bin/otree test dynamic_bottleneck_round`; expect pass without external LLM calls. If command structure differs, use repository's existing Bot invocation.
- [ ] Inspect `git diff --check` and focused diff; verify no changes to survey, payment or unrelated artifacts and no Git commit.

## Self-review

The four tasks cover config and strict parsing, creation UI and Room behavior, record/report separation, and regression verification. Preview and formal mode isolation are explicit. No task requires network or external API calls; no Git commit is authorized.

## Execution result (2026-09-18)

- Strict parser, runtime group/Agent metadata, sequential Room labels, pilot creation controls, exports and report are implemented.
- 266 relevant unit tests pass. A real two-group pilot Session was created with H-I0 (1 Human) and HA-I1 (1 Human, 1 LLM, 1 RL), and both groups received the same S01 service rate for the checked round while retaining distinct I0/I1 conditions.
- Full oTree Bot flows pass for pilot H-I0 (1 Human) and HA-I1 (1 Human, 1 RL), without calling an external LLM API. An unanswered single-Human pilot exports all 30 formal rows.
- JavaScript syntax and targeted `git diff --check` pass. Interactive admin-page browser QA could not run because the local browser CLI could not bootstrap under network restrictions and the sandbox blocked binding a local server port. No Git commit was made.
