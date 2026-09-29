# 双房间并发稳定性本地修复计划

> **For agentic workers:** 按测试驱动开发逐项原地执行；不提交 Git，不部署，不重启服务。

**Goal:** 修复混合 Session 中纯 Human 小组无法结算的问题，并减少同步页与结果页的重复工作。

**Architecture:** 保持 oTree 请求锁、统一结算锁及实验时限。Agent 入口按小组人数短路；结果展示复用已有的按轮公共反馈快照；未就绪同步页采用有上限的错峰轮询。

**Tech Stack:** oTree、Python unittest、浏览器 JavaScript、Node 内置测试工具。

## 已批准范围与不变量

依据 `docs/60人并发压力测试与修复建议_2026-09-27.md` 及用户“现在本地上进行修改”的授权执行。双 Session 各 40 Human（H30、HA10），保留标签分组与 Rooms。服务率、成本、信息条件、RL 更新和付费逻辑不变；决策窗口仍为 60 秒。无真实 API 调用或正式 Session 创建。

### 1. 无 Agent 小组短路

- [x] 在 `dynamic_bottleneck_round/test_concurrency_hardening.py` 使用真实 PAIR 配置构造 H 小组，验证启动返回 None、收集返回 []，不进入缓存、API 或 RL 历史读写。
- [x] 执行 `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_concurrency_hardening -v`，先观察失败。
- [x] 在 `dynamic_bottleneck_round/__init__.py` 的启动、收集、同步兼容入口及主 RL 入口加入 `count_per_group(session, group_id) <= 0` 返回。
- [x] 再跑上述测试及现有 Agent 集成测试，验证 HA 小组异步等待、缓存、降级与在线更新不回归。

### 2. 已结算与准备页查询减负

- [x] 增加测试：已结算组调用 `maybe_prepare_results` 不触发启动/补选；准备页每次渲染仅一次查询等待参与者。
- [x] 观察失败后，将 `results_ready` 检查放在准备结果函数入口；准备页复用一次 `round_waiting_players` 返回值并移除已经由 `mark_round_ready` 执行的第二次启动检查。
- [x] 补截止前、截止时、截止后的时限测试，确保没有延长或重置窗口。

### 3. 结果图复用结算快照

- [x] 增加等价测试：快照生成的出发分布与原来的真人加虚拟主体分布一致；重复读取不重新查询虚拟决策；旧 Session 无快照仍可使用原逻辑。
- [x] 观察失败后，`group_departure_distribution(group, public_snapshot=None)` 优先使用当前轮已保存的 `departure_outcomes`；成本图也接收可选快照。
- [x] `Results.vars_for_template` 只获取一次公共快照并传给两种图；不新增数据库字段或进程全局缓存。

### 4. 未就绪页错峰轮询

- [x] 增加 Node 测试：初始延迟不小于 1500ms，多次等待增至最多 4000ms，带随机偏移；页面路径隔离；就绪即清除计数并保持 200ms 跳转；sessionStorage 禁用时仍可轮询。
- [x] 观察失败后新增 `_static/dynamic_bottleneck_round/sync_poll.js`，接入 `RoundStartSync.html` 与 `ResultsSync.html`。不新增绕过 oTree 锁的接口。

### 5. 验证与交接

- [x] 执行动态场景全部 Python 单元测试、Rooms/标签兼容测试、付费和管理员模板回归、Node 测试与 `git diff --check`。
- [x] 使用 requesting-code-review 技能复核变更；记录本地回归证据及仍需在独立测试环境完成的 80 人双 Session、33 轮端到端压测与 SQL/CPU 定位。
- [x] 输出本地修改说明，不将单元测试或查询次数下降宣称为已经通过真实 80 人并发验收。
