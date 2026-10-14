# 单 Session 直接导出实施计划

> **For agentic workers:** 用户要求当前会话直接实施并减少子智能体。按顺序在本会话执行；`executing-plans` 技能在 Codex / Claude 本地目录均不可用，使用以下检查点替代，不下载新技能。完成后依照 `requesting-code-review` 做一次聚焦审查。

**Goal:** 在动态瓶颈 Session 的 Report 页面直接下载仅属于该 Session 的全部轮次长表和全部应用原始宽表。

**Architecture:** 独立项目模块负责只读行构建、同步记录快照和 CSV 编码；独立管理员视图模块校验 Session 并调用长表 / 原始宽表导出。路由挂到当前 `project_asgi` 的 oTree 内部应用，复用管理员认证和数据库请求锁。报告模板只展示当前 Session 的两个链接，原有正式轮次导出不改。

**Tech Stack:** 当前 conda Python 3.10、oTree 6.0.13、Starlette、标准库 csv / sqlite3 / unittest。

## 文件职责

- 新增 `session_data_export.py`：不导入 oTree 的只读行构建和 CSV 编码；读取已经存在的同步 SQLite 快照，不初始化 journal。
- 新增 `session_data_export_views.py`：管理员下载视图、目标 Session 校验、固定导出类型、路由注册和报告链接。
- 修改 `project_asgi.py`：在包装 oTree 应用前注册下载路由，不改变同步中间件顺序。
- 修改 `dynamic_bottleneck_round/__init__.py`：仅在 `vars_for_admin_report` 增加下载链接变量。
- 修改 `dynamic_bottleneck_round/admin_report.html`：Session Code 附近展示两枚按钮和数据形态说明。
- 新增 `test_session_data_export.py`：纯函数边界及只读快照测试。
- 新增 `test_session_data_export_framework.py`：一次隔离子进程内初始化真实 oTree、创建两份小 Session，验证下载、认证、模板及数据库不变。
- 新增 `docs/单Session数据导出使用说明_2026-10-14.md`：使用方法、启动入口、测试与部署边界。

## Task 1：只读全部轮次行与 CSV

- [x] 先创建纯函数测试，验证新模块尚不存在或功能不存在：

```python
def test_export_module_exists(self):
    self.assertTrue(Path('session_data_export.py').is_file())
```

- [x] 加入实际数据断言：两个人的热身与正式行；尚未选择 / 尚未结算为空；真实零结果为零；其他 Session 被拒绝；两种 Agent 的热身记录可读且不重复。
- [x] 运行 `/opt/anaconda3/envs/otree_env/bin/python -m unittest test_session_data_export -v`，记录预期失败。
- [x] 实现固定列集合，读取 Player 的已保存字段及 `_vars`，不调用 `export_row_for_player`、`sync_integration.export_metadata`、Agent 初始化或结算函数。输出轮次从原始值开始，并添加阶段：

```python
phase = 'warmup' if raw_round <= warmup_rounds else 'formal'
phase_round = raw_round if phase == 'warmup' else raw_round - warmup_rounds
formal_round = '' if phase == 'warmup' else phase_round
```

- [x] SQLite 读取使用现有路径 `mode=ro`、`PRAGMA query_only=ON`，仅查询当前 Session 的 `sync_rounds`；文件不存在时不创建。已声明初始化但快照缺失的轮次明确标为诊断不可用，不能默认判为有效。
- [x] CSV 的 None 转空，布尔转 0/1，数字保留数值，可编辑文本首个有效字符是 `= + - @` 时加单引号，使用标准 writer 和 UTF-8 BOM：

```python
buffer = StringIO(newline='')
buffer.write('\ufeff')
writer = csv.writer(buffer)
writer.writerow(headers)
writer.writerows(rows)
```

- [x] 重新运行测试，要求通过。Git 提交不自动执行。

## Task 2：认证下载与报告按钮

- [x] 写真实框架隔离测试，先确认新路由不存在导致下载失败；不访问项目数据库或付费 API。
- [x] 管理员视图继承 `AdminView`，`_requires_login` 跟随当前平台认证等级；`code` 必填且校验格式，找不到 Session 返回 404，不是动态场景返回 400，导出类型只接受 `all_rounds` / `raw`。
- [x] 长表读取 `Player.objects_filter(session=session)`；原始宽表显式限定：

```python
export.export_wide(buffer, session_code=session.code)
```

- [x] `register_session_export_routes` 幂等注册 `/SessionDataExport/{code}/{export_kind}`，放在 `project_asgi.py` 的 oTree 应用包装之前。保留内部认证中间件和请求锁，避免额外数据库线程。
- [x] Report 变量生成当前 Session 的链接；如果用非项目入口启动导致路由未加载，页面给出明确启动提示，不生成无效按钮或导出全库的替代链接。
- [x] 模板增加明确的两枚下载按钮，中文标签与已批准设计一致；不修改首页或其他场景。
- [x] 重新运行框架测试，验证未登录、登录、无效编号、非法类型、双 Session 隔离、两个下载文件和模板渲染。

## Task 3：只读验收、回归与交付

- [x] 隔离测试比较导出前后所有模型表快照、参与者 / Session 变量、同步 SQLite 内容；锁定禁止 Agent 调用和结算，保证导出没有这些副作用。
- [x] 用存储的虚拟决策测试 LLM / RL 热身及正式行；问卷、付费字段使用真实原始宽表导出验证。
- [x] 运行目标回归：`test_session_data_export`、`test_session_data_export_framework`、`dynamic_bottleneck_round.test_stochastic_capacity`、`dynamic_bottleneck_round.agents.test_dynamic_agent_integration`、`admin_template_compatibility_tests`。
- [x] 运行 `git diff --check`，检查范围只涉及本功能；已有同步改动和论文文件全部保留。
- [x] 写使用说明及真实测试结果，明确本地实现不代表服务器部署。
- [x] 最后做一次独立聚焦代码审查，处理重要问题后交付。不开启长流程或压力测试，不启动 Chrome。

## 计划自检

覆盖 Session 隔离、热身、正式轮次、Agent、未完成数据、认证、CSV 编码、只读性、原始宽表、UI 和启动边界。无额外实验机制改动，无数据库迁移，无自动 Git 提交。
