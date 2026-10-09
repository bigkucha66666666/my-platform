# Root independent validation

## 2026-10-08 server oTree5.11.5 full33

Command (temporary child database/journal, external paid provider mocked):

```sh
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. RUN_ROOM_SYNC_FULL_FLOW=1 /opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_full_flow -v
```

Result:1 explicit full acceptance passed in349.700s; exit0. Two independent I0/I1 Sessions,40 real-human actors each; H30 and HA10human+10LLM+10RL.33 rounds including3warmup/30formal;2640 human manual choices plus1320 virtual outcomes, native ResultsSync/Results/FormalStart and H/HA surveys/payment/end callbacks, formal-only cost totals and cash rounding assertions passed. RealRL/settlement; paid provider mocked. Entry is fixture-advanced to first Decision, so this does not certify introductory browser interactions or actual DOM T+2 under a public network.

Read-only progress check at round23 confirmed92group results and1840manual choices in temporary db, not projectdb. Parent/child processes45822/45824 finished naturally, session50875 exit0.

This full run loaded code before the final narrow after-success callback move to a worker thread. Final callback version is covered by Task3's18-test focused callback/real commit failure regression; Task4 must validate installed6 full flow on the final reviewed version.

## Final reviewed installed oTree6.0.13 full33

```sh
env RUN_ROOM_SYNC_FULL_FLOW=1 /opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_room_sync_framework.RoomSyncFrameworkTests.test_full_flow -v
```

Result1explicit full acceptance passed358.058s,exit0 on final reviewed production code, including optional-session/export/final-drain fixes and threaded after-success callback. Same2Sessions/80human/33round/2640human+1320virtual/survey/cost/cash/end assertions as server5 run. Parent/child finished naturally (session95224). No browser, deployment or paidAPI.

## Other independent checks

- Journal+transport+ingress:67 tests passed0.567s (before final additional callback tests).
- JS release/poll:7 tests passed52.6ms; room-release file includes v2 assertions.
- Legacy oTree5.11.5 framework:6 passed,1 explicit full-flow skip;19.709s.
- Task3 agent reports63 focused server5 tests and final18 callback/commit tests green; see its report for exact code and coverage.
- Final callback-to-worker version independently rerun on server5: `test_decision_ingress` plus `RoomSyncFrameworkTests.test_real_business_rollback_commit_pending_and_identity_guard`,18passed0.719s,exit0.
- Surrounding access-label/survey/cash/Room-label/admin-template/participant-export regressions:46passed0.015s on installed6,exit0.
- Initial installed6 broad dynamic discover:438tests71.576s,2errors,2opt-in full-flow skips. Existing I0/I1 presentation fixtures lack `session`; new control-template gate accessed `player.session` unconditionally. Returned this reproducible RED to Task3 implementer for narrow compatibility fix; final broad rerun required.
- After fixture/export/final-drain race fixes and clean re-review: installed6 dynamic discover440tests74.424s,OK,2explicit full-flow skips; node7passed56.2ms. Projectdb readonly quick_check=ok,35Sessions.
- Server5 surrounding46checks also passed0.017s.
- Final server5 dynamic discover440tests74.071s,OK,2explicit full-flow skips; exit0 (same `discover -s dynamic_bottleneck_round -t . -p 'test*.py'` command with server5 PYTHONPATH prefix).

## Independent final real-loopback load and legacy bridge checks

Command: `env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. /opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_room_sync_load -v` (scoped loopback network permission).

Result2tests11.134s,OK,exit0. One owned uvicorn TCP server/socket, realJournal and2control persistence workers,1single-business-loop synchronous-blocking fixture.2Sessions40actors each.80ACK,80status,80durablemanual,80nativefixture callbacks after cutoff,80canonical replays,1lostreply replay,1genuine late. All cleanup assertions executed. Boundary uses trusted test-clock fixture (T1941,deadline2001,complete-body arrival2000=T+59,business consumption2002>T+60), productionwindow60unchanged. It is not an actual ORM/database commit or publicbrowser benchmark.

Measured real-loopback response timings(ms): ACK median17.812/p9528.694/max31.472;status median32.129/p9550.803/max52.490;decision median43.069/p9564.665/max67.685;nativefixture median161.412/p95162.178/max162.293;replay median35.209/p9556.039/max58.365. Each80samples. Genuinelate1.464ms(1sample).

