# 任务状态机（Week 4）

以 week-04.pdf 的八状态协议为准，替代第一周提案中的 QUEUED、RETRY_WAIT、CANCELLED 命名。后端模型、数据库约束、查询筛选和前端共享这些状态。

| 状态 | 含义 | 允许转换到 |
| --- | --- | --- |
| PENDING | 已创建，等待执行 | RUNNING、CANCELED |
| RUNNING | Worker 正在处理 | SUCCESS、PARTIAL_SUCCESS、RETRYING、FAILED、CANCELING |
| RETRYING | 本次失败，等待下一次重试 | PENDING、FAILED、CANCELED |
| CANCELING | 已请求取消，等待安全停止 | CANCELED、FAILED |
| SUCCESS | 所有有效记录均写入成功 | 无 |
| PARTIAL_SUCCESS | 文件可读取，至少一条成功且至少一条失败 | 无 |
| FAILED | 文件级校验失败、全部有效记录写入失败或系统处理失败；重试机制接入后包含重试耗尽 | 无 |
| CANCELED | 已取消 | 无 |

## 转换与一致性

`repository.transition_job` 是应用中唯一修改已有任务状态的入口，通过带来源状态条件的 SQL UPDATE 原子转换。非法转换抛出 `APIError("INVALID_JOB_TRANSITION")`，统一 HTTP 错误处理映射为 409，并记录任务 ID、原状态和目标状态。不存在的任务为 404。

Worker 领取是内部消费动作：仅允许 PENDING → RUNNING，冲突或任务不存在时跳过，不把重复消息当成服务故障。终态不能再次领取或转回其他状态。

每次转换显式更新 `updated_at`，保存 `last_error_code` / `last_error_message` 并写日志。正常转换错误码为空，消息说明领取或成功原因；实际错误保留业务错误码。进入 RUNNING 时记录本次 `started_at`，进入终态时记录 `finished_at`，非终态清空 `finished_at`。时间使用数据库 UTC。

批次数据、错误、统计和最终状态仍在同一事务中提交；失败回滚不留下只改了状态的数据。保留已有确认丢失后的批次去重逻辑。任务失败或取消不删除已经提交的数据。

## 投递失败与协议冲突的处理

原实现 PENDING → FAILED 不在本周合法转换表中。因此队列投递失败或确认丢失时，仅对仍处于 PENDING 的任务执行 PENDING → CANCELED，记录 `QUEUE_DISPATCH_FAILED` 和文件级错误明细；创建请求仍返回 500。

如果消息已经被 Worker 领取或处理完，取消补偿不会覆盖 RUNNING 或终态。数据库同时不可用时保留已提交的 `QUEUE_DISPATCH_PENDING` 标记，第二节扫描补投超时 PENDING，第四节扫描将超时 RUNNING / CANCELING 标记 FAILED 并保留已提交结果。

## 本节边界

第一节实现状态规则及现有执行链路接入；第三节已实现取消接口、独立任务进程、并发限制和超时中断。生产取消接口对重复取消及终态返回 409，不开放任意状态修改接口。第四节已完成服务整体停止与重启恢复：共用收尾期限，超时任务进入 FAILED；重启扫描过期 RUNNING / CANCELING，保留已提交数据。第五周实现完整重试机制。

验证与交付见 [Week 4 第一节](../delivery/week-04-state-machine.md)。
