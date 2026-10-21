# 自动超时推进与协议 2 同步兼容修复 Implementation Plan

> **交接给下一对话：** 本文件是修复说明与验收计划，不是已完成的修复。请按 test-driven-development 先复现、先写失败测试，再修改实现。默认只在本地修复，不自动部署、不修改生产数据、不调用付费 API，不执行 Git 暂存、提交或推送。用户另行授权上线后，才执行服务器写操作。

**Goal:** 修复系统自动访问页面造成的错误就绪判定，避免一个未确认的系统页面访问锁住整场实验；阻止原生管理员强推绕过未结算屏障。

**Architecture:** 在任务发起端与项目 ASGI/业务回调边界区分可信系统导航和真实浏览器交互，使系统导航不产生浏览器就绪证据。保持协议 2 的统一放行、服务器准入时间、首次有效选择和技术故障保护；为原生强推增加项目内安全防护，不直接修改安装目录中的 oTree 源码。

**Tech Stack:** 现有 oTree、Python、Starlette/ASGI、ContextVar、PostgreSQL、持久化 SQLite 同步日志、unittest；不要求新增云服务或更换框架。

## 1. 先读结论

这次包含两个相关但不同的问题：

1. **参与者无法继续的直接原因：** 协议 2 在准备截止时把该轮标记为 `shell_not_acknowledged_or_page_queued`，随后同步接口返回 503。该轮没有放行时间，没有收到决策回执，也没有结算。
2. **后台按钮报错的原因：** 管理员随后使用 `Advance slowest user(s)`，原生 oTree 推进到了未结算 Results 页；模板上下文直接读取空的 `departure_time_label`，导致 HTTP 500。

两者不能混为“只是管理员按钮不好用”，也不能只用 `field_maybe_none()` 把异常文本消掉。必须修复系统导航来源与同步生命周期的兼容，并守住结果页的结算前提。

## 2. 已核实的现场证据

### 2.1 环境与版本边界

- 项目根目录：`/Users/hybsmac/Python项目/economics-experiment/my_platform`。
- 排查时本地 Git HEAD：`2278fc69679451697de34ff985daf1ae8cf53baa`，工作区存在用户未提交改动，不可回退或覆盖。
- 生产主机：`admin02@10.8.128.8`，项目在 `/home/admin02/econlab/my_platform`。
- 服务：`econlab-otree.service`，项目启动器监听 18000；排查时仍为运行状态，没有因排查重启。
- 生产解释器：`/home/admin02/econlab/py310/bin/python`，Python 3.10 / oTree 5.11.5。
- 本地现有解释器：`/opt/anaconda3/envs/otree_env/bin/python`，oTree 6.0.13。两版管理视图及 Participant API 存在差异，不能只验证本地版本。
- 排查时生产 191 个发布文件仍匹配 `/home/admin02/econlab/releases/cash_budget_20261015/manifest.json`。本地对应清单在 `output/deploy_cash_20261015/manifest.json`；实现前重新比较内容，不能只看 HEAD。
- 协议配置：`DYNAMIC_ROOM_RELEASE_PROTOCOL=2`、`DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS=10`。
- 同步日志：`/home/admin02/econlab/data/room_sync.sqlite3`，WAL 模式。生产业务库为 PostgreSQL。
- 本说明不记录 SSH 密码、数据库密码或 API Key。

以下为 2026-10-19 现场排查的历史快照，不是持续监控结论。上一对话只读查询 PostgreSQL、SQLite 和服务日志，没有重启、清状态、推进参与者或新增付费调用。

### 2.2 会话范围

| 会话 | 条件与组成 | 排查时状态 |
| --- | --- | --- |
| `1p3y9tv9` | I0，12 真人；H 为 9 真人，HA 为 3 真人 + 3 LLM + 3 RL | 原始轮 1–22 已结算；原始第 23 轮异常，12 人停在 ResultsSync |
| `1up9aimg` | I1，同样的 12 真人及 H/HA 组成 | 同步日志的 33 轮均为 settled，无记录的技术异常 |

