# 协议 2 首次到达与边界一致性修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 10 月 9 日报告六项缺口，使用首次完整合法准时请求的服务器登记顺序确定最终 manual。

**Architecture:** 保留独立控制端点、单业务 worker 和持久同步日志；按参与者调度 decision，后台仍并行服务其他参与者。前端独立优先发送，原生业务以回执为权威，故障联锁覆盖原生轮次关闭并保留已准入审计与恢复流程。

**Tech Stack:** Python、oTree 5.11.5/6、ASGI、受控线程池、SQLite WAL/FULL、原生 JavaScript、unittest 与 Node VM。

## Global Constraints

同一 Session 使用唯一 T 和 T+60；两个 Session 独立运行。保留全部页面、3 轮热身和 30 轮正式实验、Room/标签、I0/I1、成本、Agent 与支付规则。已确认 manual 不可覆盖，非法/早到请求不抢占有效值，客户端时钟不决定准时性。技术异常不认证为健康 settled。不得在控制事件循环或短 registry 锁内查询 SQLite/ORM、等待或执行 executor 工作。排队有界，关闭需检查 pending 与故障。

仅本地实施，不部署、不付费 API、不自动 Git 提交，不触碰无关论文与业务数据库。源文件通过 apply_patch 编辑；不并行派遣多个实现者。原地工作，不创建重复浏览器/服务进程，及时清理本轮子进程。缺失的 worktree/finishing 辅助技能不安装；采用当前已获授权的本地工作区和不提交交付。

目录基准 `/Users/hybsmac/Python项目/economics-experiment/my_platform`。Python `/opt/anaconda3/envs/otree_env/bin/python`；服务器框架源码 `/private/tmp/otree5115-verification.DSuLRA/package`。框架测试在导入前进入临时目录并设 `DATABASE_URL=sqlite:///db.sqlite3`、`OTREE_IN_MEMORY=''`，保留框架 PYTHONPATH。不得直接从项目根导入并初始化真实业务数据库。

设计文件：`docs/superpowers/specs/2026-10-09-protocol2-first-arrival-repair-design.md`。报告与原始证据只读。每任务先写失败测试、观察正确失败，再最小实现、覆盖回归、自审及独立任务审查。报告包含 RED/GREEN 命令及实际结果。

### Task 1: 前端 decision 独立优先发送

**Files:** Modify `_static/dynamic_bottleneck_round/room_release.js`; Test `dynamic_bottleneck_round/test_room_release_timeout.cjs`，按需新建同目录 `test_room_release_priority.cjs`。

- [x] **Step 1: 写并运行失败用例。** 使用现有 Node VM 的真实生产脚本、确定性 fetch/timer，模拟 operative 在途挂起、非决策退避和头/JSON 挂起；第 59 秒提交立即生成 decision 请求。核心断言：

```javascript
const h = harness();
const ack = h.calls[0];
ack.resolve({ok: true, json: async () => ({acknowledged: true,
  release_ts: 100, deadline_ts: 160, server_now_ts: 100,
  clock_id: ack.data.clock_id, clock_token: ack.data.clock_token})});
await flush();
await h.advance(59000);
h.submit();
assert.equal(h.calls.at(-1).data.kind, 'decision');
assert.equal(h.calls.at(-1).data.departure_minute, 474);
```

Run `node dynamic_bottleneck_round/test_room_release_priority.cjs`（或用例加入既有 timeout 文件后运行该文件）。预期当前脚本失败：在途 operative 或全局 retryTimer 阻止生成 decision。

- [x] **Step 2: 最小实现。** 将 decision 的在途/退避状态与非决策分开；每类仅一个在途，非决策继续 coalesce。decision 不等非决策的 Promise 或重试计时器；确认后只推进一次，保存首次选择，技术异常停止两通道。保留 fetch/JSON/probe 有界超时与时钟样本清理，旧 Live 路径不变。
- [x] **Step 3: 更新原串行调度测试。** 不再断言“decision 必须等 ACK 超时”，改按 kind 选择请求对象并分别断言立即 decision、有界 ACK abort、迟到响应不串入当前请求、manual 重试不换值、原生 POST 最多一次。
- [x] **Step 4: 回归与自审。** 运行全部 `test_room_release*.cjs`；报告准确 PASS 数和相关失败修复，不使用真实浏览器。无 Git commit。

