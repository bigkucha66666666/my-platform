# Task 6 final correction wave — 2026-10-08

## Outcome

The two Important whole-review findings are fixed, together with the complete-body-to-pending ownership race identified during this wave. The frontend aborts a control request whose headers or JSON body has not finished within 10 seconds. A rejected decision's status probe has its own finite bound. A newly persisted timely manual choice that finishes after deadline plus drain remains canonical but marks the round `decision_control_queue_overrun` before its worker releases pending ownership. Complete valid control bodies now claim pending ownership within the same short registry boundary used by native settlement closure.

Production changes: `_static/dynamic_bottleneck_round/room_release.js`, `dynamic_bottleneck_round/sync_journal.py`, `dynamic_bottleneck_round/sync_transport.py`, and `dynamic_bottleneck_round/sync_runtime.py`. Focused tests: `test_room_release_timeout.cjs` (new), `test_room_release.cjs`, `test_sync_journal.py`, `test_sync_transport.py`, and `test_room_sync_framework.py`. No commit, deployment, browser farm, paid API, production database, paper change, or full33 run.

## Design and race semantics

- The v2 browser's `fetchControl` owns one `AbortController`, one timer, and one bounded clock sample for each fetch/JSON pair. The 10-second timer starts before fetch, covers response headers and `response.json()`, and aborts on timeout; any failure aborts and clears its timer and clock sample before the single-flight flag can be freed. The ordinary retry keeps the first `savedChoice`. The decision 400-status probe stays within the same single-flight attempt and gets its own 10-second fetch/body bound. Promise-race loser completions cannot invoke `liveRecv`, schedule another native form, or affect clock estimates. v1 Live behavior and the server's authority over release/deadline remain unchanged. The v2 clock map is capped at 32 entries, with timed-out samples deleted.
- `SyncJournal.exchange` checks overrun only when it actually creates a new `manual` receipt (including valid replacement of an earlier late receipt), with arrival before deadline and processing strictly beyond deadline plus drain. It stores the original arrival and chosen minute before marking the anomaly in the same transaction. An old manual replay, fresh late receipt, settled round, or already-anomalous round does not create a new overrun fault. A private optional return value gives the worker only the created manual's limit; no new public response field or private capacity value is exposed.
- Journal processing time is sampled before the write. The control worker therefore samples again immediately after `exchange` returns, covering SQLite UPDATE/commit and lock waits that occurred after the earlier sample. It latches `decision_control_queue_overrun` and attempts durable anomaly persistence while its pending ticket remains registered. If the anomaly write fails, the existing dirty fault and native interlock remain; the bounded flusher's ownership and retry policy are unchanged. No SQLite work occurs under a registry lock or on the control main loop. A blocked native business path may resume after pending has cleared, but sees the durable anomaly and excludes the round from healthy settlement/export while preserving the manual receipt.
- The complete-body path parses bounded JSON, verifies the signed capability, validates choice, takes a nonblocking admission slot, and registers pending inside `runtime.control_body_boundary()`. That short boundary uses the same registry lock as `close_control_round`; it captures the trusted body-completion timestamp while held. It performs no disk, ORM, await, or executor operation under the lock. Worker submission and its completion callback are attached before the timed `_body` task returns to `asyncio.wait_for`'s caller, so a delayed relay, cancellation, or timeout after ownership transfer cannot drop accepted work. Malformed, unauthorized, and invalid-choice bodies receive their existing rejection responses without a pending ticket or Session fault. A settled historical replay remains subject to the Journal's settled semantics.

## Observed RED evidence

Commands below ran from `/Users/hybsmac/Python项目/economics-experiment/my_platform`. The first three direct unittest probes used each test's disposable SQLite journal; they preceded the final isolated parent/child matrix. All REDs below are behavior assertions against real code, not source-string checks.

1. Before Journal correction:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal.SyncJournalTests.test_novel_manual_processing_overrun_retains_receipt_and_faults
```

Exit 1, `Ran 1 test in 0.006s; FAILED (failures=1)`: `AssertionError: '' != 'decision_control_queue_overrun'` at the persisted-state assertion. The real SQLite receipt was manual with the original ingress time.

2. Before worker completion correction, with the actual SQLite commit held until after deadline plus drain while a business thread was blocked:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_transport.TransportTests.test_decision_commit_overrun_faults_before_pending_clears_while_business_blocked
```