两场使用 `dynamic_cost_budget_v2` 和提示词 v2。原始第 23 轮是正式第 20 轮；原始前 22 轮包含 3 轮热身和 19 轮正式实验。已结算不自动等于所有记录都是人工选择或通过了数据质量审核，后续应分开核验。

### 2.3 关键时间线，北京时间

| 时间 | 已观察到的事件 |
| --- | --- |
| 19:58:55.783743 | I0 原始第 23 轮开始准备，截止为 19:59:55.783743 |
| 19:59:46 | 超时进程记录 `Auto-submitting timed out page: /p/dog0mc3d/dynamic_bottleneck_round/Results/199` |
| 19:59:47 | 回环请求 `127.0.0.1` POST 该 Results 页，返回 302 |
| 19:59:49 | 回环请求 GET `/p/dog0mc3d/dynamic_bottleneck_round/Decision/205`，返回 200 |
| 截止前日志状态 | P007 的 shell ingress 为 19:59:47.384235，rendered 为 19:59:49.101254；没有 ACK |
| 19:59:55 | 多名参与者的 `/dynamic-room-sync` 从 200 转为 503；日志状态为 `TECHNICAL_ANOMALY` |
| 19:59:56–57 | P007 的实际客户端访问旧 Results，重定向到 Decision/205，晚于准备截止 |
| 19:59:58 | 该客户端的同步 POST 返回 503 |
| 20:02:17 | 第一次 `/AdvanceSession/1p3y9tv9` 请求返回 200 |
| 20:02:57 起 | 多次强推返回 500，堆栈落在 Results.vars_for_template 的 `player.departure_time_label` |

P007 对应 participant code `dog0mc3d`。上述标识仅用于定位这次历史事故，不能硬编码进修复。

### 2.4 同步状态与异常堆栈

第 23 轮日志：

```text
phase = TECHNICAL_ANOMALY
technical_anomaly = shell_not_acknowledged_or_page_queued
expected = 12
ack_count = 11
excluded_count = 0
receipt_count = 0
release_ts = null
deadline_ts = null
settled = false
```

强推堆栈的关键路径：

```text
AdvanceSession.post
  -> session.advance_last_place_participants
  -> participant._visit_current_page
  -> Results.vars_for_template
  -> 'departure_time': player.departure_time_label
TypeError: player.departure_time_label is None.
```

这条记录确定了“系统页面访问被计入待 ACK 的 shell”与后续整轮保护的关联。P007 的实际浏览器为何晚到，单靠现有日志不能确认，不能直接归咎于参与者、网络、硬件或 LLM。

## 3. 代码入口与检查重点

| 文件 | 关注点 |
| --- | --- |
| [project_prodserver.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/project_prodserver.py) | 当前使用单个业务 worker，并启动 `otree timeoutsubprocess` |
| [project_asgi.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/project_asgi.py) | 项目 ASGI 扩展、管理路由与中间件接入位置 |
| [decision_ingress.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/decision_ingress.py) | Decision GET 的 shell nonce、pending/failed/completed 与 ContextVar；当前不能以这些记录直接证明浏览器已经访问 |
| [sync_integration.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/sync_integration.py) | `state()` 初始化轮次；`snapshot(kind='render')` 注册 shell；`control_token()` 和结算屏障 |
| [room_release.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/room_release.py) | `mark_shell_rendered()` 与 `expire_preparation()` 对“已渲染未 ACK”的判定 |
| [sync_journal.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/sync_journal.py) | 持久状态、shell、回执和 settled；旧记录兼容 |
| [sync_runtime.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/sync_runtime.py) | 进程内故障闩与原生请求登记，不能只清 SQLite 异常 |
| [动态场景实现](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/__init__.py) | Decision.is_displayed / vars_for_template 可能触发状态；ResultsSync 的结算屏障；Results.vars_for_template 直接访问空结果 |
| [session_data_export_views.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/session_data_export_views.py) | 项目内受保护管理路由的现有接入示例，不是强推修复本身 |

还必须读**目标版本** oTree 安装目录中的 `tasks.py`、`cli/timeoutsubprocess.py`、`models/participant.py`、`models/session.py` 和 `views/admin.py`，但不得直接编辑这些第三方安装文件。默认超时任务会通过 HTTP 推进页面，urllib 可继续跟随重定向；原生管理员推进还可能直接调用页面回调，不能只关注一条普通 GET 路径。

