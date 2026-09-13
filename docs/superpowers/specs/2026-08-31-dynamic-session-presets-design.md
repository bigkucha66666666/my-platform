# 动态瓶颈 Session 快捷配置设计

## 目标

在 oTree 的普通 Session 创建页和 Room Session 创建页中提供一致的快捷配置入口，降低管理员配置动态瓶颈实验分组、Agent 和服务率同步参数的学习成本。

## 界面结构

快捷配置仅在选择 `dynamic_bottleneck_round_prod` 或 `dynamic_bottleneck_round_demo` 时显示。模块位于参与者人数输入框之后、完整配置表之前，采用四个紧凑方案按钮：

1. 单组纯参与者：20 名真实参与者，不加入 Agent。
2. Human vs Human + LLM（推荐）：G01 为 20 名真实参与者，G02 为 15 名真实参与者和 5 个 LLM Agent。
3. Human vs Human + RL：G01 为 20 名真实参与者，G02 为 15 名真实参与者和 5 个独立 RL Agent。
4. 自定义：不覆盖参数，管理员使用完整配置表。

管理员点击方案后，模块显示真实参与者人数、实验组数、各组构成和关键底层参数摘要。完整配置表继续保留，允许管理员在快捷填写后进行修改。

## B 方案写入内容

点击 Human vs Human + LLM 后写入：

```text
num_participants=35
cohort_size=20
grouping_enabled=0
manual_grouping_spec=''
group_agent_spec='G01:api=0,rl=0;G02:api=5,rl=0'
api_agent_mode='active'
api_agent_count_per_group=5
rl_fallback_enabled=0
rl_agent_enabled=0
rl_agent_count_per_group=0
dynamic_capacity_sequence_scope='session'
```

`group_agent_spec` 是每组 Agent 数量的权威配置；统一的 `api_agent_count_per_group` 仅保持表单状态一致，不覆盖分组配置。

## 共享表单

快捷方案和已有 Agent 控件迁入 `_templates/otree/includes/CreateSessionForm.html`，从而同时服务：

- 普通 `/create_session` 页面；
- `prod_room` 和 `demo_room` 的 Room 创建页面。

控件必须位于 `<form id="form">` 内，确保 oTree 的 `serializeArray()` 能提交可见控件。已有 `_templates/otree/CreateSession.html` 只保留页面外壳和共享表单引用，避免两套逻辑漂移。

## 标签与分组边界

快捷方案使用 `cohort_size=20` 自动形成 20 人和 15 人两个真实参与者组，不启用依赖登录标签的手动分组。因此标签登录不会阻止 Session 创建。标签只标识真实参与者，LLM/RL Agent 不占标签和 oTree participant 席位。

该方案不保证指定标签进入指定组；标签对应哪个真实席位仍由 Room 分配顺序决定。

## 校验与回归

- 静态测试确认共享表单同时包含快捷方案和 Agent 控件。
- 静态测试确认 B 方案包含完整、准确的字段值。
- 现有动态 Agent 管理界面测试改为读取共享控件模板。
- 运行动态瓶颈单元测试、Python 编译和 oTree bot 回归。
