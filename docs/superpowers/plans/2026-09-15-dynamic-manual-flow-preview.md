# Dynamic Manual Flow Preview Implementation Plan

> **For agentic workers:** Implement inline with test-driven development. The user subsequently authorized one scoped Git commit for these changes.

**Goal:** Add an explicit, configurable Human-only preview path to the formal dynamic bottleneck Session creator without weakening formal experiment validation.

**Architecture:** Store preview intent in `flow_preview_enabled`. The creation page synchronizes preview participant count and I0/I1 selection into existing oTree form fields, while the backend branches before Room-label binding and validates preview composition separately from formal H/HA composition.

**Tech Stack:** Python, oTree, JavaScript, HTML, `unittest`, oTree bots.

---

### Task 1: Define and test the preview backend boundary

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `settings.py`

- [x] Add failing tests for the default preview flag and preview-mode behavior.
- [x] Add failing tests proving one Human-only group is allowed only in preview mode, while Agent or multi-group preview inputs fail.
- [x] Add `flow_preview_enabled=0` to the dynamic config and implement the preview-mode predicate and validator.
- [x] Branch first-round Session creation so preview creates one Human group and skips Room-label prebinding, while formal creation keeps its existing path.

### Task 2: Add and test the manual preview controls

**Files:**
- Modify: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`
- Modify: `_templates/otree/includes/DynamicSessionControls.html`

- [x] Add failing template-contract assertions for the preview switch, participant count, I0/I1 selector, and hidden config synchronization.
- [x] Add the separate manual preview panel below the quick configurations.
- [x] Synchronize the preview controls with `num_participants`, `group_treatment_spec`, `capacity_information_condition`, Agent counts, Room-label assignment, and `flow_preview_enabled`.
- [x] Ensure selecting any formal preset disables preview and restores the preset's existing values without overwriting the configured RL fallback preference.

### Task 3: Regression verification

- [x] Run all `dynamic_bottleneck_round` unit tests under conda environment `otree_env`.
- [x] Run the existing 5-participant dynamic demo bot flow.
- [x] Run static diff checks and inspect task-related changes.

### Task 4: Resolve the reported creation-page 500

- [x] Reproduce `GET /create_session` and read the server traceback.
- [x] Confirm the installed oTree package supplies `BaseAdminRegular.html`, not `BaseAdmin.html`.
- [x] Write a failing template contract test, change the custom creation page to inherit `BaseAdminRegular.html`, and verify the test passes.
- [x] Verify the authenticated creation page returns HTTP 200 and includes the manual-preview controls.
- [x] Add `flow_preview_enabled` to custom export rows to distinguish preview data from formal samples.

## Verification record

- Dynamic app unit suite: 239 tests passed.
- JavaScript compilation check: passed.
- Dynamic demo bot flow: 5 participants, all 33 rounds, passed.
- Authenticated `GET /create_session`: HTTP 200 after the template fix.
- A real WebSocket creation request against a temporary database copy created a 1-person I1 Session with preview flag 1, no Agent, and no Room label. The real database was not used for that test.
