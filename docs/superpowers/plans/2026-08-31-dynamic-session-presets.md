# Dynamic Session Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable administrator shortcuts that configure common dynamic bottleneck Human/Agent group structures from both ordinary and Room Session creation pages.

**Architecture:** Override oTree's shared `CreateSessionForm.html` so both entry points render one form implementation. Place preset and Agent controls in a focused partial whose JavaScript writes to the original oTree config fields, preserving the complete advanced configuration table and backend validation.

**Tech Stack:** oTree templates, vanilla JavaScript, CSS, Python `unittest` static template checks, oTree bots.

---

### Task 1: Lock the shared-form contract with failing tests

**Files:**
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Add tests that require `_templates/otree/includes/CreateSessionForm.html` to include the shared dynamic controls partial.
- [ ] Add tests that require the controls partial to define the four presets and the B values `35`, `20`, `G01:api=0,rl=0;G02:api=5,rl=0`, `active`, and `session`.
- [ ] Update existing Agent-control tests to inspect the shared partial instead of `_templates/otree/CreateSession.html`.
- [ ] Run the focused tests and verify they fail because the shared templates do not yet exist.

Run:

```bash
conda run -n otree_env python -m unittest \
  dynamic_bottleneck_round.agents.test_dynamic_agent_integration.DynamicAgentAdminTemplateTests
```

Expected: failure for missing shared form/control templates.

### Task 2: Move Session controls into the reusable form

**Files:**
- Create: `_templates/otree/includes/CreateSessionForm.html`
- Create: `_templates/otree/includes/DynamicSessionControls.html`
- Modify: `_templates/otree/CreateSession.html`

- [ ] Copy oTree's standard Session form into the project override without changing its websocket creation behavior.
- [ ] Include `DynamicSessionControls.html` immediately after the participant count block so all custom inputs remain inside `<form id="form">`.
- [ ] Move the current single-bottleneck and dynamic Agent controls into the shared partial.
- [ ] Keep `_templates/otree/CreateSession.html` as a small page shell that includes the shared form.
- [ ] Run the focused tests and verify the shared-form and existing Agent-control assertions pass.

### Task 3: Implement shortcut application and visual summary

**Files:**
- Modify: `_templates/otree/includes/DynamicSessionControls.html`

- [ ] Render four compact scheme buttons only for dynamic bottleneck configs.
- [ ] Implement a field-source map for `cohort_size`, `grouping_enabled`, `manual_grouping_spec`, `group_agent_spec`, `dynamic_capacity_sequence_scope`, API settings, and RL settings.
- [ ] Apply the selected preset to the original editable oTree fields and the visible participant count/Agent controls.
- [ ] Display a two-group composition summary and mark B as recommended without silently applying it before a click.
- [ ] Keep the complete Configure Session panel editable after applying a preset.
- [ ] Run focused tests and `git diff --check`.

### Task 4: Regression verification

**Files:**
- Verify: `_templates/otree/includes/CreateSessionForm.html`
- Verify: `_templates/otree/includes/DynamicSessionControls.html`
- Verify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Run the full dynamic Agent unit suite.
- [ ] Compile the dynamic app and settings.
- [ ] Run `otree test dynamic_bottleneck_round_demo 5` against a temporary SQLite database.
- [ ] Verify the Room page receives the controls through the shared form include.
- [ ] Report the remaining label-to-group assignment limitation explicitly.
