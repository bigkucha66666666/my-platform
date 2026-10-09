# Task 4：低资源验收与交接

日期：2026-10-08。新增文件仅为聚焦测试、交接文档和本报告；没有修改已审查的生产模块，没有部署、commit、依赖安装、浏览器操作或付费 API 调用。Task 4 没有查询或写入项目 `db.sqlite3`。最终修正的所有子进程在导入应用/oTree 前 `chdir` 到 TemporaryDirectory，并强制设置 `DATABASE_URL=sqlite:///db.sqlite3`、`OTREE_IN_MEMORY=''`；实际 engine 必须是解析到临时目录 `db.sqlite3` 的 SQLite，内存模式必须关闭。保留 inherited PYTHONPATH，日志也在临时目录。

## 文件

- `dynamic_bottleneck_round/test_room_sync_load.py`
- `docs/同步机制改进_保留完整流程_2026-10-08.md`
- 本报告 `output/room_sync_20261007/task-4-report.md`

## 最终聚焦命令与输出

工作目录：`/Users/hybsmac/Python项目/economics-experiment/my_platform`。

已安装 oTree 6.0.13，来源 `/opt/anaconda3/envs/otree_env/lib/python3.10/site-packages/otree/__init__.py`：

```sh
/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_room_sync_load -v
```

```text
test_marker1_native_live_sessions_under_protocol2_wrapper ... ok
test_two_sessions_80_loopback_control_and_deadline_receipts ... ok
Ran 2 tests in 11.737s
OK
```

服务器源码包 oTree 5.11.5，来源 `/private/tmp/otree5115-verification.DSuLRA/package/otree/__init__.py`：

```sh
env PYTHONPATH=/private/tmp/otree5115-verification.DSuLRA/package:. /opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_room_sync_load -v
```

```text
test_marker1_native_live_sessions_under_protocol2_wrapper ... ok
test_two_sessions_80_loopback_control_and_deadline_receipts ... ok
Ran 2 tests in 11.742s
OK
```

两个隔离修正后最终测试进程均 exit 0。之前的 11.544 秒/11.420 秒以及主任务独立 11.134 秒通过记录属于首次隔离审查前版本，不替代这次修正后验收；主任务记录保留于 `root-validation.md`。两次最终自测在并行运行不同版本子进程时采样，不把这些本机延迟解释为服务器容量或严格性能上限。每种版本的两个 child 均输出：

```text
ROOM_SYNC_DATABASE_ISOLATION {"backend": "sqlite", "database": "db.sqlite3", "in_memory": false, "temporary_directory": true, "inherited_external_url_overridden": true}
```

语法检查 `/opt/anaconda3/envs/otree_env/bin/python -m py_compile dynamic_bottleneck_round/test_room_sync_load.py` exit 0。三个新增文件各自用 `git diff --no-index --check /dev/null <文件>` 检查无空白错误。工作树整体 `git diff --check` 报出已有论文 `papers/dynamic_bottleneck_human_agent/main.log` 的 4 处尾空白；该文件不属于本任务，未修改。

## 真实回环 TCP 负载

使用现有 uvicorn、stdlib asyncio streams 和自己拥有的 `127.0.0.1` 临时监听 socket。没有 httpx/aiohttp、浏览器或新依赖。真实 `SyncJournal`、`SyncControlASGI` executor（仅 2 个控制线程）、`ThreadedASGI`（1 个业务循环）参与处理。

业务 ASGI 夹具调用同步 `threading.Event.wait` 阻塞整个业务循环。先分别为 Session A 的 40 人、Session B 的 40 人发 ACK，断言 A 已 ARMED 时 B 尚未 release、两个自然生成的 T 不同；然后并发发送 80 status。所有控制请求完成时业务仍未释放。I0 和尚未到 T 的 I1 都没有当前容量；T 后仅 I1 返回当前容量。

边界阶段仅替换 `sync_transport.time` 的可信测试时钟并写入临时 journal 的 T fixture：T=1941、T+60=2001、完整请求体到达=2000（T+59）、业务消费=2002（T+61）。`DECISION_SECONDS == 60` 断言保留，未缩短生产决策时间。80 个控制决策均在业务仍阻塞时提交成功，重新打开真实 journal 后逐条读出并断言 `{kind: manual, departure_minute: 474, ingress_ts: 2000}`；随后释放业务，80 个原生业务夹具请求在截止后读取同一 canonical receipt，全部响应 204。

