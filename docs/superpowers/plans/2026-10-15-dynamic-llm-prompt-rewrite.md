# 动态瓶颈 LLM 提示词改造实施计划

> **For agentic workers:** 按用户要求在当前会话直接实施，减少子智能体。`executing-plans` 在 Codex / Claude / agents 本地技能目录均不存在，使用下述检查点在当前会话顺序实施，不下载新技能；最后按 requesting-code-review 做一次聚焦审查。不自动提交 Git。

**Goal:** 改善动态瓶颈 LLM 的规则表达、时间参照和输出约束，保留实验信息条件，并追踪新旧提示词版本。

**Architecture:** 新建纯提示词模块，保存原始 v1 消息和中文结构化 v2 消息；API 适配器依 Session 保存的版本构造请求。新建 Session 默认 v2，缺少版本的旧 Session 继续 v1；只为新产生的 LLM 记录和导出添加版本字段，不回补历史。

**Tech Stack:** 现有 conda Python 3.10 / oTree、标准库 JSON / unittest、现有 DeepSeek API 适配器。

## 文件职责

- 新建 `dynamic_bottleneck_round/agents/prompts.py`：版本校验、v1 / v2 消息和只基于公开固定行程的时刻参照；不依赖 ORM 或外部服务。
- 修改 `dynamic_bottleneck_round/agents/deepseek_agent.py`：配置携带版本，按版本构造请求并记录实际传入的新版上下文，不改变温度、模型、预算、timeout、调用次数和错误回退。
- 修改 `settings.py`：只给动态场景的新建配置添加 v2 标识；不在 settings 导入 app 包，避免 oTree 初始化循环。
- 修改 `dynamic_bottleneck_round/__init__.py`：创建时校验提示词版本；新 LLM 决策记录、原有正式长表添加 `prompt_version` / `agent_prompt_version`，不改变实验规则。
- 修改 `session_data_export.py`：Session 全部轮次长表从已有记录读取版本，旧记录缺失则空；不修改历史。
- 新建 `dynamic_bottleneck_round/agents/test_prompts.py`，扩展既有 Agent / Session 导出测试：离线验证。
- 新建 `scripts/check_dynamic_llm_prompt.py`：经用户追加授权的 3 轮新旧对照，最多 6 次真实请求，无重试、无实验数据库、无正式序列读取。
- 新建使用与验证说明，保存独立模拟对照的脱敏请求上下文、响应与指标。

## Task 1：提示词与公开时刻参照

- [x] 先写失败测试：模块 / 新版协议尚不存在；固定行程 6 分钟时，07:54、07:57、07:59 分别对应 08:00、08:03、08:05；改为其他行程参数也必须正确。
- [x] 运行 `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.agents.test_prompts -v`，确认因缺少新版功能而失败。
- [x] 实现如下版本和上下文边界：

```python
LEGACY_PROMPT_VERSION = 'dynamic_bottleneck_prompt_v1'
CURRENT_PROMPT_VERSION = 'dynamic_bottleneck_prompt_v2'
# 新版每档只新增固定行程参照，不估计排队或总成本。
arrival_baseline = departure_minute + free_flow_travel_minutes
```

- [x] v2 用结构化中文说明固定行程、非负额外行程、早晚到、客观成本、人格、I0/I1、历史事实、空档未知、受限记忆和一致的最终 JSON。理由简短，不要求冗长思考过程，不暗示固定某个最优档位。
- [x] 保留原始 v1 消息文本与参数，旧 Session 没有版本字段时选择 v1。非法版本明确报错，不能静默切换。
- [x] 同一组测试验证不修改原始 choice_set、人格 / 候选集不变、I0 不包含未公开当轮服务率、I1 保留公开值、请求预算和模型参数不变；通过后再接入记录链路。

## Task 2：Session 固定版本与导出审计

- [x] 写失败测试：动态新配置默认 v2、旧配置解析为 v1；构造 API 记录时记录已保存的 Session 版本；正式导出和全部轮次导出保留版本，旧记录为空。
- [x] 配置字段使用 `api_agent_prompt_version`，记录字段使用 `prompt_version`，导出列使用 `agent_prompt_version`。创建 Session 的轮次 1 校验版本，不依据部署后的全局默认值重写旧 Session 配置。
- [x] 新版请求的 `context_json` 保留实际派生后的上下文，能核查模型看到的固定行程参照；旧版请求和历史文件不重写。
- [x] 运行 Agent 集成、公开信息对等、Session 导出纯函数和真实框架隔离子进程测试。测试不得执行真实 LLM 请求。

## Task 3：经追加授权的少量真实 API 对照

用户 2026-10-15 追加授权：修改后可用 API 简单测试几轮。执行 **3 个模拟轮次 × v1 / v2 = 6 次请求**，每次最多使用现有预算，无额外重试。

- [x] 用测试替身验证模拟器和请求计数：固定种子生成服务率、其他主体出发时刻；两版共享当轮服务率和外部行动，各自根据自身上一轮结果与记忆继续。
- [x] 选择 I0，当前服务率只存在模拟器中；结算后按原规则作为历史反馈公布。不得读取 S01–S05 序列，不使用现有 Session / 参与者记录。
- [x] 使用当前配置的同一模型、温度、人格、预算和队列成本规则。凭据只从现有环境配置读取，不在文件或日志打印密钥。
- [x] 保留最多 6 条实际请求的版本、公开上下文、返回档位、理由、记忆、耗时、状态及模型响应标识；JSON 验证失败照实记录，不额外调用补齐。实际 6 次全部读取超时，无模型响应标识或回答，记录为空而非伪造。
- [x] 检查选项与固定行程参照、晚到表述和理由中的明确时刻；人工核对原始输出，不把自动关键词检查当作语义正确证明。此次没有任何模型回答可供检查，真实回答改进未验证，详见交付报告。
- [x] 记录每版换档和成本仅供这次小样本描述，不推断 30 主体正式市场必然稳定。若网络 / 凭据 / 余额受阻，报告已完成调用数与限制，不绕过访问限制或反复重试。此次两版所有行动均为原有回退，不能用于 LLM 表现比较；未追加请求。

## Task 4：回归、审查与交付

- [x] 运行提示词、Agent 集成、Session 导出、服务率 / 分组、预训练版本及原始单瓶颈 Agent 针对性回归；不跑长期压力测试。
- [x] 编译与本功能文件的 `git diff --check` 通过；已有同步和论文改动保留。
- [x] 完成一次聚焦审查，修复重要问题；写出新建 Session 的启用方式、旧 Session 行为、测试结果和 6 次真实对照的限制。无 Critical / Important，修复一处模拟个人历史字段不完全匹配的 Minor；见 `docs/动态瓶颈LLM提示词改造与API小样本验证_2026-10-15.md`。
- [x] 不部署服务器、不重启线上服务、不自动 Git 提交。真实对照结果不能作为对提示词持续调参的无限循环。
