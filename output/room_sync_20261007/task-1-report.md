# Task 1: durable minimal sync journal

Status: DONE, awaiting the parent task's independent integration review.

## Scope and changes

Created only `dynamic_bottleneck_round/sync_journal.py` and `dynamic_bottleneck_round/test_sync_journal.py`, plus this requested report. No git commits, deployments, project database writes, page changes, grouping changes, or paid APIs.

The journal stores v1-compatible release state plus private configuration/participant audit data in one round record, and registered identity-bound shell tokens in a second table. Connections close after every operation, failures roll back, SQLite uses WAL with synchronous FULL, and BEGIN IMMEDIATE serializes writers across threads and independently reopened journal objects. Persistent file paths are required; schema mismatch and unrelated version-zero databases fail closed. Unchanged state avoids UPDATE, verified with real SQLite triggers across both tables.

The state API excludes actual capacity and private decision configuration. Snapshot capacity is exposed only at T or later for an authenticated I1 participant, including mixed-condition Sessions with code-to-value maps. Shell reloads preserve first render, ACK, T and deadline. Audit shell_token is the first accepted ACK's token, falling back to the first registered valid render. The first valid operative timestamp remains unchanged across retries.

Manual receipts persist before response return, use trusted arrival timestamps even if processing occurs after deadline, and survive reopening. A canonical manual remains immutable. A queued on-time manual may supersede a nonmanual late receipt before settlement; settled outcomes cannot be replaced. Unacknowledged/early/technical-anomaly choices reject with ValueError. Late/excluded exchanges report decision_received true and persist a nonmanual receipt with departure_minute None so the original automatic business rule can continue.

## API clarifications received during implementation

- `ensure_boot(boot_id)` atomically marks existing unsettled rounds with different nonempty process_boot_id as worker_restarted_during_round / TECHNICAL_ANOMALY. Settled rounds remain readable.
- `state(..., now_ts=None)` is a pure peek; explicit time advances phase.
- `exchange(..., processing_ts=None, pending_codes=(), failed_codes=(), crossed_codes=())` separates trusted request arrival from processing time. Admission and ACK use arrival; final release scheduling and response server_now_ts use processing.
- Information condition, capacity display, and level label accept scalar values or participant-code mappings. Allowed minutes remain a shared list.
- `audit()` returns shell_get_ingress_ts, shell_rendered_ts, ack_ts, operative_ts, shell_token, receipt, and page_index.
- Decision snapshots include decision_received, decision_kind, and the canonical receipt.

## TDD evidence

The TDD skill was read completely before test/implementation work. All 20 initial behavior tests were authored and run before creating production code. Expected RED was missing-feature assertions (rather than import errors). Full observed initial output follows.

