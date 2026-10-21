# Cumulative Result Ranking Implementation Plan

**Goal:** 正式结果页展示本人截至本轮的平均成本及含 Agent 的组内累计排名，保留奖励机制。

**Architecture:** 纯函数累计并排名；组结算时保存私有版本化快照；结果页只读取本人快照。没有数据库模型迁移，没有历史 Session 反馈变更。

**Tech Stack:** Python、Decimal、oTree 5.11.5/6.0.13、既有 HTML/CSS、unittest、隔离 ASGI HTTP。

## Tasks

- [x] 1. 创建 `dynamic_bottleneck_round/test_result_ranking.py`，覆盖累计排序、平均排序等价、并列、浮点尾差、无效值与完整历史约束。先运行隔离测试，确认 RED。

```python
# 两轮累计为 H1=12、H2=14、LLM=10、RL=12；H1/RL并列第二。
first = accumulate_rankings({'H1': 10, 'H2': 2, 'LLM': 3, 'RL': 9}, completed_rounds=1)
second = accumulate_rankings({'H1': 2, 'H2': 12, 'LLM': 7, 'RL': 3},
    completed_rounds=2, previous_totals=first['totals'])
assert second['rankings']['H1']['rank'] == 2
assert second['rankings']['H1']['mean_cost'] == '6.0000'
```

- [x] 2. 创建 `dynamic_bottleneck_round/result_ranking.py`。使用 Decimal 累加，保存未舍入的总额字符串；比较累计成本时 quantize 到 0.000001；名次为 `1 + 成本更低的主体数`，并列人数由相同比较值计数。拒绝非有限/负成本、空或变化主体、错误轮数。
- [x] 3. 修改 `dynamic_bottleneck_round/__init__.py`：导入模块常量/函数；建场保存版本；已保存虚拟结算记录后、设置 results_ready 之前保存组累计和每个人快照；结果模板上下文只加入本人排名。

```python
# 在保存最终虚拟记录后调用；暖场/旧Session直接返回。
save_cumulative_result_rankings(group, players=players, virtual_records=virtual_records)
# Results.vars_for_template只读取该玩家该轮快照，不扫描历史。
personal_ranking = cumulative_ranking_for_player(player)
```

- [x] 4. 修改 `dynamic_bottleneck_round/Results.html`：正式轮增加兼容窄屏的本人排名卡；未就绪不显示假名次，缺失历史给明确暂不可用提示。保留成本图、超时和按钮行为。
- [x] 5. 在新测试模块增加 `check_cumulative_ranking_orm`；在 `test_room_sync_framework.py` 增加独立场景调用及一项测试。真实 H9/HA9 连续两轮结算，独立汇总全部主体成本验证排名，检查历史页面稳定、热身不显示、结果 HTTP 模板、隐私及收益未被改写。
- [x] 6. 将新单元模块加入 `scripts/run_timeout_origin_tests.py`。按现有隔离 runner 运行新测试、相关成本/Agent/快照/支付回归，在双版本下执行真实 ORM 场景。仅在失败或新修改需要时重复检查。
- [x] 7. 保存本轮相对 before 基线的 diff，按 requesting-code-review 技能进行独立只读审查；修复确认问题、定向验证，记录检查范围与未部署边界。

运行入口：`/opt/anaconda3/envs/otree_env/bin/python -B scripts/run_timeout_origin_tests.py --label cumulative_ranking_red_20261020 dynamic_bottleneck_round.test_result_ranking`。兼容版本增加 `--otree-path /private/tmp/otree5115-timeout-repair.D7ba0t`。付款代码与 settings 的 SHA256 应与本轮 before 基线相同。
