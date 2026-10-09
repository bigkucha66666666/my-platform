# 保留完整流程的同步改进 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or inline execution with task review. Steps use checkbox syntax for tracking. User asked for in-place local changes: no commits, new worktrees, production writes or paid API calls.

**Goal:** 完整保留实验流程，使房间放行、计时与有效选择接收不被慢 oTree 请求阻塞。

**Architecture:** 新 Session 的协议标记为 2，旧 1/未标记 Session 保留原路径。独立 WAL SQLite 日志保存同步状态和有效选择；签名同源 HTTP 控制接口在主 ASGI 循环响应，原 oTree ASGI 在单业务线程运行，原事务锁不删除。业务代码读取日志、幂等导入选择和统一时间，再执行原结果与后续流程。

**Tech Stack:** Python 标准库 sqlite3/asyncio/HMAC，现有 oTree 5.11.5/6、uvicorn，JavaScript fetch，unittest/node:test。

## Global Constraints

- 不删减或合并任何页面；保留 3 轮热身、30 轮正式、H/HA 问卷、现金结算和结束页。
- 不新增管理员操作或监控页面；原 Room、实验标签和分组不变。
- 同 Session 所有本轮真人共享 T、T+60；不同 Session 独立。刷新不重置时间。
- I0 决策时不读取实际容量；I1 仅在 T 后显示。隐藏 HTML 与签名载荷不得泄漏容量。
- 已有效接收的准时选择不能因 oTree 排队改成超时；失败必须技术异常，不能默默补选。
- 保留 oTree 事务锁，不随意增加 worker。同步日志是事实源，RAM 不能替代持久化。
- 仅本地隔离验证，禁止自动 Git 提交、部署、访问生产数据库或调用付费模型。

## Task 1: 小型同步日志与幂等状态机

**Files:** Create `dynamic_bottleneck_round/sync_journal.py`, `dynamic_bottleneck_round/test_sync_journal.py`.

- [x] 写测试并观察预期失败；再实现以下 API（模块不导入 oTree）：

```python
class SyncJournal:
    def __init__(self, path): ...
    def ensure_round(self, session_code, round_number, *, expected_codes, now_ts,
                     decision_page_index, drain_limit_seconds, information_condition,
                     allowed_minutes, actual_capacity_display=None,
                     capacity_level_label=None, boot_id=None): ...
    def register_shell(self, session_code, round_number, participant_code,
                       shell_token, *, now_ts, ingress_ts=None, page_index=None): ...
    def state(self, session_code, round_number, *, now_ts=None,
              pending_codes=(), failed_codes=(), crossed_codes=()): ...
    def exchange(self, session_code, round_number, participant_code, shell_token,
                 *, kind, now_ts, departure_minute=None, processing_ts=None,
                 pending_codes=(), failed_codes=(), crossed_codes=()): ...
    def receipt(self, session_code, round_number, participant_code): ...
    def audit(self, session_code, round_number, participant_code): ...
    def mark_anomaly(self, session_code, round_number, reason): ...
    def mark_settled(self, session_code, round_number): ...
    def ensure_boot(self, boot_id): ...
```

`state` 返回可兼容 v1 的状态字典（不含私密容量）；now_ts=None 是纯读取，显式传值才推进时间状态。`exchange` 返回现有 JS 的公开 snapshot 字段；仅放行后 I1 加容量显示字段。`receipt` 为 `{kind, departure_minute, ingress_ts}` 或 None。选择接收响应加 `decision_received` 和 `decision_kind`；合法重复返回首次 canonical receipt，非法选择明确 ValueError。无 ACK 的选择拒绝；晚到/排除允许原业务推进但不构造 manual。状态无改变时不 UPDATE。operative 记录第一有效时间，不依赖 oTree 页码当前位置。状态统一使用现有纯 `room_release.py` 的时间/超时规则。exchange 的 now_ts 为锁前可信接收时间，processing_ts 默认为 now_ts，运行时显式传真实处理时间；准入使用前者，放行排期和 server_now_ts 使用后者，不能因 ACK 排队而把 T 安排在过去。

