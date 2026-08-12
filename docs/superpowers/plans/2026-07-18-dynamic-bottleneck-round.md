# Dynamic Bottleneck Round Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建独立 oTree app，使同组同轮面对相同、不同轮按配置随机变化的瓶颈服务率。

**Architecture:** `dynamic_bottleneck_round/__init__.py` 拥有独立模型与规则；纯函数负责配置校验和可复现序列，`creating_session` 负责分组并写入每轮 Group/Player，`set_results` 按当轮 Group 容量计算点排队。模板和管理报告均属于新 app。

**Tech Stack:** Python 3.10、oTree、oTree templates、原生 HTML/CSS/JavaScript、`unittest` 与 oTree bots。

---

### Task 1: 随机容量核心

**Files:**
- Create: `dynamic_bottleneck_round/__init__.py`
- Create: `dynamic_bottleneck_round/tests.py`

- [ ] 先编写配置校验、balanced shuffle、iid 和同种子复现测试。
- [ ] 运行 `python -m unittest dynamic_bottleneck_round.tests` 确认因实现缺失而失败。
- [ ] 实现 `parse_dynamic_capacity_config()` 和 `generate_capacity_sequence()`。
- [ ] 重跑单元测试并确认通过。

### Task 2: 独立模型、分组和结果计算

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] 先添加同组同轮容量一致、跨轮变化、容量候选集、当轮容量参与排队/payoff 的测试。
- [ ] 实现独立 `Subsession/Group/Player`、自动分组、参与者状态、出发时间 helper 和 `set_results()`。
- [ ] 实现超时/掉线代填、结果同步和全轮累计 payoff。
- [ ] 运行单元测试、编译与 oTree bot 进行绿灯验证。

### Task 3: 参与者页面与公布模式

**Files:**
- Create: `dynamic_bottleneck_round/Introduction.html`
- Create: `dynamic_bottleneck_round/ComprehensionCheck.html`
- Create: `dynamic_bottleneck_round/Decision.html`
- Create: `dynamic_bottleneck_round/ResultsSync.html`
- Create: `dynamic_bottleneck_round/Results.html`
- Modify: `dynamic_bottleneck_round/tests.py`

- [ ] 先添加 before/after decision 页面泄露边界与页面流程断言。
- [ ] 以 `single_bottleneck` 的明亮工业风格实现五个独立模板。
- [ ] Decision 在 before 模式显示真实容量，after 模式只显示概率表。
- [ ] Results 显示本轮/上轮容量、个人结果、成本/payoff 和匿名组内时间分布。
- [ ] 运行两种公布模式 bot 测试。

### Task 4: 导出、后台报告和场景配置

**Files:**
- Create: `dynamic_bottleneck_round/admin_report.html`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/tests.py`
- Modify: `settings.py`

- [ ] 先添加导出表头、轮次汇总和 settings 场景存在性断言。
- [ ] 实现指定的每人每轮自定义导出。
- [ ] 实现后台容量、排队、成本、出发时间和状态次数报告。
- [ ] 在 `settings.py` 添加 demo/prod，默认关闭奖励和粗收费。
- [ ] 运行非法概率 session 创建测试并确认错误清晰。

### Task 5: 全量回归

**Files:**
- Verify: `dynamic_bottleneck_round/*`
- Verify: `single_bottleneck/*`
- Verify: `settings.py`

- [ ] 运行 `python3 -m py_compile dynamic_bottleneck_round/__init__.py dynamic_bottleneck_round/tests.py settings.py`。
- [ ] 运行 `otree test dynamic_bottleneck_round_demo 5`。
- [ ] 运行 after-decision 专用测试场景。
- [ ] 运行 `otree test single_bottleneck_demo 5`。
- [ ] 运行 `otree check`、`git diff --check` 并检查未修改原 app 业务逻辑。
