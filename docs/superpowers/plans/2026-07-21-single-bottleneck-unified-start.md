# Single Bottleneck Unified Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a session-wide synchronization gate before the first decision so all participants receive the full first-round choice time, with recovery through oTree's authenticated Session Monitor.

**Architecture:** Add a round-1-only `WaitPage` with `wait_for_all_groups=True` between the comprehension check and decision. Its subsession-level completion hook initializes every group's first decision deadline immediately before release. Add a selected-session link in the existing admin report that opens oTree's Session Monitor for manual recovery when a participant never reaches the gate.

**Tech Stack:** Python 3.10, oTree, oTree templates, native JavaScript, `unittest`, oTree bots.

---

### Task 1: Unified start contracts

**Files:**
- Modify: `single_bottleneck/tests.py`
- Create: `single_bottleneck/UnifiedStartWait.html`
- Modify: `single_bottleneck/__init__.py`

- [ ] **Step 1: Write failing page-sequence and template tests**

Import `Path` from `pathlib`, and import `UnifiedStartWait` and `page_sequence` from the app in `single_bottleneck/tests.py`. Then add tests that require the new page to be session-wide and located directly between `ComprehensionCheck` and `Decision`:

```python
class UnifiedStartTests(unittest.TestCase):
    def test_wait_page_is_session_wide_and_precedes_first_decision(self):
        self.assertTrue(UnifiedStartWait.wait_for_all_groups)
        self.assertEqual(
            page_sequence.index(UnifiedStartWait),
            page_sequence.index(ComprehensionCheck) + 1,
        )
        self.assertEqual(
            page_sequence.index(Decision),
            page_sequence.index(UnifiedStartWait) + 1,
        )

    def test_wait_page_explains_the_unified_start(self):
        template = Path('single_bottleneck/UnifiedStartWait.html').read_text(encoding='utf-8')
        self.assertIn('正在等待所有参与者', template)
        self.assertIn('将同时进入第 1 轮', template)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
eval "$(conda shell.zsh hook)" && conda activate otree_env
python -m unittest single_bottleneck.tests.UnifiedStartTests -v
```

Expected: FAIL because `UnifiedStartWait` and its template do not exist.

- [ ] **Step 3: Implement the round-1 session-wide wait page**

Add this class immediately after `ComprehensionCheck` in `single_bottleneck/__init__.py`:

```python
class UnifiedStartWait(WaitPage):
    wait_for_all_groups = True

    @staticmethod
    def is_displayed(player: Player):
        return player.round_number == 1 and access_allowed(player)

    @staticmethod
    def after_all_players_arrive(subsession: Subsession):
        for group in subsession.get_groups():
            ensure_decision_deadline(group)
```

Update the sequence:

```python
page_sequence = [
    Introduction,
    ComprehensionCheck,
    UnifiedStartWait,
    Decision,
    ResultsSync,
    Results,
]
```

Create `single_bottleneck/UnifiedStartWait.html` with oTree wait-page blocks. The participant copy must state that the page is waiting for everyone, the first round begins simultaneously, and the decision timer has not started. Use restrained blue/teal styling, one progress indicator, and no actionable button.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
eval "$(conda shell.zsh hook)" && conda activate otree_env
python -m unittest single_bottleneck.tests.UnifiedStartTests -v
```

Expected: PASS.

### Task 2: Administrator recovery link

**Files:**
- Modify: `participant_link_export_tests.py`
- Modify: `single_bottleneck/admin_report.html`

- [ ] **Step 1: Write the failing admin-report contract test**

Add:

```python
def test_single_bottleneck_admin_report_links_unified_start_control(self):
    template = Path('single_bottleneck/admin_report.html').read_text(encoding='utf-8')

    self.assertIn('open-unified-start-control', template)
    self.assertIn('/SessionMonitor/', template)
    self.assertIn('推进最慢参与者', template)
    self.assertIn('可能需要多次操作', template)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m unittest participant_link_export_tests.ParticipantLinkExportTests.test_single_bottleneck_admin_report_links_unified_start_control -v
```

Expected: FAIL because the control does not exist.

- [ ] **Step 3: Add the selected-session monitor control**

Add an anchor with `id="open-unified-start-control"` beside the existing export buttons. The explanatory note must say that administrators should use Session Monitor's `推进最慢参与者` action and may need to repeat it until absent participant slots reach the gate.

In the existing report script, cache the anchor and update it whenever `renderSession(sessionCode)` runs:

```javascript
const unifiedStartControl = document.getElementById('open-unified-start-control');

function updateUnifiedStartControl(sessionCode) {
  unifiedStartControl.href = sessionCode
    ? `/SessionMonitor/${encodeURIComponent(sessionCode)}`
    : '#';
  unifiedStartControl.setAttribute('aria-disabled', sessionCode ? 'false' : 'true');
}
```

Call `updateUnifiedStartControl(sessionCode)` from `renderSession`. Disable the link when there are no reports. Open Session Monitor in a new tab and do not expose REST credentials.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
python -m unittest participant_link_export_tests.ParticipantLinkExportTests.test_single_bottleneck_admin_report_links_unified_start_control -v
```

Expected: PASS.

### Task 3: Full regression and timing verification

**Files:**
- Modify: `single_bottleneck/tests.py`
- Verify: `single_bottleneck/__init__.py`
- Verify: `single_bottleneck/UnifiedStartWait.html`
- Verify: `single_bottleneck/admin_report.html`

- [ ] **Step 1: Extend the bot flow assertions**

In round 1, assert `UnifiedStartWait.is_displayed(self.player)` is true before submitting the comprehension check. In later rounds, assert it is false. Keep both existing `staggered` and `same_time` cases unchanged so the test proves the synchronization page does not alter decision or cost behavior.

- [ ] **Step 2: Run static checks**

Run:

```bash
python3 -m py_compile single_bottleneck/__init__.py single_bottleneck/tests.py single_bottleneck/toll_calibration.py participant_link_export_tests.py settings.py
git diff --check -- single_bottleneck/__init__.py single_bottleneck/UnifiedStartWait.html single_bottleneck/admin_report.html single_bottleneck/tests.py participant_link_export_tests.py
```

Expected: both commands exit 0.

- [ ] **Step 3: Run unit tests**

Run:

```bash
eval "$(conda shell.zsh hook)" && conda activate otree_env
python -m unittest participant_link_export_tests single_bottleneck.tests -v
```

Expected: PASS.

- [ ] **Step 4: Run the complete oTree bot regression**

Run:

```bash
OTREE_DATABASE_URL=sqlite:////tmp/single_bottleneck_unified_start.sqlite3 \
OTREE_ADMIN_PASSWORD=devpass \
otree test single_bottleneck_demo 5
```

Expected: both bot cases complete all 10 rounds.