这 80 个原生请求是 ASGI 业务夹具，**不执行 oTree ORM 提交或真实结算**；真实冻结 ORM/结算由下述兼容测试及主任务全 33 轮验收负责。本测试不声称验证了 PostgreSQL 的队列吞吐。

80 次重放各自建立新的 TCP 连接，发送第二个值 475，返回和持久记录均仍为首次的 474。另有 1 个已存在 receipt 的重复请求主动关闭连接、丢弃回复，然后重试确认 canonical 值仍在。此项验证丢失回复后的幂等恢复，不声称它覆盖了首个 receipt 写入瞬间断线的所有竞态；取消/pending 的细粒度行为另由已有 transport 回归覆盖。另起第二轮的独立 late_actor，完整决策体在截止后 0.001 秒到达，真实接口返回 late，journal 中 departure_minute 为 None。

| 完整度类别 | 已安装 6.0.13 | 源码包 5.11.5 |
| --- | ---: | ---: |
| ACK 成功/预期 | 80/80 | 80/80 |
| status 成功/预期 | 80/80 | 80/80 |
| 截止前 durable manual | 80/80 | 80/80 |
| 截止后业务夹具消费 | 80/80 | 80/80 |
| 新连接 canonical 重放 | 80/80 | 80/80 |
| 丢失回复后恢复 | 1/1 | 1/1 |
| 真正 late 新回执 | 1/1 | 1/1 |

延迟用实际 `time.monotonic` 从连接开始到完整响应测量，单位毫秒；与合成边界时钟完全分开。ACK 为两个 Session 各 40 的两次突发合计 80；其他 80 样本类别为单次 80 并发。

| 版本 | 请求类别 | 样本数 | 中位数 ms | P95 ms | 最大 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| installed 6.0.13 | ACK | 80 | 22.106 | 43.112 | 48.995 |
| installed 6.0.13 | status | 80 | 40.822 | 61.862 | 64.193 |
| installed 6.0.13 | decision receipt | 80 | 48.316 | 76.594 | 81.092 |
| installed 6.0.13 | native fixture | 80 | 183.765 | 184.555 | 184.705 |
| installed 6.0.13 | replay | 80 | 40.812 | 66.764 | 69.144 |
| installed 6.0.13 | late | 1 | 1.602 | 1.602 | 1.602 |
| source 5.11.5 | ACK | 80 | 23.296 | 39.114 | 47.582 |
| source 5.11.5 | status | 80 | 36.640 | 63.178 | 65.115 |
| source 5.11.5 | decision receipt | 80 | 45.816 | 73.408 | 76.341 |
| source 5.11.5 | native fixture | 80 | 179.911 | 180.696 | 180.818 |
| source 5.11.5 | replay | 80 | 39.252 | 62.812 | 65.472 |
| source 5.11.5 | late | 1 | 1.641 | 1.641 | 1.641 |

这里 native fixture 的延迟包含它等待测试释放业务循环的时间，不是业务数据库处理耗时。样本无缺失，不把缺失响应记为零。P95 用排序后 ceil(0.95*n) 分位；late 的单样本不构成延迟分布。

## marker 1 在新入口下的兼容验收

另一独立临时子进程先设置协议 2，执行标准 oTree setup、导入 `project_asgi` 并初始化 journal，再以协议 1 创建两 Session。复用真实冻结 HTTP/Live helper 的第 4 轮，只有外部付费 LLM 被模拟，RL 和 H/HA 结算真实执行。

两版本均通过：2 Sessions、80 真人、81 次 Decision GET、80 次 native Live ACK/手动表单 POST、H30/HA10+10LLM+10RL 结算、共同 T/T+60、刷新及重连保持 T。marker 仍为 1；Decision GET 无非空 `data-control-token`。模板保留空属性是正常兼容行为，不能把空属性本身误当作协议 2 令牌。该验收通过本地直接 ASGI 队列调用真实消费者，不是网络 WebSocket/浏览器性能测试。

## 主任务提供的独立集成证据

以下没有由 Task 4 重复启动；精确命令、输出及版本限制见 `root-validation.md`：