Separate actual frozen-oTree compatibility scenario: original marker1 Sessions under already-installed protocol2 ThreadedASGI/control wrapper;2Sessions80human nativeLive/manual results,81shellGETs,0nonemptyfasttokens,realRL+originalH/HA settlement,mockedpaidprovider. Business bridge/executor closed naturally. No production protocol changes or browsers.

## Local harness cleanup

Two confirmed failed-test empty local Session shells36/w4e6q7it and37/jt34mxuj were backed up and removed with exact ID/code and all dependent-row absence guards. Backup `/private/tmp/room-sync-local-db-recovery.hPfOIC/before-empty-test-session-cleanup.sqlite3` contains37 Session rows; localdb now has35 original Session rows. Both SQLite quick_check=ok. No participant or experimental record deleted. This was correcting a test-isolation incident, not production service/database access.

## Final cross-task failure-path probe (RED)

One isolated no-network diagnostic reused actual TransportTests setup/realSQLite and ASGI executor in a fresh temp cwd (forcedSQLite/in-memoryoff/staticlink). Round armed atT+59, registered valid decision token. Injected RuntimeError from receipt persistence. Actual response503; pending registration cleared; no receipt; Journal technical_anomaly stayed empty. Expected durable/server-side technical fault assertion failed (1test0.015s). This confirms the response alone is insufficient to prevent later business automatic absence filling after temporary persistence recovery. Needs coordinated fail-closed control-error bookkeeping/integration fix before whole-change approval. No projectdb access, no browser or deployment. Initial probe fixture lacked_static and was corrected before actual failing assertion; that import error is not RED behavior evidence.

## Task5 final independent validation in progress

Task5 focused realJournal/transport/framework runs passed on both versions (78+3 separate runs, one explicit full33 skip); see task-5-report.md. Root started the final-code server5 full33 and one isolated dynamic discover. The first broad runner supplied only _static, so legacy template tests could not find application/_templates paths, subprocess import paths were relative to the disposable directory, and oTree5's app-style detection lacked app directories. Result452tests88.586s,22fixture errors,2full-flow skips; this is not a clean regression pass and is not behavior RED. Actual protocol2 load and old-marker1-under-wrapper2 scenarios passed within that run. Corrected runner now supplies readonly symlinks for _templates/_rooms/all app directories and absolute project PYTHONPATH inherited by children; still forces temporary SQLite/in-memoryoff before any app import. No project database was opened by this parent/child runner.

Final JS on Task5 code:7passed58.248ms,exit0 (Task5 made no JS changes). Readonly process inventory showed only the one owned full33 parent/child and one dynamic regression parent, no duplicate persistent test server/browser.

Corrected server5 isolated dynamic discover:459tests88.696s,OK(skipped=2),exit0. Complete readonly fixture symlinks plus absolute inherited PYTHONPATH fixed the runner issues. Includes actual loopback80load and marker1-on-wrapper2:80ACK/status/durablemanual/nativefixture-after-cutoff/replays complete,1lostresponse replay/1genuinelate. Response decision median45.875ms/p9569.831/max77.192 (80samples); ACKp9529.544,max35.688;statusp9553.785,max56.159. These are local TCP fixture metrics, not production ORM/publicbrowser metrics. Owned child server/businessbridge/executor cleaned up.

Task5 production server5 full33:1test354.096s,OK,exit0 (session12097), same dualSession80human/2640humanmanual/1320virtual/realRL/mockpaid/full33/survey/30formal-onlycost/cash/end assertions. This full run and the corrected459test run loaded Task5 code before its final independent review requested two additional fault-path fixes (retainedfault healthy response and autonomous overload flush). They are not silently relabeled as after those fixes. Root will pair these normal-path acceptance runs with focused revised-code tests and final whole-change review, and explicitly record any later acceptance runs.

Task5 independent review: two Important findings and one Minor auth lifetime bound, see task-5-review-findings.md. Single implementer fix wave dispatched. Handoff documents32768 bound; small-bound test requested. No automatic commit or deployment.

## Corrected Task5 final-code acceptance

Task5 corrected review spec compliant/task quality approved; no remaining Important/Critical. Missing latest-wave pre-fix RED output remains a documented workflow evidence caveat; original Task5 RED retained. Identity ceiling documented and small-bound tested. Corrected focused89OK/1explicitfullskip on installed6(31.143s)/server5(30.971s).

Root full33 after all Task5 production edits, server5:1test359.723s,OK,exit0 (session52276). Parent itself forced SQLite/inmemoryoff and chdir disposable before app import with complete readonly fixture symlinks/absolute inheritedPYTHONPATH. Same80human/2640humanmanual/1320virtual/33round/nativeH-HA/survey/formal-onlycost/cash/end assertions. This is exact final Task5 backend code acceptance, not publicbrowser/introDOM or paidLLM acceptance.

