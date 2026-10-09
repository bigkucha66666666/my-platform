# Task 5: Fail closed after legitimate control failures

Date: 2026-10-08. No commit, deployment, production database access, browser automation, paid provider calls, or paper changes.

## Outcome and files

Unexpected failure of an authenticated, valid active-round control request now installs a server-side fault before the worker releases its pending registration. Worker failure records `control_persistence_failure`; authenticated admission failure records `control_queue_admission_failure`. The native business path refuses automatic filling and healthy settlement after recovery, and retries durable anomaly recording. Failure of that anomaly write does not remove the interlock.

Production changes are limited to:

- `dynamic_bottleneck_round/sync_runtime.py`: vetted authorization metadata, round fault interlock, worker/business persistence retry, atomic final control boundary.
- `dynamic_bottleneck_round/sync_transport.py`: authentication/choice preflight before queue admission, typed validation failures, worker-owned fault recording, cancellation shielding.
- `dynamic_bottleneck_round/sync_integration.py`: state/audit overlays while persistence is unavailable, readiness/final-drain checks, final native settlement guards and marker failure bookkeeping.
- `dynamic_bottleneck_round/sync_journal.py`: `SyncRequestRejected(ValueError)` distinguishes intentional client/admission rejection from operational exceptions. Journal data layout, clocks, receipts and transaction algorithm are unchanged. This addition was necessary: real injected `PermissionError` and `ValueError` previously returned403/400 without a fault, confirmed by RED tests below.

Focused tests changed in `test_sync_transport.py` and `test_room_sync_framework.py`. All edits used apply_patch.

## Fault lifecycle and concurrency

1. Business token issuance reads the real Journal and caches only a vetted exact identity and its allowed-minute set. Neither clocks, participant decisions, receipts nor result values are cached. The cache is bounded at32,768 identities and reset by `reset_runtime`; it refuses new issuance at capacity rather than silently evicting an active capability. Unregistered signed test identities are not admitted to this cache. Post-restart active rounds still fail closed through the existing boot check; existing tokens need fresh business issuance to populate the new process's capability cache.
2. The main control loop performs pure signature/identity/choice validation, then takes queue admission. Bad signatures, wrong identity and invalid choices cannot latch an overload fault. Valid admission failure latches immediately with no disk I/O on the main loop. It becomes durable at the next accepted control worker or native business access. No new unbounded background queue is introduced.
3. Every accepted control operation has pending ownership. An unexpected worker exception latches first and attempts durable anomaly recording on that worker before its done callback releases pending. ACK/status/operative failures use the same policy as decision failures. Expected early/invalid/admission rejections use the explicit Journal exception type and do not poison the round.
4. HTTP cancellation does not cancel already admitted queued/running control work. Pending remains until that work finishes; failure bookkeeping runs even if its HTTP waiter has gone away. Wrapper futures retrieve exceptions to avoid unobserved-exception warnings.
5. The runtime registry lock is never held over `mark_anomaly`, `state`, `mark_settled`, or any new SQLite operation. A deliberately blocked anomaly write test proves signature verification and pending operations remain responsive. Control workers import no ORM and call no native callbacks.
6. Native readiness checks persistent/latching faults before proceeding, after receipt import, and during the final drain sweep. A fault injected after an earlier healthy snapshot cannot become automatic absence. Canonical manual receipts remain unchanged.
7. `schedule_settled` checks once more while still in the native transaction. After the native commit succeeds, its existing worker callback atomically closes the control boundary only if no fault or control operation remains. A fault/pending operation arriving during native commit prevents a healthy Journal settlement and records an anomaly. Native values that already committed are preserved and excluded by audit; the implementation does not pretend it can roll back a completed native commit.
8. Once that successful native/control boundary closes, later replay failures cannot invalidate already completed results. An already-settled Journal is also respected during persistence retry. If the Journal settlement marker itself fails, the boundary is reopened and `control_settlement_failure` remains latched/durable, preserving established manual receipts.

## RED evidence

Every command ran from `/Users/hybsmac/Python项目/economics-experiment/my_platform`. Parent processes force SQLite with in-memory mode off and change to a disposable directory before importing the application. The framework tests spawn their own isolated children, retain the selected oTree installation, and mock no persistence except the deliberately injected fault. No project ORM database was used.