## 4. 不允许以修复名义改变的行为

- 保留 3 热身 + 30 正式轮、H/HA 与 I0/I1、固定服务率序列、排队结算、LLM 提示词、RL 策略、问卷和版本化报酬；不修改论文。
- 保留同一 Session 的统一放行及 T 到 T+60 决策窗口，保留现有 10 秒 drain 语义。不能只把准备时间或决策时间加长来掩盖本问题。
- 保留服务器可信接收时间、canonical receipt、首次有效选择、重复提交幂等、跨 Session 隔离及 I1 放行前保密。
- 真实浏览器页面失败、请求跨截止、ACK/决策持久化失败、进程重启等真正技术故障仍应 fail closed，不能一概改判真人缺席。
- 不能关闭全部 timeout worker 或取消已有超时、补选、RecoveryGate、掉线恢复链路。
- 不以客户端自报时间、User-Agent、一个未认证自定义请求头或 `127.0.0.1` 单独作为可信系统来源。经 Nginx 反代的真人请求也可能来自回环地址；现有测试客户端同样使用回环地址。
- 不将未放行轮次造为已结算，不给缺失成本填 0，不将自动或回退行为伪标人工选择/LLM 成功。

## 5. 分步修复要求，先红后绿

### Task 1：建立失败回归，覆盖真实任务链

- [ ] 在 [test_room_sync_framework.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_room_sync_framework.py) 中增加独立子进程场景，使用冻结后的真实 oTree 模型和临时数据库。
- [ ] 建立 12 人的 I0 测试 Session，另一独立 I1 Session 作隔离对照；不使用生产会话代码或参与者数据。
- [ ] 让 11 个浏览器确认，最后一人仍停在上一轮 Results；执行真实超时任务的 POST 与重定向访问，随后跨过准备截止，再让迟到浏览器进入。
- [ ] 原代码应出现本次 fault；修复后的期望是“内部访问本身不产生待浏览器 ACK 的证据”。真实缺席按既有规则排除/代理，其他人继续；迟到者不获得不合法的当轮人工决策资格，并按既有规则恢复后续轮次。
- [ ] 测试同时核对最终真人选择来源、自动策略、结果与恢复流程，不只断言 HTTP 200。
- [ ] 增加“超时任务是新一轮首个访问者”的场景，检查其是否提前建立准备时钟；不能只修本次它不是首个访问者的情况。
- [ ] 保存真实 RED 输出，再动实现。测试必须执行任务链，不以直接修改 Session vars 或手工调用 `mark_shell_rendered()` 代替框架验收。

以下是上一对话已经运行的**纯状态机诊断复现**，不是新增缺陷回归，也不是修复证明。它说明现有分类为何会锁住房间，不能通过删掉这个保护分支直接“修绿”：

```python
import importlib.util
from pathlib import Path

path = Path('/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/room_release.py')
spec = importlib.util.spec_from_file_location('isolated_room_release', path)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

codes = [f'P{i:03d}' for i in range(1, 13)]
state = release.make_round_state(round_number=23, now_ts=0, expected_codes=codes)
for code in codes:
    if code != 'P007':
        release.acknowledge_shell(state, code, now_ts=20)
# 这里模拟被上层错误纳入浏览器 shell 的系统访问。
release.mark_shell_rendered(state, 'P007', now_ts=53.318, ingress_ts=51.600)
release.expire_preparation(state, now_ts=60.1, pending_codes=(), drain_limit_seconds=10)
assert state['technical_anomaly'] == 'shell_not_acknowledged_or_page_queued'
assert state['release_ts'] is None
```

### Task 2：修复可信来源与初始化副作用

