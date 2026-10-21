# Hybrid Rank Payment Implementation Plan

**Goal:** 新场次使用15元基础+12元成本+3元排名奖金，最高30元。

**Architecture:** 支付模块读取最终私人排名快照，使用版本化纯计算函数；保留旧规则分支。金额以整数分保存、导出和原生oTree结算。

**Tech Stack:** Python Decimal、oTree5/6、unittest、临时SQLite。

不修改Git历史，遵守项目AGENTS。用户已选择混合方案及30元上限，在当前会话执行。

- [x] 添加有实际金额断言的失败测试：首中末、并列、单人、人数变化、缺失排名、旧版兼容。
- [x] 实现dynamic_cost_rank_v3及最终快照验证；每项奖金四舍五入到分。
- [x] 更新收据、正式说明、导出及默认配置；原始实验积分不变。
- [x] 添加真实ORM最终30轮混合市场结算校验，验证现金、原生结算、模板及导出。
- [x] 用户已确认保留Agent原成本目标，不改LLM/RL算法。
- [x] 两个oTree版本各295项回归通过，独立审查通过，报告已记录。

关键测试命令：`/opt/anaconda3/envs/otree_env/bin/python scripts/run_timeout_origin_tests.py --label hybrid_rank_green6 payment_info.test_rank_payment payment_info.test_cash_payment payment_info.test_cash_payment_framework`。旧版增加`--otree-path /private/tmp/otree5115-timeout-repair.D7ba0t`。