Exit 1, `Ran 1 test in 0.576s; FAILED (failures=1)`: `AssertionError: '' != 'decision_control_queue_overrun'` after worker completion and pending release.

3. Before the complete-body admission correction, `asyncio.wait_for`'s relay was withheld after the complete body returned:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_transport.TransportTests.test_complete_body_owns_pending_before_wait_for_relay
```

Exit 1, `Ran 1 test in 0.016s; FAILED (failures=1)`: `AssertionError: True is not false : complete valid body must own the final settlement boundary`. The native final boundary could close while the completed valid body had no pending ticket.

4. The Node VM executes the real production script with controlled timers/fetch. The initial test shape encountered an undefined `signal` before reaching an assertion; it was adjusted to assert the missing abort behavior, then run against root's saved pre-fix production script:

```sh
node dynamic_bottleneck_round/test_room_release_timeout.cjs .superpowers/sdd/task6-baseline/_static/dynamic_bottleneck_round/room_release.js
```

Exit 1 with `AssertionError [ERR_ASSERTION]: hanging ACK headers must be aborted within a finite bound`, `false !== true` at `test_room_release_timeout.cjs:74`. The pre-fix script left the hanging ACK and queued choice stranded.

5. The body-admission refactor initially changed the response to an invalid ASGI message. A focused assertion recorded `503 != 400` (`Ran 1 test in 0.013s; FAILED (failures=1)`, exit 1) for:

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_transport.TransportTests.test_rejects_bad_signatures_origin_method_and_body
```

After restoring the existing 400 classification, this command returned `Ran 1 test in 0.013s; OK`, exit 0. The final matrix below was rerun after that production change.

## Final GREEN verification

Every Python matrix parent forced SQLite/in-memory-off and changed to a disposable directory before importing the application. Framework children also use disposable directories and real frozen oTree/SQLite, with server package precedence retained. Only the intended pause/failure boundaries are injected.

Installed oTree 6 command:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_journal","dynamic_bottleneck_round.test_sync_transport","dynamic_bottleneck_round.test_room_sync_framework"]); result=unittest.TextTestRunner(verbosity=1).run(suite); sys.exit(not result.wasSuccessful())'
```

Exit 0, `Ran 96 tests in 34.466s; OK (skipped=1)`.

Frozen server oTree 5 command:

```sh
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.append(str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_journal","dynamic_bottleneck_round.test_sync_transport","dynamic_bottleneck_round.test_room_sync_framework"]); result=unittest.TextTestRunner(verbosity=1).run(suite); sys.exit(not result.wasSuccessful())'
```

Exit 0, `Ran 96 tests in 34.437s; OK (skipped=1)`. The single skip in each matrix is the explicit full33 acceptance owned by root.

Node behavior commands:

```sh
node dynamic_bottleneck_round/test_room_release.cjs
node dynamic_bottleneck_round/test_room_release_timeout.cjs
```

Both exit 0. Outputs: `room release admission and delayed-response clock checks passed`; `room release timeout checks passed`. The new VM cases cover stalled headers, stalled JSON, stalled rejection-probe JSON, retry of the immutable first choice, and late completions that cannot schedule controls or native submission.

The real native framework case `test_worker_overrun_after_native_block_keeps_manual_receipt_and_excludes_round` is included in both 96-test matrices. It blocks native readiness until the control worker finishes overdue, then verifies preserved manual receipt/arrival, refusal of healthy results, and the export exclusion flag. The transport matrix also covers an actual delayed SQLite commit, an interrupted relay, invalid choice without poisoning, and a cancelled HTTP waiter whose admitted worker still finishes.

## Remaining boundary

No full33 or public-browser acceptance is claimed here; root owns that independent review and broader run. The complete-body timestamp is taken when the ASGI `receive()` yields the final body message to `_body`; transport or event-loop delay before that yield is outside this process-level linearization boundary. The new lock makes validation and pending registration atomic with native settlement closure from that point onward.