### Task 2: 首次到达排序与故障前准入审计

**Files:** Modify `dynamic_bottleneck_round/sync_transport.py`, `sync_runtime.py`, `sync_journal.py`; Test `test_sync_transport.py`, `test_sync_journal.py`。不得修改业务集成或前端。

- [x] **Step 1: 写失败测试并观察。** 在现有 transport fixture 的私有 allowed_minutes 加入 475，重新签发同身份 token。暂停第一份 474 的 `_exchange_registered`，第二份 475 在服务器登记后仍未完成；释放第一份，检查两响应和日志均是 474。注册第二份的事件必须在真实 `register_pending` 成功之后设置，不依赖固定 sleep。关键断言：

```python
self.assertFalse(second.done(), 'later arrival must not confirm before the first')
release_first.set()
responses = await asyncio.gather(first, second)
for response in responses:
    self.assertEqual(json.loads(response[-1]['body'])['receipt']['departure_minute'], 474)
self.assertEqual(self.journal.receipt('s', 1, 'p')['departure_minute'], 474)
```

补充不同 shell token 同人共序、同时间戳登记序、早到/非法不抢占、取消/重试、首份持久化失败不降级、其他人/房间不受阻、排队有界及清理。另写故障前完成准入、业务先标 overrun、worker 后执行仍保留值与可信到达审计、响应不能健康且关闭失败的测试。

Run `python -m unittest dynamic_bottleneck_round.test_sync_transport dynamic_bottleneck_round.test_sync_journal`，但须通过临时工作目录/现有隔离 runner 执行，禁止初始化真实业务 DB。预期新排序/故障前审计断言失败；现有用例用于兼容证据。

- [x] **Step 2: 实现有界串行调度。** 每个 `(session_code, round_number, participant_code)` 在完整合法请求体的同一短准入边界登记顺序；包含不同 token。可用队列头完成后提交下一个的方式，无 worker 等待 predecessor Future。登记与最初调度分离，executor 操作不进入短锁。所有条目预先拥有 pending 和 slot，取消 HTTP 不取消持久化；异常也按序释放/调度，先装故障联锁再释放 pending。
- [x] **Step 3: 保留已接纳审计。** 记录服务器内部准入上下文（包括故障前准入标记，不能由 JSON 伪造）。当稍后出现技术异常时，日志仍可保存该请求及原始到达时间的审计信息，但不返回健康 manual 确认、不解除故障、不覆盖 canonical。直接 journal/transport 未授权调用不能获得这种权限。可采用明确独立审计字段并在报告说明接口；业务导出接入由 Task 3 完成。
- [x] **Step 4: 覆盖回归并报告接口。** 跑 transport/journal/runtime 相关现有测试，保持原 atomic close、dirty-fault 持久化、有限准入、日志替换和重启保护。报告新增字段/API及消费者要求。无 Git commit。

### Task 3: 原生表单、故障关闭与 RecoveryGate 衔接

**Files:** Modify `dynamic_bottleneck_round/__init__.py`, `sync_integration.py`, `decision_ingress.py`；按需修改 `sync_runtime.py` 的纯请求身份/故障边界。Test `test_room_sync_framework.py`, `test_decision_ingress.py` 及必要新框架测试。

- [x] **Step 1: 真实框架失败回归。** 使用既有 `run_scenario` 的临时 DB 和 actual ASGI GET/POST。将以下竞态注入放在真实 `Decision.error_message` 中，保留真实表单填充和真实业务消费者：

```python
original = e.Decision.error_message
def validate(player, values):
    asyncio.run(control_request(project_asgi.app, tokens[0],
        {'kind': 'decision', 'departure_minute': 474}))
    return original(player, values)
with patch.object(e.Decision, 'error_message', side_effect=validate):
    status, _, error = await post(codes[0], 475)
assert status == 302, error
saved = player(codes[0])
assert saved.departure_minute == 474
assert saved.decision_source == e.DECISION_SOURCE_MANUAL
```

分别加入截止后未关闭轮的真实 Decision POST 500 防健康 settled、已关闭历史重放无污染、迟到 POST 真实跳转 RecoveryGate/确认后 ResultsSync 且不会再次激活 dropout、pending manual 不能被自动覆盖。审计用 Task 2 的真实日志接口验证导出，异常仍 exclude。运行新场景，预期值、settled 或 RecoveryGate 路径断言失败。

