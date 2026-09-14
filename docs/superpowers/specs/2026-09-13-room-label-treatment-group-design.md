# Room 标签顺序固定实验组设计

## 1. 目标

在随机服务率动态瓶颈正式实验中，允许同一信息条件下的 H 组与 HA 组放在同一个 Session 和同一个 oTree Room 中运行，并按 Room 标签文件顺序确定实验组。参与者进入 Room 的先后顺序不得改变其处理组。

典型配置为：

- I0 场次：`G01:H-I0;G02:HA-I0`；
- I1 场次：`G01:H-I1;G02:HA-I1`。

每个场次需要 40 名真人：H 组 30 名，HA 组 10 名。HA 组中的 10 个 LLM 与 10 个 RL Agent 不占用 Room 标签。

## 2. 方案选择

采用“Session 创建时按标签文件顺序预绑定 Participant，再按同一顺序创建 Group”的方案。

不采用实验开始后重新执行 `set_group_matrix()`。oTree 的 `set_group_matrix()` 会删除并重建 Group，而当前动态瓶颈在 `creating_session` 中已经写入各轮容量、同步状态和处理元数据，后置重分组容易破坏这些数据。

不修改 oTree 安装包文件。项目在 `access_gate` 加载时为启用
`participant_label_assignment=sequential` 的 Session 安装严格标签查找保护：
只允许匹配已预绑定 Participant 的精确标签，不允许 oTree 在未知标签上
回退到第一个未访问 Participant。其他未启用该配置的旧 Session 仍保持
oTree 默认行为。

## 3. 标签权威来源

正式动态瓶颈配置与 `prod_room` 共同使用 `_rooms/econ101.txt`。后端按文件中的非空标签顺序读取、去重并校验。

对于 `G01:H-I0;G02:HA-I0`：

- P001–P030 对应 G01 / H-I0；
- P031–P040 对应 G02 / HA-I0；
- P041–P100 不属于该 Session。

I1 场次使用同样的标签区间，只改变信息条件。

Session 创建时，将本场所需的前 N 个标签依次写入 N 个 oTree Participant。Room 收到 P001–P040 中任一标签时，oTree 会优先查找同名 Participant，因此进入顺序不会改变分组。

## 4. 配置与数据结构

正式场景增加以下配置：

```text
participant_label_file = _rooms/econ101.txt
participant_label_assignment = sequential
```

每个 Human Participant 保存：

```text
expected_room_label
assigned_group_id
assigned_group_label
dynamic_bottleneck_treatment_group
dynamic_bottleneck_information_condition
```

`expected_room_label` 用于入口校验和审计。Participant 的正式 `label` 与其值必须一致。

## 5. 创建流程

1. 解析 `group_treatment_spec` 并计算各组 Human 数量。
2. 校验 Session 的 Human 总数等于各组 Human 数量之和。
3. 从标签文件读取前 N 个唯一标签。
4. 按 Participant 的 `id_in_session` 顺序预绑定标签。
5. 按处理组 Human 数量连续切分 Participant：先 G01，再 G02，依次类推。
6. 沿用现有逻辑设置全部 35 轮 Group、容量、Agent 数量和处理元数据。
7. Room 参与者可以任意顺序进入，但始终落入与其标签预绑定的 Participant。

## 6. 管理员界面

正式场景的快捷配置增加两个同场方案：

- I0 同场对比：G01 H-I0 + G02 HA-I0；
- I1 同场对比：G01 H-I1 + G02 HA-I1。

选择后自动填写真人数量 40，并显示：

```text
G01 · H-I0 · P001–P030
G02 · HA-I0 · P031–P040
```

自定义多组配置继续可用，并按各组 Human 数量自动计算连续标签范围。

## 7. Room 兼容与入口校验

- 不修改 `prod_room` 的名称、监控页面、标签文件或已有链接形式。
- 正确的预绑定标签会被 oTree Room 解析到指定 Participant，与进入顺序无关。
- 对本场未分配的标签（例如 40 人 Session 中的 P041），严格查找保护会在
  Room 分配阶段直接拒绝，不覆盖 P001 或其他已预绑定标签。
- `access_gate` 校验实际 Participant 标签与 `expected_room_label` 一致；不一致时不得进入动态瓶颈实验，并显示需要使用的正确标签。
- 管理员只应向本场参与者分发界面显示的标签范围。标签文件中超出本场范围的标签保留给其他 Session，但不属于当前 Session。
- 非 Room 启动仍可使用参与者专属链接，预绑定标签不会改变实验分组。

## 8. 失败处理

以下情况在正式 Session 创建阶段立即失败：

- 标签文件不存在或不可读；
- 标签重复或数量不足；
- Session Human 数量与处理配置不一致；
- `participant_label_assignment` 不是 `sequential`；
- 预绑定标签与 Participant 数量不一致。
- Room 请求中的标签未预绑定给当前 Session 的任一 Participant。

入口阶段检测到标签不一致时，不进行静默换组。

## 9. 测试与验收

至少覆盖：

1. I0、I1 同场方案均生成 40 个 Human 席位。
2. I0 标签范围为 P001–P030 和 P031–P040；I1 相同。
3. P040 先于 P001 进入时，两者仍属于预定组。
4. 标签文件不足、重复或配置人数不一致时立即报错。
5. P041 进入 40 人 Session 时被拒绝，且 P001 的预绑定保持不变。
6. H 组有 30 个 Human；HA 组有 10 Human、10 LLM、10 RL。
7. 同一 Session 两组同轮共享服务率，但排队与成本独立。
8. Room 管理页面继续显示标签在线状态和 Session 监控链接。
9. 原有回归测试与新增严格查找测试通过。
10. 在 `otree_env` 中完成一次 40 人、两组、35 轮正式 Session 创建和 Bot 流程。

## 10. 非目标

- 不增加跨组逐轮强制同步；
- 不改变容量序列、I0/I1 信息边界、排队或成本公式；
- 不给 LLM/RL Agent 分配 Room 标签；
- 不修改后问卷；
- 不将不同 Session 同时挂载到同一个 Room。