Initial transport RED command:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_transport.TransportTests.test_failed_control_persistence_leaves_durable_fault_before_pending_clears", "dynamic_bottleneck_round.test_sync_transport.TransportTests.test_cancelled_failed_worker_still_latches_fault", "dynamic_bottleneck_round.test_sync_transport.TransportTests.test_overload_authenticates_and_validates_before_latching_fault"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 3 tests in 0.034s; FAILED (failures=6)`, exit1. All four request-kind subtests found `'' != 'control_persistence_failure'`; the cancelled worker also left no anomaly; overload returned503 for an invalid request instead of validation rejection. These were assertion failures, not fixture/import errors.

Native framework RED command:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_control_persistence_failure_blocks_native_automatic_settlement", "dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_control_fault_during_receipt_import_blocks_automatic_fill", "dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_control_fault_blocks_until_anomaly_write_recovers"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 3 tests in 1.710s; FAILED (failures=3)`, exit1. Assertions: `storage recovery must not heal a lost decision`; `fault after readiness snapshot must block automatic fill`; `RAM safety latch must block while anomaly cannot persist`.

Additional settlement-marker RED command:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_failed_settlement_marker_keeps_durable_fault_and_manual_receipts"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 1 test in 0.709s; FAILED (failures=1)`, exit1. Real native commit had succeeded, but failed Journal marker left `technical_anomaly=''` instead of `control_settlement_failure`.

Operational exception classification RED command:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_transport.TransportTests.test_storage_permission_error_is_not_mistaken_for_invalid_identity", "dynamic_bottleneck_round.test_sync_transport.TransportTests.test_storage_value_error_is_not_mistaken_for_invalid_choice"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 2 tests in 0.021s; FAILED (failures=2)`, exit1: `403 != 503`, `400 != 503`.

## GREEN commands and outcomes

Final production code, installed oTree6.0.13:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_journal", "dynamic_bottleneck_round.test_sync_transport", "dynamic_bottleneck_round.test_room_sync_framework"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 78 tests in 27.279s; OK (skipped=1)`, exit0. The skip is the explicit full33 acceptance, owned independently by root.

Final production code, frozen server oTree5.11.5, preserving server package precedence:

```sh
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.append(str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_journal", "dynamic_bottleneck_round.test_sync_transport", "dynamic_bottleneck_round.test_room_sync_framework"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 78 tests in 27.138s; OK (skipped=1)`, exit0.

After these runs, test-only refinement split the original request-kind subtests into independent fresh-fixture tests, ensuring ACK/status/operative assertions cannot inherit the decision test's existing anomaly. Production code did not change. Exact added coverage commands:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_transport.TransportTests.test_failed_ack_persistence_leaves_durable_fault", "dynamic_bottleneck_round.test_sync_transport.TransportTests.test_failed_status_persistence_leaves_durable_fault", "dynamic_bottleneck_round.test_sync_transport.TransportTests.test_failed_operative_persistence_leaves_durable_fault"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.append(str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_transport.TransportTests.test_failed_ack_persistence_leaves_durable_fault", "dynamic_bottleneck_round.test_sync_transport.TransportTests.test_failed_status_persistence_leaves_durable_fault", "dynamic_bottleneck_round.test_sync_transport.TransportTests.test_failed_operative_persistence_leaves_durable_fault"]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: installed6 `Ran 3 tests in 0.048s; OK`; server5 `Ran 3 tests in 0.031s; OK`, both exit0. No JavaScript changed, so no duplicate JS suite was run.

The native commit-race fixture initially injected at an earlier internal commit before every group was ready. Its expectation that all results were already committed failed; the fixture was corrected to inject only when the real final `sync-settled` callback is present in the current native request. Final78 runs include the corrected race test and pass.

## Boundaries for root review

- Queue-admission faults are immediately server-side. The follow-up fix now schedules one owned, coalesced persistence job on the control executor, even if no affected-round request follows. Failed writes remain dirty for a later capacity/admission event and the native business path, without a self-rescheduling retry loop. The latch prevents healthy business progress in the interim, and restart protection remains.
- Authorization cache capacity is a fail-closed process lifetime bound. At32,768 distinct issued page identities, further token issuance fails visibly; it does not evict live identities and silently lose overload protection. No deployment tuning or lifecycle policy was added in this focused patch.
- A fault arriving after native database commit cannot undo that commit. Such rounds are marked anomalous and are not certified settled in the Journal; canonical choices/results already committed are kept immutable. A replay fault after the successful atomic control boundary is not allowed to rewrite historical success.
- Root owns final independent whole-change review, broad regressions and final-code full33 validation. This report claims only the focused runs above and does not treat pre-fix full33 results as coverage of this fix.