Root server5 final Task5-code load/legacy:2tests12.266s,OK,exit0 (session62021). Owned loopbackTCP and originalmarker1-on-wrapper2 bothpass.80ACK/status/durablemanual/nativefixture-aftercutoff/replays;1lostreply+1truelate; metrics each80samples: ACKmedian23.058/p9537.795/max39.523ms;statusmedian41.657/p9566.848/max69.619;decisionmedian52.917/p9583.301/max84.918;nativefixturemedian200.574/p95201.914/max202.081;replaymedian44.042/p9567.855/max70.292. TrustedtestclockboundaryT1941,dead2001,arrival2000,consumption2002; unchanged60s productionwindow. All owned TCP/business/executor cleanup assertionspassed. Temporary SQLite engine/path/inherited-externalURL guardpassed; no browser/productionPG benchmark.

Final Task5-code surrounding label/survey/payment/Room/admin/export:46tests0.022s,OK,exit0, parenttempSQLite. py_compile all changed/newPython modules passed; scopedtrackedgitdiff--check clean. Untrackedsource/spec/plan/handoffgitdiff--no-index--check produced emptyoutput,exit1expectedforaddedfiles(no whitespace errors). JS7passed58.248ms from earlierTask5 unchangedasset; not a whole DOM timeout test.

Whole-change review in progress subsequently identified missing request timeout in frontend controlPump; an asset-level fix and behavioral regression are still required. Full backend33 does not execute browserJS and cannot certify that issue.

## Task6 final correction and local completion

Task6 closes all3whole-review findings: frontendheaders/JSON/statusprobe timeout, novelmanual controlpersistence limit beforepending clears, and completebody atomicpending transfer beforewait_forrelay. ActualRED/GREEN documented in task-6-report.md. Finalfocused96OK/1explicitfullskip each: installed6 34.466s/server5 34.437s. Corrected whole-change rereview approved spec/taskquality/localhandoff, no remainingCritical/Important; see final-review.md. Authorizationceiling/evidence caveats preserved.

Root Task6 normal full33 server5 after atomic-transfer stable notification:1test358.758s,OK,exit0 (session74626). Parent/childtempSQLite beforeimports, same2independentI0/I1Sessions80human/2640manual/1320virtual/realRL/mockpaid/3warm30formal/nativeResultsbarrier/surveys/cash/end assertions. Readonly progress query observed all132Groupresultsready before naturalcleanup. Optionaljournalcounter query after childfinished failed unableopen because tempdirectory was already removed; not a test failure or projectDBaccess. Final96matrix separately covers the last invalid-ASGI-message400classification restoration; fullnormalflow does not send that erroneous message, and counts are not combined into one omnibus run.

Root finalTask6 server5 actualTCP+marker1underwrapper2:2tests11.708s,OK,exit0(session67349). All80ACK/status/durablemanual/nativefixtureaftercutoff/replay countscomplete,1lostreply/1truelate;2controlworkers/1blockedbusinessloop, noORMbenchmark. Each80samplesms:ACKmedian18.838/p9530.867/max34.598;statusmedian36.755/p9557.692/max59.573;decisionmedian47.942/p9569.481/max81.938;nativefixturemedian179.057/p95179.879/max180.046;replaymedian37.593/p9559.455/max62.726. Trustedtest-clockT1941/dead2001/arrival2000/consume2002,60sproductionwindowunchanged. Actualsqliteengine/tempdatabase/inheritedexternalURLoverride assertionspassed; ownedTCP/socket/thread/executor cleanup complete. Notpublicbrowser/productionPG.

Root finalTask6 Node release+timeout+syncpoll:8passed71.121ms,exit0; realproductionVM timeoutbehavior plus legacyclock/poll cases. Finalpy_compile changed/newPython modules and trackedscopedgitdiff--check pass. Previousallnewfile whitespace checks emptyoutput/expectedaddedfileexit1; finalsource recheck recorded atclose. No broad userpaper whitespace errors were edited.

Cleanup inventory after bothrootruns: no ownedunittest/fullflow/uvicorn/project_prodserver/19080persistentprocess remaining (onlydiagnosticps/rg itself). Projectdbreadonlyquick_checkok,35originalSessions,HEADa202ebf/no commits. No browsercreated/deployment/paidAPI/productionDB operations.