- [x] **Step 2: 修复表单权威消费。** 使用已有 request-start preexisting snapshot 区分旧决定与本次表单值，强制校正后者为 canonical；保护既存自动/已结算结果，人工记录及重复 POST 幂等。不要全局移除“已有选择不能覆盖”保护。
- [x] **Step 3: 补当前轮原生故障边界。** 在运行时缓存经过业务验证的参与者/页面至 Session/轮次关联；中间件只用纯元数据记录故障/必要 pending。500 或 raised 事件先与 close 线性化，再解除 pending；关闭之前的故障均阻止健康认证，包括 T+60 以后。真正已关闭历史重放不联锁，且不改旧协议行为。不在 ASGI 主控制 loop 做 ORM/SQLite 查询。
- [x] **Step 4: 恢复流程及审计导出。** 确认迟到在原生推进前激活 RecoveryGate，pending 准时 decision 保留在原页面待确认，不提前生成自动值；结果 drain 仍有守卫。缺席同轮一次，恢复确认后补选只更新策略审计，不重新激活同次恢复。接入 Task 2 的异常审计字段到相应导出列，健康选择/成本不受影响。
- [x] **Step 5: 覆盖回归。** 跑新增与既有原生 framework、ingress、恢复和导出测试；使用服务器 frozen 5.11.5，并用当前 conda 框架做适量兼容检查。完整轮次测试暂留 Task 4；无 Git commit。

### Task 4: 整体验证与交接

**Files:** Create `docs/协议2首次到达修复与本地验证_2026-10-09.md`；如需补测试只修改上述受影响测试，不改业务规则。结果保存独立 `output/protocol2_repair_20261009/`，不覆盖 `output/review_20261009/`。

- [x] **Step 1: 一次整体验证。** 查阅既有隔离验证 runner，复制其必要隔离配置并输出到新目录。运行动态瓶颈回归、Room/问卷/支付等周边相关回归、所有 JS、一个 40 和两个 40 Session 的低资源接收/截止隔离验证。
- [x] **Step 2: 完整 33 轮。** 使用已有 `RUN_ROOM_SYNC_FULL_FLOW=1` 的明确验收夹具；真实 RL、外部付费 LLM 模拟。仅运行一次该耗时验收；不要运行另一份等价完整测试。预期所有 33 轮、2640 manual、问卷与支付结束通过；记录真实测得数量与时间，不从旧报告复制结果。
- [x] **Step 3: 处理回归失败。** 用 systematic-debugging 根因定位，先补失败断言再最小改动；只跑对应覆盖测试，不反复全套。不能放宽 60 秒或跳过恢复来使测试变绿。
- [x] **Step 4: 交接与最终检查。** 写明六项逐一结果、首次到达排序如何生效、异常审计和恢复如何保留、源模块和测试文件、测试命令及结果、尚未进行的服务器 PG/公共网络/实际浏览器验收。运行 `git diff --check` 与适量语法检查；确认没有本轮遗留测试服务/浏览器。无 Git commit。

## 审查与完成

每任务提交工作区差异包、报告和 task brief 给独立只读 reviewer，修复 Important/Critical 后才进入下一任务。由于不自动提交，差异包针对明确文件的工作区 diff，并包含新增测试；不得将用户无关改动纳入。最终对全部四任务净差异做一次架构/协议审查，不重复跑已验证的整套测试。审查若产生修复，覆盖测试复跑并重审。

## 本地实施收口（2026-10-09）

以上checkbox表示实施/验证步骤已执行，不表示所有单次运行均PASS。六项生产边界与额外审查问题已修复、RED/GREEN覆盖及最终只读审查完成；没有部署、付费API或Git提交。动态整套两个TCP受阻用例仅权限后局部重跑；一次完整33轮外层700秒超时，原临时库33轮/2640manual/66健康settled已完成，仅在新临时副本补验末尾960真实HTTP、80问卷/成本/现金/finished，不能称为连续完整验收通过。所有限制、源码快照差异与实际数值在公开交付及validation-summary明确记录。连续完整服务器验收仍属上线前必做，不通过放宽60秒或删页掩盖超时。
