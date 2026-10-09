# Task5 independent review — Needs fixes

Spec compliance: issues found. Task quality: needs fixes. Reviewer sync_fault_review, 2026-10-08. Critical: none. Full-flow/payments cannot be verified from this task diff; root owns acceptance.

## Important 1: Healthy responses escape a retained fault

sync_transport.py:180–181 ignores persist_control_fault's returned reason. If mark_anomaly fails but reads/exchange succeed, status returns healthy200 and a retry decision may write a new healthy manual receipt. Native settlement guard alone does not prevent clients resuming a failed round. Honor retained fault before normal exchange and check again before returning a response for concurrent failures. Add real-temp regression where only anomaly persistence fails while later status/decision storage could otherwise succeed.

## Important 2: Overload failure not flushed after capacity recovers

sync_transport.py:289–291,298–300 only latch RAM; completion releases slots without arranging persistence. If affected clients stop after503 and queued work belongs to other Sessions, available healthy storage/workers never save the original fault. Requirement: persist as soon as safely possible. Arrange a bounded worker-side persistence retry when capacity becomes available. Retain failclosed guard if write fails. Test recovery without any further affected-round request. No unbounded jobs/retry loops/SQLite on main event loop or registry locks held over SQL.

## Minor: Process-lifetime authorization bound

sync_runtime.py:114–119,126–128,250 never retires old shell identities before reset, eventually rejecting new issuance at32768 (refresh identities also count). Document and test this operational ceiling, or safely retire metadata while maintaining canonical historical replay. Root has already documented the ceiling in handoff; a bounded regression is sufficient for this narrow fix unless correctness requires lifecycle redesign.

## Strengths checked

Fault before pending release; all-kind cancellation shielding; pre-admission auth/choice checks; final atomic close after native commit; journal settled-history immutability; real-temp/native race regressions. Reviewer performed named-risk-only readonly checks of journal/client/final-callback dependencies, no test duplication.
