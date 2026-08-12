# Dynamic Round Start Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `dynamic_bottleneck_round` 增加每轮统一开始机制，并将等待页轮询间隔调整为 3 秒。

**Architecture:** 新增 `RoundStartSync` 参与者页面，使用当轮 `Player.round_start_ready` 记录到达，使用 `Group.round_start_deadline_ts` 和 `Group.round_started_at_ts` 实现全组到齐或超时放行。只有轮次正式开始时才统一写入 `decision_deadline_ts`，`Decision` 仅读取剩余时间。

**Tech Stack:** Python 3.10、oTree、oTree templates、原生 JavaScript、`unittest`、oTree bots。

---

### Task 1: 轮次同步状态与纯函数

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] 添加失败测试：第 1 轮同步最长等待为 120 秒，后续轮次为 60 秒。
- [ ] 添加失败测试：全组到齐时立即返回应开始，未到齐且未超时时不开始，超时时开始。
- [ ] 运行目标单元测试，确认因 helper 缺失而失败。
- [ ] 实现 `round_start_wait_seconds(round_number)` 和 `should_start_round(ready_count, group_size, now_ts, deadline_ts)`。
- [ ] 运行目标测试并确认通过。

### Task 2: 每轮统一开始与截止时间

**Files:**
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `dynamic_bottleneck_round/__init__.py`

- [ ] 添加失败测试：未开始轮次不得由 `Decision` 自行创建截止时间。
- [ ] 在 `Group` 增加 `round_start_deadline_ts`、`round_started_at_ts`和 `round_started`，在 `Player` 增加 `round_start_ready`。
- [ ] 实现 `mark_round_ready()` 和 `maybe_start_round()`：首位到达者只创建同步截止时间，全组到齐或超时时写入统一的决策截止时间。
- [ ] 删除上一轮结果计算中预先创建下轮决策截止时间的逻辑。
- [ ] 运行相关单元测试并确认通过。

### Task 3: RoundStartSync 页面与 3 秒轮询

**Files:**
- Create: `dynamic_bottleneck_round/RoundStartSync.html`
- Modify: `dynamic_bottleneck_round/ResultsSync.html`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] 添加失败的模板和 bot 契约测试：页面顺序包含 `RoundStartSync`，同步与结果等待页都使用 3000 毫秒轮询。
- [ ] 实现 `RoundStartSync(Page)`，进入时标记当轮已就绪，到齐或超时后自动进入 `Decision`。
- [ ] 新建简洁的等待页，显示已到达人数、同组总人数和最长剩余等待时间。
- [ ] 将 `C.SYNC_POLL_INTERVAL_SECONDS` 从 1 调整为 3，两个同步页共用该常数。
- [ ] 更新 bot 在每轮提交 `RoundStartSync`，然后运行新 app 完整流程。

### Task 4: 回归与稳定性验证

**Files:**
- Verify: `dynamic_bottleneck_round/*`
- Verify: `single_bottleneck/*`

- [ ] 运行 `python -m unittest dynamic_bottleneck_round.tests`。
- [ ] 运行 `python3 -m py_compile dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/tests.py settings.py`。
- [ ] 运行 `otree test dynamic_bottleneck_round_demo 5`。
- [ ] 运行 `otree test single_bottleneck_demo 5`。
- [ ] 运行 `git diff --check -- dynamic_bottleneck_round settings.py`。