- [ ] 优先在项目自己的任务启动/请求上下文边界实现可信来源区分。若需专用 worker 入口，可新增项目根目录 `project_timeout_worker.py`，并由 `project_prodserver.py` 启动；先核对 oTree 原命令的 setup、任务消费与事务语义，不复制一个不完整 worker。
- [ ] 若使用内部 HTTP 标识，必须由受控任务进程生成并经服务端认证，覆盖直接 GET、POST 以及每次重定向后的请求；没有有效认证的请求按普通浏览器处理。不要改用另一条绕过统一业务事务锁的业务写接口。
- [ ] 上下文应能穿过 ThreadedASGI、Starlette 同步线程回调，并在所有正常、异常、取消出口复原；并发请求之间不能泄漏系统身份。
- [ ] 在 `state()`、`snapshot(kind='render')`、Decision.is_displayed、shell 注册与 control token 创建等位置落实语义：系统导航可以维持必要的原生推进，但不能替真人 ACK、为其生成就绪证据，或仅因系统预访问而启动/重置浏览器准备时钟。
- [ ] 同时检查 ingress 的 pending/failed/completed 与 crossed 记录。只跳过 `register_shell()` 而留下同一个系统访问的 queued/failure 证据，仍可能误报技术异常。
- [ ] 可以在任务源头限制无意义的 Decision 自动访问，但必须覆盖直接访问与重定向，并用真实任务测试证明没有遗漏原生跳页的 `is_displayed()` 副作用。不要把“禁止 urllib 跟随一个 302”当作完整修复。
- [ ] 未经真实浏览器确认的自动导航，不能输出真实 I1 当前容量或被当作客户端已获得可操作界面。
- [ ] 新增审计信息应明确区分系统导航和浏览器交互；旧日志缺少新字段时采用兼容读取，不重置或反向改写历史记录。

### Task 3：保护管理员强推与结果页

- [ ] 先针对协议 2 动态 Session 写真实管理 HTTP 回归：缺少放行、存在故障、缺少选择或尚未结算时，强推不得进入 Results，不得部分推进一批人再报错。
- [ ] 最小安全方案可以**禁用协议 2 动态 Session 的原生强推功能**，明确显示“不能绕过同步/结算屏障，请使用受控恢复流程”。通过项目管理路由扩展和必要的模板提示实现，不改 site-packages。
- [ ] 后端必须防护直接 POST，不能只隐藏按钮；保留原生管理员鉴权，未登录者不能读取故障详情或操作会话，非目标应用/旧协议的既有功能不误伤。
- [ ] 拒绝应返回受控状态和清晰原因，例如 HTTP 409，而不是未处理 500。若继续使用原生前端，也要处理其通用错误框，避免用户只能看到“查看日志”。本地 oTree 6 与目标 oTree 5 的模板/接口必须分别核对。
- [ ] Results 只在真实结算成功后展示费用、出发/到达时间和现金结果；未就绪时保持等待或显示真实技术异常。不可只把空字段显示为空字符串或 0 后假装正常完成。
- [ ] 正常结算后的 Results、问卷、支付、导出和已完成 Session 回看保持原行为；不得因阻止强推而把已完成数据写回。

### Task 4：必须覆盖的验收矩阵

| 场景 | 必须证明的结果 |
| --- | --- |
| 真实任务 POST Results 并跟随重定向 | 不凭空生成待真人 ACK 的 shell，不能重现本次整轮误锁 |
| 系统导航为新一轮首个访问 | 不能仅由自动导航消耗真人准备时间；真实访问按明确语义启动且只启动一次 |
| 回环地址的真实浏览器、普通客户端伪造系统标识 | 都不能凭 IP/自报字段获得可信内部身份；真实浏览器仍正常 ACK |
| 真实浏览器已加载但 ACK 缺失，或页请求确实失败/跨截止 | 原有技术异常保护仍存在，不吞故障、不一律改判缺席 |
| 真缺席与迟到后重连 | 既有排除、代理、RecoveryGate 与后续重新加入有效，不重复计超时 |
| 两个独立 Session 同时推进 | 一个场次的准备/故障/自动任务不影响另一个场次 |
| 准时控制选择、迟到选择、重复提交 | 首次有效回执和截止判定不变，不因原生队列覆盖人工选择 |
| 未放行或未结算时调用 AdvanceSession | 明确拒绝，索引、轮次、回执、结果与已结算数据不改变 |
| 异常后刷新与重复控制请求 | 不自动清除真实故障、不重复结算、不让旧令牌越权进入新轮 |
| 重启、日志丢失、持久化失败、提交回滚 | 既有 fail-closed、故障留痕及 settled 提交顺序不退化 |
| 33 轮和尾部页面 | 明确跑完 3+30 轮、H/HA 问卷、按保存版本付款和结束，退出码为 0 |

