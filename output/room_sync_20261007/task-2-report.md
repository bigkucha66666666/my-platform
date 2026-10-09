# Task 2 implementation report

Completed 2026-10-08. Implementation remains in place, without commits, deployment, paid API calls, new worktrees, or oTree ORM database writes. Task 1 journal files were not changed.

## Changes

- `dynamic_bottleneck_round/sync_runtime.py`: lazy singleton journal, one process boot ID, once-per-initialization boot recovery, durable database UUID in a separate metadata table, strict HMAC identity tokens, active transport guard, pending ACK/decision registry and reset lifecycle helper. Metadata connections explicitly close on success/failure and use `synchronous=FULL`. Existing cached journals fail closed if their backing file disappears or changes inode. Signing initializes on the business side; verification uses cached identity and HMAC without SQLite, initialization locks or file stat.
- `dynamic_bottleneck_round/sync_transport.py`: fixed `/dynamic-room-sync` JSON endpoint with same-origin and method checks, 16 KiB body limit, five-second incomplete-body timeout, trusted complete-body timestamp, four fixed persistence workers and at most 128 submitted control operations. Valid signed ACK/decision arrivals are registered before the first executor yield; future completion, including cancellation before starting, owns cleanup. Cancelling an HTTP caller does not prematurely unregister a running persistence operation. Worker errors are returned as sanitized JSON without exception/body/token/environment details; responses use `no-store`.
- Journal shell identity and page registration are authenticated before any state advancement. State is peeked without advancement, then pending current-page HTTP/Live ACKs, control ACKs, failed pages and requests crossing preparation cutoff are supplied to `exchange`. Participant-specific journal snapshots remain authoritative for capacity visibility. Clock ID/nonce are echoed only together when both pass validation.
- `ThreadedASGI` keeps the entire original app, HTTP/Live/Room and existing oTree lock on one dedicated business loop. Send/receive bridge onto the owning ASGI loop from the caller's current loop, including oTree's `asyncio.run` worker-thread sends. Request ContextVars propagate into business execution; receive-time Live ACK ContextVars propagate back. Exceptions propagate. Cancelled HTTP callers drain the business operation before relinquishing ingress ownership; explicit closure drains active HTTP before stopping the loop. Lifespan reaches the real app; startup failure is explicit and sanitized; executor and business thread are closed.
- `project_asgi.py`: only environment protocol `2` builds `SyncControlASGI(DecisionIngressMiddleware(ThreadedASGI(otree_app)))`; all other values retain the existing wrapper.
- Root-approved additive changes to `decision_ingress.py`: optional `page_index` filtering in pending/crossed request helpers, corresponding metadata in pending records, unchanged default helper behavior. Matching focused test added to `test_decision_ingress.py`.
- `test_sync_transport.py`: 22 tests using only disposable SQLite journals.

## RED → GREEN evidence

The initial 10 transport tests failed because the isolated control implementation was absent. After implementation they passed. The page-filter test first failed on the absent keyword argument, then passed after the additive helper extension.

Additional regressions were observed failing before fixes:

1. A valid ACK queued behind an occupied persistence worker had no pending registration. Removed request-time executor initialization before verification/registration. Coverage also checks queued decision registration, round isolation and cancellation before worker start.
2. Live ACK receive timestamp disappeared across the loop boundary. The bridge now returns the receive context to the business task.
3. Cancelling HTTP released business ownership while its synchronous worker was still running. HTTP is shielded and drained before returning cancellation.
4. Startup initialization exceptions escaped instead of yielding an explicit sanitized lifecycle failure. Added explicit failure response and cleanup.
5. Runtime metadata connection remained open after initialization. Added explicit closing plus FULL durability.
6. Explicit close cancelled active business HTTP. Close now drains HTTP first.
7. Lost journal returned an ordinary request rejection. It now returns technical 503 without recreating the lost file.
8. Excessively nested JSON escaped as a recursion exception. It now returns generic 400.

## Final verification

`/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_transport -v`

Result: 22 tests passed.

`/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal dynamic_bottleneck_round.test_sync_transport dynamic_bottleneck_round.test_decision_ingress -q`

Result: 64 tests passed (27 journal, 22 transport, 15 ingress).

The responsiveness test runs a genuine synchronous `threading.Event.wait` inside the business ASGI coroutine; control completes within the 0.5-second bound before releasing that business wait. Other tests cover complete-body timing, current-page pending GET protection, malformed/signature/origin/method/large/incomplete inputs, registered shell binding, clock pairs and capacity omission, lazy boot recovery exactly once, stable/replaced database identity, pure signature verification, cancellation ownership, worker-loop sends, lifecycle and disconnect/exception completion.

Scoped `git diff --check` passed for the changed tracked files. A broad workspace diff check encountered pre-existing trailing whitespace in an unrelated paper compilation log; that file was left untouched.

## Self-review and integration boundaries

- No page sequence, round count, Room flow, participant labels, grouping, cost or cash calculation was changed.
- Control workers use the dedicated journal/configuration only; no oTree ORM model creation is introduced. The original oTree transaction lock is retained.
- Authentication uses the existing journal's connection helper for a read-only `sync_shells` lookup before state peek/advancement. Task 1 schema/API remains unchanged.
- The cached identity makes main-loop verification nonblocking. Persistence validates the live file before accessing it; a new initialized database gets a new UUID, so old tokens fail against it. Task 3 must persist/check the database UUID in Session before ensuring a round, as specified in the integration brief.
- Runtime `reset_runtime()` requires drained pending work and is not used as a normal request operation. `aclose()` drains/stops transport resources and clears the active guard.
- Graceful shutdown intentionally waits for active HTTP business work. A permanently wedged synchronous callback cannot be safely force-terminated by Python without risking concurrent/orphaned ORM work; this implementation reports a business-thread stop failure if the stop/join stage fails, rather than weakening serialization.
- These are targeted transport/journal/ingress checks, not a full oTree browser or multi-client acceptance run. Root's independent review and Task 3 integration remain next.

## Independent-review fixes (2026-10-08)

Two important review findings were reproduced before changes and fixed without touching Task 1 or broadening the transport design.

1. Real Starlette lifespan startup failure placed a diagnostic sentinel in its downstream `lifespan.startup.failed.message`, and then raised an exception. The previous wrapper forwarded that traceback and emitted a second failure. The new test `test_real_starlette_lifespan_failure_is_sanitized_exactly_once` runs real Starlette startup/shutdown handlers. RED: startup subtest failed the no-sentinel assertion. GREEN: downstream startup/shutdown failure events are replaced with a fixed generic message; the failure-send guard prevents a second event when the app subsequently raises. The test verifies exactly one failure event, correct phase, and no diagnostic sentinel for both startup and shutdown.
2. A `RuntimeError` raised by ASGI `receive()` escaped during body acquisition. RED: `test_unexpected_receive_error_is_sanitized_but_cancellation_propagates` failed because that exception escaped. GREEN: unexpected body-acquisition exceptions return technical JSON 503 without diagnostic content. The same test verifies that `CancelledError` still propagates with no response. Existing disconnect, body-size, malformed-JSON and timeout tests remain passing.

Focused RED run: two tests, two failures. Focused GREEN run: two tests passed. Final covering command:

`/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_transport dynamic_bottleneck_round.test_sync_journal dynamic_bottleneck_round.test_decision_ingress -q`

Result: 66 tests passed (24 transport, 27 journal, 15 ingress). Ready for the same independent reviewer to re-review.
