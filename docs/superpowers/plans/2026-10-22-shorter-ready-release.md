# Shorter Ready Release Implementation Plan

**Goal:** 新Session就绪后3秒开放选择，同时减少查询并明确倒计时。

**Architecture:** 独立Session规则传入协议1/2的轮状态；旧缺失字段回退5秒。身份查询只读取code列；前端倒计时不授予权限。

**Tech Stack:** oTree5/6、SQLAlchemy、独立SQLite journal、JavaScript、unittest及Node。

- [x] RED：添加新旧规则、齐员/缺席、journal重开、阶段时长、SQL数量及完整身份测试。
- [x] GREEN：在room_release.py保存并校验提前量，接入creating_session、协议1及协议2初始化，固定已有放行时刻。
- [x] 查询优化：sync_integration.state只读取所有Participant code列，HTTP请求内按数据库/Session/轮次复用不可变名单，避免N+1与大型vars反序列化。
- [x] 前端RED/GREEN：实际定时器倒计时，T前不操作、零秒不自行放行、异常停止、I1不泄露。
- [x] 两版本默认短回归各459通过、1项完整长测跳过；三份JS脚本通过；真实HTTP新旧Session及SQL指标已验证。
- [x] 独立只读审查通过；本地验证报告已记录生产/80人完整33轮/浏览器边界；修改保留未提交。

测试命令：`/opt/anaconda3/envs/otree_env/bin/python scripts/run_timeout_origin_tests.py --label ready_lead6 dynamic_bottleneck_round.test_release_lead dynamic_bottleneck_round.test_room_release dynamic_bottleneck_round.test_sync_journal dynamic_bottleneck_round.test_room_sync_framework`。旧版加`--otree-path /private/tmp/otree5115-timeout-repair.D7ba0t`，先验证实际路径。