- server source 5.11.5 全 33：1 项通过、349.700 秒。该运行早于最终 after-success 回调线程化；最终回调和真实提交失败由 18 项 source5 聚焦回归补验（0.719 秒）。
- installed 6.0.13 最终审查代码全 33：1 项通过、358.058 秒。
- 两个全流程均为 2 Sessions、80 真人、2640 manual、1320 虚拟结果、真实 RL、H/HA 问卷、30 正式轮成本合计、现金舍入和结束回调；付费 LLM 模拟，首次 Decision 前有 fixture 推进。
- 最终 dynamic discover：installed6 440 项通过、74.424 秒；source5 440 项通过、74.071 秒；两者各 2 个 opt-in 完整流程显式跳过。这两批运行先于本次新增聚焦测试。
- 周边 access-label/问卷/payment/Room/admin/export：各版本 46 项通过，installed6 0.015 秒、source5 0.017 秒。
- JS：7 项通过、56.2 毫秒。

## 失败定位与资源清理

Task 4 首轮独立审查发现 Important 隔离缺口：子进程继承 DATABASE_URL/OTREE_IN_MEMORY 时，仅 chdir 不能保证 SQLite。按测试驱动步骤先加入安全 tripwire：两个覆盖测试各自仅向 child 环境放入 `DATABASE_URL=postgresql://isolation.invalid/never_connect`、`OTREE_IN_MEMORY=1`，然后在任何应用/oTree 导入前断言必须被覆盖为临时 SQLite。未修正时执行上述 installed6 聚焦命令，得到以下 RED；两个 child 都在导入前停止，不尝试连接伪 URL，也未测试任何真实外部数据库：

```text
test_marker1_native_live_sessions_under_protocol2_wrapper ... FAIL
test_two_sessions_80_loopback_control_and_deadline_receipts ... FAIL
AssertionError: database isolation failed before imports
Ran 2 tests in 0.089s
FAILED (failures=2)
```

最小修正只在 `run_child` 临时 chdir 后、任何应用/oTree 导入前 pin 两个环境变量，保留导入前断言；随后导入 database 并断言实际后端为 sqlite、engine 文件路径为该临时目录、IN_MEMORY 为 false。两个原覆盖测试继续携带伪继承配置，故每次验收都重复检测此隔离行为。之后原样重跑上述 installed6/source5 命令，分别 2 项 GREEN、11.737/11.742 秒、exit 0。该修正不涉及生产模块、数据库迁移或外部数据库连接。

首次 RED 为临时目录没有 `_static`，应用导入阶段就失败；依据框架 helper 的同样隔离方式添加只读源码 symlink，未改生产模块。下一次回环 bind 被 workspace sandbox 拒绝；经限定测试命令的执行授权后运行真实临时回环端口。兼容测试的首个断言错误把空 control-token 属性当作非空令牌，依据真实模板改为禁止非空令牌，重新执行通过。以上均为测试夹具或沙箱限制，未发现生产缺陷。

成功路径 `finally` 设置 release、清空拥有的请求、停止拥有的 uvicorn task、关闭监听 socket、关闭 ThreadedASGI 和 executor，断言 business thread 不再存活；再 reset_runtime，离开 TemporaryDirectory 删除临时测试文件。最终输出均含 cleanup 标记，两版本测试子进程和主任务独立验收子进程均已正常退出。

## 验收边界与交接

本机 TCP 延迟不证明生产服务器 PostgreSQL、校园网/公网或 80 台设备的显示时差，可信测试时钟也不代表真正等待 59 秒后的服务器资源分布。未完成服务器 PostgreSQL 的 40/80 人 T+59 突发、分布式真实浏览器 T 到 T+2 可操作、介绍/理解页 DOM 全交互、真实外部 LLM 性能及生产切换；不据此宣布正式数据采集的同步公平性达标。

交接文档明确保留 33 轮、H/HA 问卷/现金/Room/标签；列出签名快速控制和单业务循环/原生事务锁、新 Session opt-in 与旧兼容、准确 launcher/环境变量、持久 SQLite 路径与 WAL-safe 备份、单 worker 限制、重启/日志丢失 fail closed，以及上述未完成的服务器/公网浏览器门槛。没有自动部署或 commit。
