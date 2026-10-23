# 双房间独立实例 Implementation Plan

> **For agentic workers:** 按清单逐步执行；最终使用 requesting-code-review 做独立审查。本次用户已授权可行的本地修改，不操作 Git 或服务器。

**Goal:** 两个本地单 worker 实例使用固定源码副本和独立数据库、journal、临时目录，避免跨房间共用业务队列。

**Architecture:** 标准库配置模块一次校验两个实例。启动器先构造完整环境和工作目录，再 exec 既有生产入口；settings 在实例模式拒绝 .env 回退。每个实例仅展示本条件房间，创建 Session 时验证信息条件。

**Tech Stack:** Python 3.10、oTree 6.0.13（兼容验证 5.11.5）、PostgreSQL、unittest。

---

用户提供的 `docs/双房间独立实例_本地改造清单_2026-10-23.md` 为本次设计来源。第一、二批本地改造纳入本次；第三批统一入口及线上切换另行验收。端口不能隔离 Cookie，本地使用独立浏览器配置文件。

## 配置与启动

- [x] 在 `test_instance_config.py` 写配置拒绝、路径别名、环境污染、运行副本排除和条件校验用例；运行 `/opt/anaconda3/envs/otree_env/bin/python -m unittest -v test_instance_config`，确认缺少实现而失败。
- [x] 新建 `instance_config.py`：JSON pair 配置必须包含 i0/i1；production 只允许两个不同 PostgreSQL 库；local-test 明确允许各工作目录的 SQLite。使用绝对 canonical 路径，拒绝共用/重叠目录、journal、端口、签名密钥、REST key。保护变量不可被额外环境覆盖。
- [x] 新建 `run_instance.py`：`init-local CONFIG --root ABS` 生成无付费调用凭据的私有配置；`validate CONFIG` 校验；`prepare CONFIG` 复制允许的源码/模板/静态/标签并生成 SHA256 manifest，拒绝覆盖目录；`run CONFIG i0|i1` 验证副本，切换 cwd、清洁环境并 exec `project_prodserver.py 127.0.0.1:PORT`。
- [x] 用 `python -m unittest -v test_instance_config` 验证配置、复制、实例 settings 和历史单实例兼容。

## 条件固定

- [x] `settings.py` 在实例环境中验证配置及 cwd 后跳过 `.env`；仅显示 formal config 及本条件 Room，默认 PAIR、40 真人，序列 S01。
- [x] `dynamic_bottleneck_round/__init__.py` 在新 Session 创建和正式组解析时校验每组条件、顶层条件；旧单实例无此限制。原生 Room 字典拒绝另一房间。
- [x] `DynamicSessionControls.html` 读取实例固定条件，隐藏不适用快捷按钮，约束动态选项。服务端校验作为最终约束。

## 进程隔离与回归

- [x] 新建 `scripts/verify_independent_instances.py`，仅在新临时目录启动本次拥有的 PostgreSQL 测试集群与两个实例；生成同名测试标识，检查 journal/缓存/超时子进程/条件/跨实例凭据，停止 I0 后确认 I1 响应；输出匿名延迟分布。
- [x] 运行上述实际双入口验收，确认失败进程也被清理，不连接既有数据库。
- [x] 运行相关 oTree 双版本回归和 Node 同步脚本，确认此前身份查询与 3 秒放行改动保留。
- [x] 独立审查并修复发现，补充无密码配置示例、运行说明与验证边界；不将短流程描述为 80 人全 33 轮或统一入口验收。