## Review-finding fix wave (2026-10-08)

The independent review found two important gaps in the preceding implementation: a retained fault could still produce a healthy transport response, and an overload fault lacked autonomous worker-side persistence when affected clients stopped. An interrupted implementer had already edited `sync_runtime.py`, `sync_transport.py`, and `test_sync_transport.py` for those findings before this verification handoff. I inspected those edits and completed the covering verification; no additional production edit was needed in this handoff.

The retained latch and durable-dirty ownership are distinct: `_faults` keeps the round interlock, while `_faults_to_persist` tracks only anomaly writes still owed. The worker checks the latch before exchange and again after exchange; the HTTP path checks it again before a healthy response. The control executor owns one coalesced flush job beyond admission slots. It snapshots dirty rounds, performs Journal writes outside both registry and scheduling locks, and schedules one further pass only for *new* dirty rounds that arrived during its pass. A failed write remains dirty, but does not spin. Completion callbacks can trigger a later attempt; shutdown drains accepted work and makes a final best-effort worker-thread attempt. A successful Journal write clears dirty ownership but retains the latch, so storage recovery alone cannot heal the round.

The new focused tests exercise a failed anomaly write followed by otherwise healthy status/decision storage, a fault injected during authentication and after worker completion, twenty authenticated overloads while another Session holds the only worker, persistence after that worker releases without another affected request, a failed flush without retry spin, a new fault arriving during a flush, shutdown drain, and the authorization ceiling/refusal/reset. The queue assertion bounds the coalesced extra job to one. These tests use disposable real SQLite journals and injected failures only at the fault boundary.

The preceding report's RED commands and outputs are the recorded RED evidence for the original Task5 defects. No RED command/output for the *review-finding fix wave* was preserved by the interrupted implementer. The added tests therefore provide current GREEN coverage, but this report does **not** claim that I witnessed their pre-fix RED. I did not reverse production edits solely to manufacture that evidence.

Exact covering command, installed oTree6.0.13, from `/Users/hybsmac/Python项目/economics-experiment/my_platform`:

```sh
env DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.insert(0,str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_journal","dynamic_bottleneck_round.test_sync_transport","dynamic_bottleneck_round.test_room_sync_framework"]); result=unittest.TextTestRunner(verbosity=1).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 89 tests in 31.143s; OK (skipped=1)`, exit0.

Exact covering command, frozen server oTree5.11.5, retaining server package precedence:

```sh
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. DATABASE_URL=sqlite:///db.sqlite3 OTREE_IN_MEMORY='' /opt/anaconda3/envs/otree_env/bin/python -c 'import os,tempfile,pathlib,sys,unittest; project=pathlib.Path.cwd(); tmp=tempfile.TemporaryDirectory(); os.chdir(tmp.name); pathlib.Path("_static").symlink_to(project/"_static"); sys.path.append(str(project)); suite=unittest.defaultTestLoader.loadTestsFromNames(["dynamic_bottleneck_round.test_sync_journal","dynamic_bottleneck_round.test_sync_transport","dynamic_bottleneck_round.test_room_sync_framework"]); result=unittest.TextTestRunner(verbosity=1).run(suite); sys.exit(not result.wasSuccessful())'
```

Output: `Ran 89 tests in 30.971s; OK (skipped=1)`, exit0. The one skip per run is the explicit full33 acceptance delegated to root. Parent processes set SQLite/in-memory-off and change to a disposable directory before importing the application; framework children preserve the chosen installation. No production ORM database, browser, paid API, deployment, commit, or full33 rerun was used. No JavaScript changed in this fix wave.

Self-review: the two review findings are covered by real Journal behavior and focused race tests. The extra job is bounded per transport instance, and all anomaly writes occur on executor or business threads without a registry/scheduling lock held across SQLite. Successful native/control closure still makes later replay failure historical rather than retroactive. Remaining operational boundary: if SQLite anomaly writes keep failing and no later admission/native access occurs, the latch remains in this process but no timer retries the dirty write; startup boot protection remains responsible after process restart. Root will independently review the whole change and run the final-code full33 acceptance.