SQLite 路径必须是持久文件，连接关闭、事务异常回滚，WAL+FULL；最小独立表、参数绑定、支持线程/同文件重开，schema 版本不兼容时失败。shell_token 必须实际注册且对应身份/轮次/page。实际容量只保存私有字段，不进入 `state`。注册重载 shell 不重置 ACK/T。已 settled 状态不再接收新选择；已接收选择可幂等重放。boot_id 改变且未 settled 时标记技术异常；ensure_boot 供 runtime 首次加载时扫描并拒绝上个进程尚未结算的轮次，防止已有页面绕过新 GET 继续。

```python
def test_received_before_deadline_survives_reopen(self):
    journal = self.armed_journal()
    result = journal.exchange('s', 1, 'p', 'a' * 24,
        kind='decision', now_ts=self.release_ts + 59, departure_minute=474)
    self.assertTrue(result['decision_received'])
    reopened = SyncJournal(self.path)
    self.assertEqual(reopened.receipt('s', 1, 'p')['departure_minute'], 474)
```

- [x] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_journal -v`; cover 40/80 人、两个 Session、I0/I1、截止边界、缺席/技术异常、重开、重复、错误 nonce、status 无写入。
- [x] 独立任务复核；报告 RED/GREEN 命令与输出。无 Git 提交。

## Task 2: 独立 ASGI 控制与单业务线程桥接

**Files:** Create `dynamic_bottleneck_round/sync_runtime.py`, `dynamic_bottleneck_round/sync_transport.py`, `dynamic_bottleneck_round/test_sync_transport.py`. Modify `project_asgi.py` only for opt-in protocol 2 wrapping.

- [x] 先测试阻塞业务期间控制请求依旧响应、错误签名/Origin/方法/超大或不完整 body 拒绝、JSON 不泄漏、thread/contextvar 桥接、shutdown。
- [x] Runtime 懒加载 SyncJournal（`DYNAMIC_ROOM_SYNC_DB` 默认当前目录 `room_sync.sqlite3`），使用 settings.SECRET_KEY HMAC-SHA256 签名 `{session_code,round_number,participant_code,shell_token,page_index}`，容量不在 token 中；解析严格校验类型。只允许注册的 token 访问当轮。
- [x] Control path 固定 `/dynamic-room-sync`、POST JSON `{"token": ..., "kind": "ack|status|operative|decision", "clock_id": ..., "clock_token": ..., "departure_minute": ...}`。读取完整有限 body 后记录可信接收时间，executor 持久化成功后返回 JSON；no-store，异常 503/技术提示，取消不能让后台已开始的持久化处于无人登记状态。只有匹配 nonce 的 clock 标识回显。POST receipt 持久化中的 code/轮次需登记 pending，结算先等其完成。
- [x] `ThreadedASGI` 在一个专用线程/事件循环运行原 oTree app，receive/send 跨线程安全映射至主循环，传播原 ContextVars 和异常，断开正确取消连接。不删除全局锁；保持 HTTP、Live、Room 在同一业务循环。启动失败明确报错；lifespan shutdown 收尾线程/池，不残留。

```python
async def test_control_does_not_wait_for_slow_business(self):
    business = asyncio.create_task(self.request('/slow'))
    await self.business_started.wait()
    response = await asyncio.wait_for(self.control('status'), timeout=.5)
    self.assertEqual(response.status, 200)
    self.release_business.set()
    await business
```

- [x] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_sync_transport -v`；慢业务应包含真正同步等待，而非仅 async sleep，证明事件循环隔离。
- [x] 任务复核；无 Git 提交。

## Task 3: 业务与前端接入，不改变页面流程

**Files:** Modify `dynamic_bottleneck_round/__init__.py`, `Decision.html`, `_static/dynamic_bottleneck_round/room_release.js`; tests `test_room_release_framework.py`, `test_room_release.cjs` plus new `test_room_sync_framework.py` if clearer.

