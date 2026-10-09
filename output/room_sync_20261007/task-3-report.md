# Task 3 implementation report

## Result

Protocol 2 is integrated into the original experiment: durable control receipts
precede native oTree POSTs; Journal owns the Session-wide decision clock; original
H/HA settlement, real RL, warmup/formal totals, questionnaire/payment and page
sequence remain in place. The full-flow helper traverses all 33 rounds and every
original survey/payment/finish callback; root is running that acceptance separately.
Production decision windows remain 60 seconds.

## Observed RED evidence

- Real frozen v2 smoke initially failed: every Decision GET returned 302 because
  the business protocol marker accepted only 1.
- Frontend v2 test failed `v2 must send control ACK`: 0 actual fetches vs 1 expected.
- After-success callback test initially reported the missing `after_success` API.
- Canonical native audit regression failed because a previously invalid native
  POST was stored as the accepted submission ingress. Audit now records the first
  accepted native request, distinct from the receipt ingress.
- Late receipt settlement test failed `disconnect_auto` vs expected `timeout_auto`.
  Late receipts are now finalized with the original timeout strategy after drain.
- Suspended frozen GET failed 500 `ValueError('unknown participant')`; original
  suspended proxy selection now occurs before skipping Decision to RecoveryGate.
- After-release native HTTP failure test found an empty technical anomaly; the
  Session-wide settlement guard now marks `decision_page_http_failure`.
- A late pending control write incorrectly permitted direct automatic settlement;
  all pending decision writes now block, including late writes still persisting.
- Repeated Enter during an in-flight retry caused a fourth control call; queued
  decision duplicates are removed after durable confirmation.
- Rejected decision queue prevented a status probe; the frontend now serially
  probes status on a rejected receipt before retrying, allowing anomaly shutdown.
- V1 metadata lacked the new blank receipt columns; it now includes blank fields.
- Callback thread test initially ran SQLite work on the control loop's owner
  thread; callbacks now run via `asyncio.to_thread` after native success.

## GREEN evidence

Final server5 focused run:

```
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. \
  /opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.test_room_sync_framework \
  dynamic_bottleneck_round.test_room_release \
  dynamic_bottleneck_round.test_decision_ingress -q
Ran 63 tests in 23.620s: OK (one explicit full-flow acceptance skipped)
```

After moving the success callback to its worker thread, this exact server5
command (oTree5.11.5 selected via the inherited source path) was run:

```
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. \
  /opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.test_decision_ingress \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_real_business_rollback_commit_pending_and_identity_guard -q
Ran 18 tests in 0.792s: OK
node --test dynamic_bottleneck_round/test_room_release.cjs \
  dynamic_bottleneck_round/test_sync_poll.cjs
7 tests: PASS
```

Real disposable frozen tests cover dual 40-human Sessions, mixed H-I0/HA-I1 within
one Session, I1 privacy until T, refresh/duplicate ACK, durable canonical receipt
vs a changed/invalid native value, invalid native retry, duplicate native POST,
late automatic source, drain/pending protection, direct settlement protection,
native Decision HTTP failure, journal identity mismatch, suspended recovery, and
the zero-eligible cohort. Only the paid external provider is mocked; RL and
settlement are production code. Actual native Room add/remove and Session monitor
initial rows plus synchronous worker broadcast pass through the real bridge.

The commit regression injects a genuine SQLAlchemy `before_commit` failure after
both groups prepare results: native HTTP500 rolls results back and Journal remains
unsettled. Removing the injected failure lets a subsequent real ResultsSync GET
persist both groups and mark Journal settled after the HTTP request completes.

## Self-review

- Journal/runtime/transport contracts were not edited. Every business Journal
  access checks the stored Session journal identity; initialized round markers
  prevent recreating a lost historical clock.
- Decision identity comes from the actual round's Decision lookup index even
  when the caller is on RecoveryGate/ResultsSync. Eligible cohort freezes before
  recovery changes participant eligibility.
- Private condition/capacity/level maps are per participant's own group. Signed
  tokens contain only identity fields and a registered shell nonce.
- Canonical imports preserve existing choices/results and request-scoped
  preexisting-choice flags. Receipt import calls original manual bookkeeping.
- All pending control decisions block settlement; missing choices wait through
  configured drain; overrun is technical data, and late receipt differs from
  genuine absence using the original strategies.
- Both groups must persist results before a request-scoped, deduplicated,
  journal-only callback can mark settlement. A native failure cannot run it.
- Native HTTP and durable receipt timing fields remain distinct; actual ACK shell
  identity is used to match completed shell response metadata. Pure metadata
  reads use stored participant variables instead of marking them dirty.
- JS coalesces status, prioritizes decision/operative, keeps one in-flight
  operation, intercepts form submission including Enter, retains the first choice
  through lost responses, clears duplicate queued decisions, and stops on anomaly.
  V1 clock bounds and timeout flow remain. Changed asset has a cache version.
- `git diff --check` on edited tracked task files passed. No Git commits, paid
  requests, deployment, worktree creation, or browser mass-launch were performed.

## Harness incident and attribution

The first server5 invocation of the new harness ran exactly these two tests in
order at 2026-10-08 12:19:07: suspended flow, then transaction flow. Its top-level
package import initialized oTree5's database before tempfile chdir. oTree5
`create_session` commits a bare Session before constructing Participants, so the
subsequent `_monitor_note` schema mismatch left two empty local Session artifacts.
Root verified IDs36/37, codes `w4e6q7it`/`jt34mxuj`, with zero Participants and
zero Subsessions across all seven app tables. The transaction failure output
explicitly identifies `jt34mxuj`; the first artifact's attribution is by ordering,
timestamp and root's scoped verification. Root completed recoverable targeted
cleanup with backup
`/private/tmp/room-sync-local-db-recovery.hPfOIC/before-empty-test-session-cleanup.sqlite3`.
Root reports the original 35 Sessions remain and SQLite `quick_check=ok`. This
implementation agent did not delete or edit those database rows.