已有测试参考：[test_decision_ingress.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_decision_ingress.py)、[test_room_release.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_room_release.py)、[test_sync_journal.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_sync_journal.py)、[test_room_sync_framework.py](/Users/hybsmac/Python项目/economics-experiment/my_platform/dynamic_bottleneck_round/test_room_sync_framework.py)。其中已有“真实浏览器 rendered 但没 ACK 应异常”的测试应保留；本次需要补的是系统导航来源，不是删掉旧断言。

### Task 5：隔离回归与报告

不要在本地已有 `db.sqlite3` 或生产 PostgreSQL 上运行创建会话、超时推进或强推测试。可用下面的隔离入口运行现有回归；新增测试应加入对应已有模块或在清单中明确追加。

```bash
/opt/anaconda3/envs/otree_env/bin/python - <<'PY'
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path('/Users/hybsmac/Python项目/economics-experiment/my_platform')
modules = [
    'dynamic_bottleneck_round.test_decision_ingress',
    'dynamic_bottleneck_round.test_room_release',
    'dynamic_bottleneck_round.test_sync_journal',
    'dynamic_bottleneck_round.test_sync_transport',
    'dynamic_bottleneck_round.test_room_sync_framework',
    'dynamic_bottleneck_round.test_room_sync_load',
    'dynamic_bottleneck_round.test_project_prodserver',
    'dynamic_bottleneck_round.tests',
    'dynamic_bottleneck_survey.tests',
    'payment_info.test_cash_payment',
    'payment_info.test_cash_payment_framework',
    'test_session_data_export',
    'test_session_data_export_framework',
    'admin_template_compatibility_tests',
]
with tempfile.TemporaryDirectory(prefix='timeout-origin-regression-') as temporary:
    for entry in root.iterdir():
        if entry.is_dir() and (entry.name.startswith('_') or (entry / '__init__.py').is_file()):
            Path(temporary, entry.name).symlink_to(entry, target_is_directory=True)
    env = dict(os.environ, PYTHONPATH=str(root), DATABASE_URL='sqlite:///db.sqlite3',
        OTREE_IN_MEMORY='', OTREE_PRODUCTION='0', OTREE_ADMIN_PASSWORD='isolated-test-only',
        DYNAMIC_ROOM_RELEASE_PROTOCOL='',
        DYNAMIC_ROOM_SYNC_DB=str(Path(temporary, 'journal.sqlite3')),
        DEEPSEEK_AGENT_API_KEY='isolated-no-paid-provider',
        DEEPSEEK_AGENT_BASE_URL='http://127.0.0.1:9')
    # Framework children set protocol 2 themselves; outer unit tests also cover v1.
    subprocess.run([sys.executable, '-m', 'unittest', '-v', *modules],
        cwd=temporary, env=env, check=True, timeout=1800)
PY
```

- [ ] 新增框架场景先断言 engine 的后端与路径指向本子进程临时库，再调用 oTree setup、建 Session 或启动任务。
- [ ] 测试超时进程时只处理自己测试库里的任务；不允许连到 18000、继承正式 DATABASE_URL、使用正式同步日志或操纵现有参与者。
- [ ] 外部 LLM 全部模拟；若错误路径意外访问外部网络，要立即终止，不能沿用正式 Key。
- [ ] 在同样隔离前提下设置 `RUN_ROOM_SYNC_FULL_FLOW=1` 明确运行完整流程测试；默认跳过此项不能报告“33 轮已验收”。外层超时或非零退出也不能用尾部看似完成的数据改判 PASS。
- [ ] 运行现有 JS 测试 `test_room_release.cjs`、`test_room_release_timeout.cjs`、`test_sync_poll.cjs`，命令为 `node` 加对应文件的绝对路径，Node 版本先核对。服务器已提供 Node 22，勿为测试更换系统级 Node。
- [ ] 必须在目标 oTree 5.11.5 的隔离环境再跑。可用 `/home/admin02/econlab/py310/bin/python`，但脚本路径、PYTHONPATH、工作目录、数据库与同步日志必须改为独立暂存/临时目录，不能拿生产目录当测试工作目录。
- [ ] 如需 PostgreSQL 并发验收，仅在明确授权后创建自己命名的隔离数据库或 schema，校验所有进程的连接隔离；不能指向 public。
- [ ] 报告 RED/GREEN、每项测试真实版本、运行数/失败/跳过、退出码、是否覆盖真实超时任务与重定向。不要把纯状态机复现、ASGI 模拟浏览器、回环 TCP 和真实浏览器验收混称同一结果。

