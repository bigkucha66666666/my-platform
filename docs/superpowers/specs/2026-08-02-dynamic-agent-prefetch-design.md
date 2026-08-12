# 动态瓶颈 Agent 异步预取设计

## 目标

动态瓶颈场景开启 Agent 时，参与者提交出发时间后应立即进入 `ResultsSync`。DeepSeek 尚未响应时由等待页轮询，不得让浏览器停留在 `Decision`。

## 设计

- 当本轮通过 `RoundStartSync` 正式启动时，在请求线程中构造不含真人选择的 Agent 决策上下文。
- 将纯网络请求提交到进程内线程池；后台线程不得读写 oTree ORM 对象。
- 使用 `session_code + group_id + round_number` 作为任务键，同一组同一轮只创建一个任务。
- `ResultsSync` 轮询时仅检查任务是否完成。未完成时不计算结果；完成后在请求线程中把返回值转换为 Agent 记录并执行统一排队和成本计算。
- `after_decision` 继续使用候选服务率与概率，不向 Agent 暴露本轮真实服务率。
- API 异常仍由现有 DeepSeek adapter 转为确定性回退选择。
- Agent 关闭时不创建任务，原流程保持不变。

## 稳定性边界

- 任务注册表只保存不可变上下文和 Future，不保存 Player、Group 或 Session ORM 对象。
- 结果结算继续使用现有文件锁，避免等待页并发轮询重复落地。
- 开发和当前单机部署使用进程内任务池；若未来采用多实例部署，应改为 Redis/Celery 等共享任务队列。

## 验收

- 慢 API 运行期间，`maybe_prepare_results()` 快速返回且 `results_ready=False`。
- API 完成后的下一次轮询完成 Agent 记录与本轮结算。
- 同一轮多次启动只调用一次 Agent API。
- Agent 开关关闭、超时恢复、同分钟统一成本和原单瓶颈回归不受影响。