The harness now loads the standalone helper by file specification (no app import),
changes into the fresh tempfile first, and only then imports/setup oTree. It
preserves inherited PYTHONPATH, prints actual oTree source/version, and checks the
child source matches the parent-selected runtime. Every later server5 test uses
its own temporary SQLite and Journal files.

## Files changed

- `dynamic_bottleneck_round/__init__.py`
- `dynamic_bottleneck_round/sync_integration.py` (new)
- `dynamic_bottleneck_round/Decision.html`
- `_static/dynamic_bottleneck_round/room_release.js`
- `dynamic_bottleneck_round/decision_ingress.py` (additive after-success hook)
- `dynamic_bottleneck_round/test_decision_ingress.py`
- `dynamic_bottleneck_round/test_room_release.cjs`
- `dynamic_bottleneck_round/test_room_release_framework.py` (v2/full-flow branch)
- `dynamic_bottleneck_round/test_room_sync_framework.py` (new)
- this report

## Post-implementation compatibility regression

Root's broad installed6 discovery found two existing lightweight
`DynamicCapacityPresentationBackendTests` fixtures lacking `player.session`.
Reproduced RED (two AttributeErrors at Decision vars_for_template line4599) with:

```
/opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.tests.DynamicCapacityPresentationBackendTests.test_i0_decision_context_does_not_disclose_capacity_or_level \
  dynamic_bottleneck_round.tests.DynamicCapacityPresentationBackendTests.test_i1_decision_context_discloses_exact_capacity_and_level -v
Ran 2 tests in 0.002s: FAILED (errors=2)
```

Narrow fix: compute `room_v2` with `getattr(player, 'session', None)`, matching
the existing optional Session check, and use it for the two new control-template
attributes. I0/I1 capacity visibility assertions and all original context values
are unchanged. Same command GREEN: 2 tests in0.001s, OK.

Frozen v1 GET and frozen v2 canonical/native/late boundary checks both passed in
installed oTree6.0.13 (2 tests in1.082s) and server5.11.5
(2 tests in1.097s) with:

```
/opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_canonical_native_validation_late_and_direct_settlement_drain \
  dynamic_bottleneck_round.test_room_release_framework.RoomReleaseFrameworkTests.test_frozen_player_can_render_real_decision_get -v

env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. \
  /opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_canonical_native_validation_late_and_direct_settlement_drain \
  dynamic_bottleneck_round.test_room_release_framework.RoomReleaseFrameworkTests.test_frozen_player_can_render_real_decision_get -v
```

Root reports explicit server5 80-human33-round acceptance PASS in349.700s, exit0
(root-validation.md). The full-flow child loaded the pre-final callback thread
implementation; final callback and real commit regression were subsequently
verified independently by root on server5 (18 tests,0.719s, OK).

Outstanding: root's final broad rerun and independent review.

## Task 3 review corrections: two Important findings

### Export side effects

Reviewer found that `export_metadata` invoked the business `state(player)` helper,
which initializes untouched future rounds. Real isolated server5 regression
iterated original `custom_export` across all33 Players while round4 was active,
then called original `export_row_for_player` for every future Player. RED:
`export must not start a future prepare clock` (future prepare_started_ts set).

Fix: metadata first validates the Session journal identity, reads the stored
initialized-round marker, and returns protocol2 plus blank audit fields for an
uninitialized round. For initialized rounds it directly reads Journal.state/audit
without calling ensure_round or the business state initializer. A marked but
missing Journal round still fails closed. Test asserts only round4 exists in
Journal, Session initialized rounds stay[4], active clock remains byte-equivalent
as a returned state, and the later real round5 GET creates a fresh prepare clock
at that GET rather than at export time. Native formal export still has30 rows.

### Manual receipt race at drain

Reviewer found a receipt committing after the first import and before the second
pending check could be seen in the final drain sweep but ignored as manual, then
be overwritten by disconnect automatic filling. The deterministic isolated
regression uses the actual ready_for_results helper and actual Journal.exchange:
after the first stale receipt read, a canonical on-time receipt commits and
pending is clear, while business processing is at deadline+drain+1. RED:
`disconnect_auto` instead of `manual`.

Fix: final drain sweep re-imports a fresh manual receipt through existing
`import_manual`, which preserves preexisting choices/results and calls original
`record_manual_decision`. The test asserts manual source, exact474 departure,
and `last_manual_departure_minute=474`; late and genuine-absence strategies remain
covered by the existing real boundary test.

Both regressions plus canonical/late and genuine business commit rollback tests
passed after correction using the exact commands below:

```
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. \
  /opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_future_round_exports_do_not_start_prepare_clocks \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_receipt_committed_after_initial_import_wins_final_drain_sweep \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_canonical_native_validation_late_and_direct_settlement_drain \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_real_business_rollback_commit_pending_and_identity_guard -v
oTree5.11.5: Ran4 tests in2.356s, OK

/opt/anaconda3/envs/otree_env/bin/python -m unittest \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_future_round_exports_do_not_start_prepare_clocks \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_receipt_committed_after_initial_import_wins_final_drain_sweep \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_canonical_native_validation_late_and_direct_settlement_drain \
  dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_real_business_rollback_commit_pending_and_identity_guard -v
oTree6.0.13: Ran4 tests in2.382s, OK
```

Files for this correction: sync_integration.py, test_room_sync_framework.py, this
report. No Journal/runtime/transport changes, Git commits, or new project DB
artifacts were made. The corrected I0/I1 optional-Session template gate remains.
