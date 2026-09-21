# 动态瓶颈预实验自定义人数设计

## 目标与边界

仅在 `dynamic_bottleneck_round_prod` 的创建 Session 页面增加独立的“预实验自定义人数”模式。正式快捷配置、正式每组 30 个主体的约束、单人流程预览、Room 监控、30 轮截断正态服务率和 I0/I1 信息规则均保持原样。预实验的用途是人数不足时仍能按 H/HA 和 I0/I1 条件运行真实流程并保留可分析数据；预实验数据必须明确标识，不能与正式固定构成数据混淆。

oTree 创建 Session 时需要确定 Human 登录席位数。管理员在创建前填写本次可用的 Human 数；创建后不通过本功能动态增减席位。未到场或未完成的参与者按现有超时、恢复和导出机制记录，不能伪造成已到场 Human。

## 创建页交互

- 仅选中正式动态瓶颈场景时显示“预实验自定义人数”入口；与正式快捷方案、正式自定义分组及手动流程预览互斥。切回正式方案时清除预实验开关和预实验分组配置。
- 预实验可配置一组或多组。每组选择 `H-I0`、`H-I1`、`HA-I0`、`HA-I1`，并分别填写 Human、LLM（API Agent）、RL 人数。每组实时显示主体总数，页面显示整个 Session 的 Human 登录席位总数与 Room 标签区间。
- H 组只能有 Human。HA 组可以独立调节 LLM 和 RL，但两类 Agent 合计至少为 1；不自动把不足 30 的主体补到 30。
- 每组 Human 为 1–30，LLM 和 RL 各为 0–10，总主体为 1–30。整个 Session 的 Human 总数不得超过当前 Room 标签文件可提供的标签数量；不足时在创建前提示。
- 预实验继续使用 S01–S05 固定容量序列，并沿用同轮各组共享序列的机制。创建页继续自动填写 `num_participants`，其值为各组 Human 数之和，不把 Agent 算入登录席位。

## 配置与后端合同

- 在动态瓶颈共同配置加入 `pilot_mode_enabled=0`、`pilot_group_spec=''`。仅正式动态瓶颈场景解释这两个字段；预览与预实验同时启用时拒绝创建。
- 预实验分组规范采用明确、可验证的文本合同，例如 `G01:H-I0,human=12,api=0,rl=0;G02:HA-I0,human=8,api=10,rl=10`。编号从 G01 连续，处理条件只允许四种现有标签，人数必须为规范十进制整数；重复、缺项、未知项或越界一律报错，不静默修正。
- `configure_formal_treatments` 在预实验模式下将每组处理条件与自定义 `human/api/rl` 人数写入现有 Session 分组元数据；正式模式继续从固定 `TREATMENT_DEFINITIONS` 取 30/10+10+10。Agent 启用状态由预实验各组实际配置汇总，运行时每组 Agent 数取分组元数据，不使用不匹配的全局默认数。
- 分组矩阵仍按各组 Human 数切片；创建时 Human 总数必须与 `num_participants` 精确相等。`validate_formal_actor_composition` 对预实验校验已解析的各组人数和处理条件，对正式实验继续执行固定构成校验。
- Room 标签仍通过现有 `build_sequential_label_plan` 按各组 Human 数顺序分配，支持 Session 监控和标签进入房间。人数变化只改变标签边界，不改变 Room 路由或标签文件。
- 预实验不改变容量生成、队列/成本结算、Agent 策略、问卷或支付流程。

## 数据记录与可比性

- Session 配置保存 `pilot_mode_enabled` 与完整 `pilot_group_spec`，使创建时的计划可追溯。
- 自定义导出的人类与 Agent 行均增加 `pilot_mode_enabled`、`planned_group_human_count`、`planned_group_api_count`、`planned_group_rl_count`、`planned_group_total_count`；人类行增加 `participant_label`（Room 标签）、按轮判定的 `access_granted` 和首次入口时间 `access_granted_at_ts`，Agent 行的这三列留空。现有处理标签、Session code、组号、Agent ID、正式轮号、服务率序列、实际服务率、信息条件、出发选择、自动决策来源、排队与成本、收益、LLM/RL 审计字段继续保留。
- 管理员报告增加预实验标识与每组计划构成，并按轮分列显示已进入的 Human 数、Human 手动选择数、Human 自动补选数、尚无选择的 Human 数、LLM 决策记录数和 RL 决策记录数。入口通过时保存 `access_granted_at_ts`，各轮 `access_granted` 通过该时间与本轮决策截止时间判定，避免晚到者追溯改写早期轮次。`decision_source` 区分手动和自动选择；这些实际记录都不能用计划 Human 人数替代。
- 正式 Session 的导出预实验标志为否，沿用原有固定构成；预览模式仍单独由 `flow_preview_enabled` 标识。分析时可用 Session code、预实验标志、处理标签、实际构成与 Sxx 序列识别匹配或不匹配的样本，不默认宣称不同人数的小组具有严格可比性。

## 验证

- 测试预实验规范解析及边界：H/HA 规则、1–30 总数、0–10 各 Agent、非法人数、重复或不连续组号、预览冲突。
- 测试一组与多组预实验的 Human 分组、Agent 实际数量、Room 标签顺序、各组 I0/I1 揭示及同一 Sxx 逐轮容量匹配。
- 测试正式快捷配置仍拒绝非固定人数，预览仍不绑定 Room，且切换界面模式不会残留旧配置。
- 测试人类与 Agent 导出都有预实验标志和计划构成，Human Room 标签和进入标志正确、Agent 对应列为空，管理员报告不会把缺席或未提交 Human 算作手动选择。
- 使用 `otree_env` 的单元测试及 oTree Bot 走查预实验页面流程；不调用外部 LLM API 来验证页面与分组合同。

## 不在本次范围

不修改论文、问卷、支付计算、正式配置人数规则，不新增创建后动态补录 Human 功能，不自动用 Agent 补满 30，也不替用户提交 Git。