Command, from `/Users/hybsmac/Python项目/economics-experiment/my_platform`:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal -v
```

```text
test_absence_excludes_and_late_shell_can_advance_without_manual (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_boot_change_marks_unsettled_and_preserves_settled (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_boundary_early_rejected_start_accepted_and_deadline_late (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_delayed_ack_uses_arrival_for_admission_processing_for_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_file_only_wal_full_and_schema_mismatch_fail_closed (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_i0_never_and_i1_only_after_release_expose_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_invalid_choices_raise_and_valid_replay_preserves_first_receipt (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_mark_anomaly_blocks_manual_and_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_operative_records_only_first_effective_time (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_preparation_failures_and_crossed_pages_are_anomalies (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_pure_peek_and_unchanged_status_do_not_update_any_table (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_queued_manual_uses_trusted_arrival_before_deadline (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_received_before_deadline_survives_reopen (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_reload_token_does_not_reset_ack_or_release_or_first_shell_times (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_rendered_missing_ack_is_anomaly_and_pending_drains (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_sessions_and_40_or_80_clients_share_exactly_one_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_settled_replays_accepted_choice_but_refuses_new_choice (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_threaded_reopen_keeps_first_choice_atomic (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_transaction_exception_does_not_advance_phase (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_unacknowledged_decision_and_wrong_identity_round_or_page_rejected (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL

======================================================================
FAIL: test_absence_excludes_and_late_shell_can_advance_without_manual (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_boot_change_marks_unsettled_and_preserves_settled (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_boundary_early_rejected_start_accepted_and_deadline_late (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_delayed_ack_uses_arrival_for_admission_processing_for_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_file_only_wal_full_and_schema_mismatch_fail_closed (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_i0_never_and_i1_only_after_release_expose_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_invalid_choices_raise_and_valid_replay_preserves_first_receipt (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_mark_anomaly_blocks_manual_and_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_operative_records_only_first_effective_time (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_preparation_failures_and_crossed_pages_are_anomalies (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_pure_peek_and_unchanged_status_do_not_update_any_table (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_queued_manual_uses_trusted_arrival_before_deadline (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_received_before_deadline_survives_reopen (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_reload_token_does_not_reset_ack_or_release_or_first_shell_times (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_rendered_missing_ack_is_anomaly_and_pending_drains (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_sessions_and_40_or_80_clients_share_exactly_one_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_settled_replays_accepted_choice_but_refuses_new_choice (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_threaded_reopen_keeps_first_choice_atomic (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_transaction_exception_does_not_advance_phase (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

======================================================================
FAIL: test_unacknowledged_decision_and_wrong_identity_round_or_page_rejected (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 17, in setUp
    self.assertIsNotNone(SyncJournal, 'durable SyncJournal is not implemented')
AssertionError: unexpectedly None : durable SyncJournal is not implemented

----------------------------------------------------------------------
Ran 20 tests in 0.001s

FAILED (failures=20)
```

The first implementation run passed 16/20 and rejected fixture tokens that had been deliberately registered under multiple independent Sessions. The journal had imposed global token uniqueness beyond the identity-scoped contract. Changing shell key/lookup scope to Session + round + participant + token produced 20/20 GREEN (0.204s). Wrong identity/round/token without matching registration still rejects.

Two later integration requirements were also RED first. Command:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_mixed_condition_session_reveals_only_each_participants_capacity dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_audit_token_prefers_first_ack_over_first_render_and_stays_private -v
```

```text
test_mixed_condition_session_reveals_only_each_participants_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_audit_token_prefers_first_ack_over_first_render_and_stays_private (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL

======================================================================
FAIL: test_mixed_condition_session_reveals_only_each_participants_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 301, in test_mixed_condition_session_reveals_only_each_participants_capacity
    self.assertEqual(result.get('actual_capacity_display'), capacity)
AssertionError: None != '2'

======================================================================
FAIL: test_audit_token_prefers_first_ack_over_first_render_and_stays_private (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 308, in test_audit_token_prefers_first_ack_over_first_render_and_stays_private
    self.assertEqual(self.journal.audit('s', 1, 'p').get('shell_token'), self.token)
AssertionError: None != 'aaaaaaaaaaaaaaaaaaaaaaaa'

----------------------------------------------------------------------
Ran 2 tests in 0.009s

FAILED (failures=2)
```

The first attempt to assert the absent audit field used direct indexing and emitted KeyError. Before implementation that assertion was corrected to .get(), obtaining the explicit expected failure above. Minimal mapping selection and private token audit then produced 22/22 GREEN (0.286s).

Queue reordering regression command:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_queued_on_time_manual_supersedes_late_receipt_before_settlement dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_settled_late_receipt_cannot_be_superseded_by_queued_manual -v
```

```text
test_queued_on_time_manual_supersedes_late_receipt_before_settlement (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL
test_settled_late_receipt_cannot_be_superseded_by_queued_manual (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok

======================================================================
FAIL: test_queued_on_time_manual_supersedes_late_receipt_before_settlement (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 213, in test_queued_on_time_manual_supersedes_late_receipt_before_settlement
    self.assertEqual(queued['decision_kind'], 'manual')
AssertionError: 'late' != 'manual'
- late
+ manual

----------------------------------------------------------------------
Ran 2 tests in 0.009s

FAILED (failures=1)
```

Minimal unsettled late-to-manual replacement produced the final GREEN. Full output:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal -v
```

```text
test_absence_excludes_and_late_shell_can_advance_without_manual (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_audit_token_prefers_first_ack_over_first_render_and_stays_private (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_boot_change_marks_unsettled_and_preserves_settled (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_boundary_early_rejected_start_accepted_and_deadline_late (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_delayed_ack_uses_arrival_for_admission_processing_for_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_file_only_wal_full_and_schema_mismatch_fail_closed (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_i0_never_and_i1_only_after_release_expose_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_invalid_choices_raise_and_valid_replay_preserves_first_receipt (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_mark_anomaly_blocks_manual_and_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_mixed_condition_session_reveals_only_each_participants_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_operative_records_only_first_effective_time (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_preparation_failures_and_crossed_pages_are_anomalies (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_pure_peek_and_unchanged_status_do_not_update_any_table (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_queued_manual_uses_trusted_arrival_before_deadline (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_queued_on_time_manual_supersedes_late_receipt_before_settlement (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_received_before_deadline_survives_reopen (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_reload_token_does_not_reset_ack_or_release_or_first_shell_times (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_rendered_missing_ack_is_anomaly_and_pending_drains (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_sessions_and_40_or_80_clients_share_exactly_one_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_settled_late_receipt_cannot_be_superseded_by_queued_manual (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_settled_replays_accepted_choice_but_refuses_new_choice (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_threaded_reopen_keeps_first_choice_atomic (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_transaction_exception_does_not_advance_phase (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_unacknowledged_decision_and_wrong_identity_round_or_page_rejected (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok

----------------------------------------------------------------------
Ran 24 tests in 0.248s

OK
```

## Self-review and integration concerns

All journal tests use TemporaryDirectory SQLite paths; the project database is untouched. Real tests cover 40/80 humans, independent Sessions, mixed I0/I1, exact T/T+60 boundaries, genuine absence vs rendered/pending/failed/crossed requests, bounded queue drain, reopen, parallel reopened writers, bad token/identity/page/round, illegal choices, replay after deadline/settlement, boot changes, rollback, and no-op UPDATE suppression.

No unresolved issue identified within the specified journal scope. Runtime must call ensure_boot once at startup; provide trusted arrival and processing timestamps; register a shell against its correct Decision page; supply pending/failed/crossed requests; and hold settlement until predeadline decisions drain. The journal deliberately preserves a settled automatic outcome, so the runtime's settlement guard is essential to avoid settling before a queued accepted manual choice is durable. All capacities remain private in storage/state and are selected by authenticated participant when exposed. Per-round JSON is intentionally minimal; 80-client correctness was tested, while HTTP/oTree throughput testing belongs to integration tasks. Independent review and broader regressions are owned by the parent task.

## Independent review fixes

The independent reviewer identified two important defects missed by the initial self-review; both are now corrected with regression-first evidence. No files outside the original two implementation/test files and this report were changed.

1. Decision admission rejection occurred inside the transaction context, whose exception handler rolled back an independently observed technical anomaly. Reproduction captured trusted arrival 159, processing 160, p ACKed, q rendered, and failed_codes=['q']; the persisted anomaly incorrectly remained empty. The new test also covers crossed pages and pending queue timeout, requires no receipt after rejection, and proves a later predeadline ACK cannot arm the anomalous round. The fix defers an admission ValueError until after state commit; invalid identity/minute validation and unexpected transaction exceptions still roll back. Focused RED/GREEN:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_rejected_decision_preserves_observed_anomaly_and_cannot_later_arm -v
```

```text
test_rejected_decision_preserves_observed_anomaly_and_cannot_later_arm (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL

======================================================================
FAIL: test_rejected_decision_preserves_observed_anomaly_and_cannot_later_arm (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 322, in test_rejected_decision_preserves_observed_anomaly_and_cannot_later_arm
    self.assertEqual(self.journal.state(session, 4)['technical_anomaly'], reason)
AssertionError: '' != 'participant_page_http_failure'
+ participant_page_http_failure

----------------------------------------------------------------------
Ran 1 test in 0.005s

FAILED (failures=1)
test_rejected_decision_preserves_observed_anomaly_and_cannot_later_arm (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok

----------------------------------------------------------------------
Ran 1 test in 0.015s

OK
```

2. The unconditional time > preparation cutoff + drain guard treated a never-rendered, nonpending missing participant as a queue failure. The pure expire_preparation helper instead excludes genuine absence. The fix makes ACK processing overrun an explicit ACK-only signal while status advances through the pure helper. A predeadline ACK processed beyond the drain still raises technical anomaly; genuine absence after drain gets excluded and receives one shared T=processing+5. Existing tests for pending/rendered/failed/crossed cases remain green. Regression RED:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_late_status_excludes_genuine_absence_even_beyond_drain -v
```

```text
test_late_status_excludes_genuine_absence_even_beyond_drain (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL

======================================================================
FAIL: test_late_status_excludes_genuine_absence_even_beyond_drain (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 333, in test_late_status_excludes_genuine_absence_even_beyond_drain
    self.assertEqual(result['technical_anomaly'], '')
AssertionError: 'shell_ack_queue_overrun' != ''
- shell_ack_queue_overrun
+


----------------------------------------------------------------------
Ran 1 test in 0.004s

FAILED (failures=1)
```

The post-fix full run passed 26 tests in 0.276s, including all concurrent response receipts matching the final persisted manual receipt.

The parent additionally clarified that durable journal state must identify protocol 2 (while the unchanged pure room_release helper defaults to 1). A regression asserted created and reopened state have protocol=2; it failed before the creation override and now passes. SQLite schema version remains 1 because this is the journal's first schema.

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_journal_state_identifies_durable_protocol_v2 -v
```

```text
test_journal_state_identifies_durable_protocol_v2 (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... FAIL

======================================================================
FAIL: test_journal_state_identifies_durable_protocol_v2 (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py", line 341, in test_journal_state_identifies_durable_protocol_v2
    self.assertEqual(created['protocol'], 2)
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 1 test in 0.002s

FAILED (failures=1)
```

Final GREEN:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal -v
```

```text
test_absence_excludes_and_late_shell_can_advance_without_manual (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_audit_token_prefers_first_ack_over_first_render_and_stays_private (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_boot_change_marks_unsettled_and_preserves_settled (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_boundary_early_rejected_start_accepted_and_deadline_late (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_delayed_ack_uses_arrival_for_admission_processing_for_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_file_only_wal_full_and_schema_mismatch_fail_closed (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_i0_never_and_i1_only_after_release_expose_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_invalid_choices_raise_and_valid_replay_preserves_first_receipt (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_journal_state_identifies_durable_protocol_v2 (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_late_status_excludes_genuine_absence_even_beyond_drain (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_mark_anomaly_blocks_manual_and_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_mixed_condition_session_reveals_only_each_participants_capacity (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_operative_records_only_first_effective_time (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_preparation_failures_and_crossed_pages_are_anomalies (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_pure_peek_and_unchanged_status_do_not_update_any_table (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_queued_manual_uses_trusted_arrival_before_deadline (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_queued_on_time_manual_supersedes_late_receipt_before_settlement (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_received_before_deadline_survives_reopen (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_rejected_decision_preserves_observed_anomaly_and_cannot_later_arm (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_reload_token_does_not_reset_ack_or_release_or_first_shell_times (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_rendered_missing_ack_is_anomaly_and_pending_drains (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_sessions_and_40_or_80_clients_share_exactly_one_release (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_settled_late_receipt_cannot_be_superseded_by_queued_manual (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_settled_replays_accepted_choice_but_refuses_new_choice (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_threaded_reopen_keeps_first_choice_atomic (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_transaction_exception_does_not_advance_phase (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok
test_unacknowledged_decision_and_wrong_identity_round_or_page_rejected (dynamic_bottleneck_round.test_sync_journal.SyncJournalTests) ... ok

----------------------------------------------------------------------
Ran 27 tests in 0.308s

OK
```

Final task status: DONE, pending same-reviewer re-review. Runtime dependencies and pending-decision settlement guard described above still apply.
