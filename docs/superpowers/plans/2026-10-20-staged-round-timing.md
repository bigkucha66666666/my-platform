# Staged round timing implementation plan

**Goal:** 按用户确认的 60/40/30 秒决策及 50/40/30/30 秒结果观看规则运行新建 Session。

**Architecture:** 新增独立计时规则模块并随 Session 冻结 33 轮参数。普通页面及两个同步协议读取相同规则；旧 Session 和已初始化同步状态保留原值。

**Tech Stack:** Python 3.10、oTree 6.0.13/5.11.5、unittest、临时 SQLite、现有 Node 前端测试。

- [x] 新增 `dynamic_bottleneck_round/test_staged_timing.py`，先运行隔离测试，确认后期时限仍为 60/50 而失败。
- [x] 新增 `round_timing.py`；在 `creating_session` 保存快照，`maybe_start_round`、Decision、Results、准备页读取规则。
- [x] `room_release.make_round_state`、`SyncJournal.ensure_round` 接收并保存决策秒数；同步集成在创建轮次时传入，放行只使用保存值。
- [x] 更新 Decision/RoundStartSync 模板及 `room_release.js` 的时长提示，覆盖 40/30 秒前端截止。
- [x] 在真实冻结 ORM 的隔离数据库中验证轮次边界、I0/I1、页面提示、控制回执及结果页；运行双版本相关回归与现有 JS 测试。
- [x] 复核相对本轮基线的差异，记录实际测试结果及未做的服务器/设备验收；不进行 Git 提交。

验证详情见 `docs/分阶段计时修改与验证_2026-10-20.md`。本轮执行使用当前对话，独立子代理仅做只读代码审查。