## 6. 已故障场次的处理边界

**修代码与恢复历史故障场次是两个独立任务。** 新代码不能自动决定旧 I0 场次继续、重跑或作废。

- `1p3y9tv9` 前 22 原始轮及其 manual/自动/Agent 审计应保留；第 23 轮没有放行和回执，不能按完整正式轮导出或付款。
- 不直接把第 23 轮改成 settled，不手工改参与者页索引、补出发时间或移除技术异常来续跑。
- 故障同时可能在持久日志、原生数据库和进程内故障闩中存在。只改一个 SQLite JSON 标记不能视作恢复，重启也可能触发新的保护。
- 若用户后续要求恢复旧场次，需要另外给出有审计的恢复方案：备份、确认实验已停止、判定重跑/跳过规则、记录新旧轮次关联并经用户确认。更安全的默认是保留事故场次、修复验收后创建新场次，不伪造连续完整数据。
- I1 同步日志显示 33 轮已结算，不能为了修 I0 回滚整个业务库或整体替换数据库，导致已完成场次丢失。
- 未完成 30 正式轮的参与者报酬应另行人工确认，不能修改成本或轮数来套用完整结算公式。

## 7. 后续获准上线时的门槛

- [ ] 重新只读核对目标主机、服务、依赖版本、源码差异、活跃参与者和仍存在 Session 的未结算轮次。旧孤立日志不能与真实运行场次混淆。
- [ ] 有正在进行的实验就不重启；故障会话未处理时也不能把“无近期请求”当作安全更新的充分条件，先取得维护和旧场次处置授权。
- [ ] 备份 PostgreSQL、同步 SQLite 的 WAL 安全快照、旧源码、服务配置及受限权限的环境文件；记录恢复路径并校验可读。
- [ ] 不上传本地数据库、不覆盖 `.env`、不执行 `otree resetdb`、不升级依赖来替代修复。新内部任务凭据若需要，应另行安全注入，不打印在报告中。
- [ ] 保持单 worker；若改任务启动方式，核对旧子进程已随服务停止、新 worker 只有一个，不能遗留两个超时进程消费同一库。
- [ ] 上线后核对管理员鉴权、受控强推拒绝、自然跳页、两房间隔离、数据导出；生产参与者路径不得被拿来做破坏性验收。
- [ ] 对比原有业务表、Session 和历史已结算日志的内容，确认没有改写；新故障保护对旧会话的兼容必须说明。
- [ ] 明确区分“本地修复完成”“目标版本隔离测试通过”“已部署”“真实浏览器验收通过”，没有做的项目单列。

## 8. 发给另一个对话的任务文本

> 请按本文修复项目的“超时自动推进与协议 2 同步兼容”问题。先用真实 oTree 超时任务和重定向链写失败回归，再修复系统导航来源、准备时钟/shell 记录，并保护管理员强推和未结算结果页。保留真实浏览器缺 ACK、持久化失败、截止判定及首次选择等现有保护，不能只把空字段设默认值或扩大超时时间。保持实验设计、Agent 策略、问卷、报酬和历史数据不变。先只在本地和隔离环境修复，完成目标 oTree 5.11.5 与本地版本回归，提交修改文件及真实测试结果给我确认；不要自动部署、重启、恢复旧故障场次、调用付费 API 或进行 Git 提交。本文的事故记录是排查时快照，进行生产操作前必须重新核对。

本轮交付仅为本说明；没有实施上述修复，没有重新运行完整回归，没有再次连接或修改生产服务。
