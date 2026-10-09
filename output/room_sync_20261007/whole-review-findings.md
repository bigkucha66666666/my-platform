# Final whole-change review — Needs fixes (2026-10-08)

Reviewer sync_whole_final_review. No Critical. Three Important findings including later focused arrival-boundary probe; all other inspected architecture/flow/privacy/fault controls supported. Local implementation handoff not approved yet. Server/publicbrowser acceptance remains separate.

## Important 1 — hanging frontend controls block choices indefinitely

_static/dynamic_bottleneck_round/room_release.js:68–81: neither main fetch nor decision-rejection status probe bounds fetch/response.json. controlInFlight blocks all queued controls forever if an attempt never settles. Connection warning only changes text. Tiny readonly NodeVM probe: unresolved fetch,120.4s simulated polls, queued decision =>fetchCount1/ACK only/no abort signal.

Fix: bound response headers AND JSON body; abort timed-out attempt, clean attempt ownership and its clock sample, retry/coalesce while retaining savedChoice. Apply to status probe too. Preserve one active client attempt, priorities, immutable canonical receipt, native form advancement, technical stop, originalT/T+60/clock intervals/I0-I1 privacy/v1. Test hanging headers/JSON/probe, retry and no overlapping active attempts; don't use browser farm.

## Important 2 — decision persistence overrun can be lost after pending finishes

sync_journal.py:254; sync_transport.py:343; sync_integration.py:150. Only native readiness detects still-pending control decision at deadline+drain. Journal checks ACK processing overrun but not decision. If business is blocked and worker finishes overdue first, pending disappears and later native import treats round healthy. Disposable realJournal probe:T107/deadline167/drain10/arrival166.99/processing200 =>canonical manual, anomaly empty.

Fix: worker-owned control-processing-overrun detection before pending release, independently of business observer. Keep valid receipt manual but mark round technical/excluded. Detect actual persistence completion/SQLite wait, not just a timestamp captured before the write. Genuine fresh late submissions and historical canonical replay must not turn into new processing-overrun faults; replay cannot retroactively invalidate durable healthy records. Already-durable manual later consumed natively remains permitted even after drain. Add actual blocked-business regression that resumes after worker completion and verify canonicalreceipt preserved/no healthy settlement/export exclusion. Persist-failure fallback retains runtime interlock.

## Minor / evidence boundaries

## Important 3 — completed body not pending before wait_for caller resumes

sync_transport.py:309 (pre-fix line): body timestamp exists in child `_body`, but parent awaits asyncio.wait_for before validation/pending registration. Independent native business can close/settle during relay. Tiny disposable realJournal probe paused that boundary: completebodyarrival166.99/dead167,pendingempty,close_control_roundTrue,settled marker set,subsequentHTTP400/noReceipt/anomalyempty. This exercised actual runtimeclose/journalmarker, not nativeORM.

Fix in the same ongoing Task6 wave: atomic completed-body timestamp/pure authentication/preflight/pending ownership using common settlement boundary lock before the timed body yields to caller. SQL stays worker-only. Transfer must remain owned/drained under HTTP cancellation/body timeout even if parent never attaches response waiter. Invalid request doesn't obtain legitimate pending or poison round. Preserve historical replay semantics.

## Evidence boundaries

No additional blocking issue. Documented32768auth lifetime bound and missing previous fix-wave RED output remain disclosed. Root final Task5 backendfull33 passed359.723s, finalTCP/legacy2passed12.266s; these do not certify frontendJS or the missing bound. This review's two small probes did not run suites, ORM or browsers.