- [x] 先写失败的真实冻结框架协议 2 测试及 JS 单在途/提交失败重试测试。
- [x] 创建时接受环境 `DYNAMIC_ROOM_RELEASE_PROTOCOL=2`，要求正确 ASGI 初始化和有限 drain。v1 标记保留。v2 同步状态来自 Journal，组 T 只在业务需用时幂等同步，不在每次 status 重写历史。
- [x] Decision GET 注册真实 shell nonce；HTML 加 v2 control endpoint/token data 属性，token 无实际容量。保留 v1 live 路径及全部原模板/选择器。
- [x] JS 对 v2 使用 fetch 控制消息、单在途和有界重试；时钟沿用原单调区间。选择先 POST 持久化接收，成功后再原表单 POST；提交失败保留选择并提示重试，不能设置 submitted 后永久停止。超时自动选择仍用原规则。operative 在快速下一页时也可独立保存。
- [x] 业务 canonical receipt 优先，不信任原表单二次值；结果前将已接收 manual 幂等导入 Player 并运行原 record_manual_decision。pending receipt 未完成不得补选；异常不中断成有效轮。正常晚到和真实缺席继续原自动策略。历史有效选择/结果不覆盖。
- [x] export 从 journal 取同样的同步审计字段，业务处理/响应时间区分首次接收。ResultsSync 仍保留 Session 所有组结算屏障，原结果/问卷/支付继续。

```python
def test_flow_remains_33_rounds(self):
    self.assertEqual(experiment.C.WARMUP_ROUNDS, 3)
    self.assertEqual(experiment.C.FORMAL_ROUNDS, 30)
    self.assertEqual(experiment.C.NUM_ROUNDS, 33)
```

- [x] 聚焦命令：`python -m unittest dynamic_bottleneck_round.test_room_sync_framework -v` 与 `node --test dynamic_bottleneck_round/test_room_release.cjs dynamic_bottleneck_round/test_sync_poll.cjs`。测试真正非法表单、canonical 重复、晚到、刷新、I0/I1、跨轮/房间。各任务完整 review 后再全套。

## Task 4: 全流程、低资源负载与交接

**Files:** `dynamic_bottleneck_round/test_room_sync_framework.py`, `docs/同步机制改进_保留完整流程_2026-10-08.md`, this plan progress.

- [x] 在临时 SQLite 与服务器 oTree 5.11.5 包环境分别运行聚焦回归；不能打开项目正式 db.sqlite3。必要时仅复用已有本机依赖。
- [x] 40/80 低资源并发测试：慢业务、ACK/status、T+59 burst、迟到、连接恢复。记录每类样本与完整度，不把框架内 transport 性能当公网浏览器性能。
- [x] 显式 33 轮完整真实 HTTP 框架路径（模拟外部付费 LLM、真实 RL）检验 80 真人、每 HA10 LLM/10 RL、问卷、正式累计成本/现金/结束页；v1 回归也通过。
- [x] 全套 unittest、周边 questionnaire/payment/admin/room 回归、JS tests、compile/diff 检查，最终独立审查并修复 Important/Critical。
- [x] 文档给协议 2 入口/开关/日志路径/备份/单 worker 限制、旧会话兼容、服务端未完成验收点。结束所有本次测试进程，保留结果文件，不自动 commit 或部署。

## Task 5: 最终复核发现的控制故障安全补全

- [x] 保存失败 RED 回归、pending 移除前故障闩、原生自动补选/结算拒绝、原生提交并发/日志标记失败测试。
- [x] 独立复核指出的健康重试响应和队列恢复后自主落盘缺口修复；89 项聚焦运行两版本通过（各跳过 1 项显式完整流程），修正复核通过。
- [x] 文档/测试明确 32,768 个身份的进程生命周期上限；保留最后修补阶段 RED 输出未保存的证据限制，不冒充已观察的前置失败。

详细任务报告和最终整体复核进度见 `output/room_sync_20261007/progress.md`。

## Task 6: 整体复核最后三项边界补全

- [x] 请求响应头/JSON/探测超时、选择保留/钟样本清理；控制写入处理时间及实际提交完成超限；完整请求体的原子 pending/结算关闭边界。
- [x] 三项均有观察到的失败回归并修复；96 项聚焦两版本 OK（各显式跳过完整流程1项），最后 Node8项通过。
- [x] 独立整体修正复核通过，最后正常33轮/TCP80席位/旧协议通过，收尾进程与文档完成。未自动 commit 或部署。

本地交付批准；服务器 PostgreSQL、真实设备 T+2 可操作时点、完整介绍页 DOM、真实付费 Agent 与上线切换仍为独立启用门槛，不计作本次已完成工作。
