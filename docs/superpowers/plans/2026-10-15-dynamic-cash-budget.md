# 动态场景 80 人 / 2,000 元预算 Implementation Plan

> **For agentic workers:** 用户要求当前会话直接修改，主代理顺序实施，不自动提交。`executing-plans` 本地未提供；按以下测试检查点执行，最后一次聚焦代码审查。

**Goal:** 新场次最多 80 名真人总报酬不超过 2,000 元，不追溯改变旧场次。

**Architecture:** 现金纯函数按保存版本选择奖金上限与成本系数；v1 不变，v2 将旧绩效部分减半。新 Session 固定 v2，正式开始与收据共用版本说明，原生账务与导出跟随实际版本。

**Tech Stack:** 现有 conda `otree_env`、oTree、Decimal、unittest，无新增依赖或数据库结构。

## Task 1：纯函数与兼容

Files: `payment_info/cash_payment.py`, `payment_info/test_cash_payment.py`。

- [x] 新增失败用例：
  ```python
  result = calculate_cash_payment(total_cost=0, formal_rounds=30,
                                  rule_version='dynamic_cost_budget_v2')
  self.assertEqual(result.total_cents, 2500)
  self.assertEqual(result.total_cents * 80, 200000)
  ```
- [x] 用 `/opt/anaconda3/envs/otree_env/bin/python -m unittest payment_info.test_cash_payment -v` 观察新功能失败，旧版测试仍通过。
- [x] 保留旧默认，新增 `BUDGET_RULE_VERSION`、80 人与 200000 分预算常量、版本规则表；使用 `rule_version` 选择奖金 1000 / 2000 分与平均成本除数 4 / 2，未知版本拒绝。
- [x] 同步测试成本 0/10/20/40、分位舍入、单调性、混合 80 人总额和旧版原值。

## Task 2：保存版本、页面、原生付款与导出

Files: `settings.py`, `dynamic_bottleneck_round/__init__.py`, `dynamic_bottleneck_round/FormalStart.html`, `dynamic_bottleneck_round/tests.py`, `payment_info/__init__.py`, `payment_info/PaymentInfo.html`, `payment_info/test_cash_payment.py`。

- [x] 先写失败测试：新版实际结算/页面/原生付款为 20 元（平均成本 20），旧版仍为 25 元；新配置保存 v2；版本化说明和导出上限正确。
- [x] 付款函数传入 `session.config['cash_payment_rule']`，读取保存的版本，不能用部署默认伪标旧 Session。
- [x] 新正式配置 `cash_payment_rule='dynamic_cost_budget_v2'`，创建校验接受两种已知规则；保留基础 15 元与每账务积分 0.01 元约束。
- [x] 共享说明由规则表派生，模板使用 `{{ cash_payment_description }}`；无现金场景不调用规则解析器。
- [x] 新增导出列 `max_bonus_cny`, `max_total_cny`, `budget_human_limit`, `budget_total_cny`，旧版只显示原个人上限，不伪造新预算归属。
- [x] 缺成本/缺轮次、重复访问、其他场景的原有测试继续通过。

## Task 3：隔离框架、审查与交付

Files: new `payment_info/test_cash_payment_framework.py`，交付说明。

- [x] 在临时工作目录、临时 SQLite 中建立两个独立新版 40 人 Session 与一个旧版小 Session；设置人工合成成本，不调用 Agent。
- [x] 真实付款页上下文/原生付款一致，80 人零成本总和正好 2000 元；旧版零成本仍 35 元；说明/导出区分版本；清理临时资源。
- [x] 跑付款测试、动态场景针对性回归和 Session 导出框架测试；`compileall` 与相关 `git diff --check`。相关回归271项通过，另一次隔离80人账务测试通过。
- [x] 一次聚焦审查、修复实质问题；无 Critical/Important，补充精确半分与两侧舍入断言；说明新建场次生效、旧场次保持原承诺、当前仅本地修改、不保证把预算发满。
