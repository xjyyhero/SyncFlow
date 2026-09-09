# MySQL 数据库设计（待审核提案）

完整字段、类型、非空性、默认值和约束见 [schema.sql](schema.sql)。SQL 是设计附件，不会由启动脚本自动应用；没有宣称已完成业务迁移。

## 表与字段含义

| 表 | 字段组 | 含义 |
| --- | --- | --- |
| jobs | id | UUID 字符串任务主键，对应 API job_id |
| jobs | filename / storage_key / file_sha256 | 安全展示文件名、内部受控文件键、内容 SHA-256；后两者不直接返回 API |
| jobs | status | 状态机枚举，见 state-machine.md |
| jobs | total_count / processed_count / success_count / failed_count / checkpoint | 非空记录总数、已处理数、成功数、失败数、已提交非空记录位置；processed=success+failed=checkpoint |
| jobs | attempt_count / lease_expires_at | 当前执行次数与 Worker 租约，用于恢复和阻止旧执行者提交 |
| jobs | dispatch_pending / next_retry_at | 待投递标记和重试到期时间，用于补投与调度 |
| jobs | cancel_requested | V1.1 取消请求标记；与任务状态分开，避免误报已取消 |
| jobs | idempotency_key / idempotency_expires_at | V1.1 请求去重键及过期时间；未使用时 NULL |
| jobs | last_error_code / last_error_message | 经脱敏的最近任务级错误，对外映射为 last_error |
| jobs | started_at / finished_at | 首次执行开始、任务最终结束时间 |
| records | id / job_id / row_number | 自增内部主键、任务关联、CSV 逻辑记录序号 |
| records | external_id / name / amount / record_date | CSV 四业务字段；金额 DECIMAL(12,2)，日期 DATE |
| job_errors | id / job_id / row_number / code / message / field | 每个错误行保留一个主要错误及可选字段名，不保存完整原始行 |
| job_attempts | id / job_id / attempt_no / outcome | 执行尝试及结果，重试等待对应前次 attempt 的 FAILED |
| job_attempts | error_code / error_message / started_at / finished_at | 本次尝试的错误及时间 |
| 所有表 | created_at / updated_at | UTC datetime(3)，由应用显式写入；不依赖服务器默认时区 |

关联在应用层维护，不建数据库外键。写结果前锁定 jobs 行并核验存在性、状态、attempt_no 与租约；删除任务（未设计公开接口）必须同时处理关联记录。API 不暴露内部自增主键，避免 JavaScript 大整数精度问题。

## 索引用途

| 索引 | 对应查询或约束 |
| --- | --- |
| jobs PRIMARY | 按任务 ID 查询、锁定和更新 |
| uq_jobs_idempotency | 原子保证幂等键唯一；NULL 允许多个普通请求 |
| ix_jobs_created | 全部任务按 created_at DESC,id DESC 稳定分页 |
| ix_jobs_status_created | 状态筛选后的稳定分页 |
| ix_jobs_dispatch | 扫描需要投递且已到期的任务 |
| ix_jobs_lease | 扫描 RUNNING 过期租约 |
| ix_jobs_key_expiry | 清理过期键，事务内将键和过期时间同时置 NULL |
| uq_records_external | 同任务业务标识唯一，跨任务允许重复 |
| uq_records_row | 重放保护；按 job_id,row_number 导出和查询 |
| uq_errors_row | 每行一个主要错误；错误列表按 row_number 分页 |
| uq_attempts_no | 同任务尝试序号唯一，执行记录分页 |

external_id 使用区分大小写且 NO PAD 的 utf8mb4_0900_bin 排序规则，不能依赖默认不区分大小写的排序规则实现 D02。是否 trim 为明确提案，待需求方确认。

## 幂等与迁移方案

V1.1 可选 Idempotency-Key 限 ASCII 可见字符 1–128。有效期内相同 key+相同文件字节摘要返回原任务 200，摘要不同返回 409；无 key 的重复文件创建不同任务。过期键在锁定原记录后释放，唯一索引处理并发创建；TTL 为创建后 24 小时，不因重放续期。文件名不参与请求身份。

未来使用版本化 SQL 和 schema_migrations(version,checksum,applied_at) 管理升级：单迁移进程执行，已应用且摘要相同则跳过，摘要不同拒绝。MySQL DDL 隐式提交，不能承诺多条 DDL 的事务回滚；迁移需逐步可重入、失败重试前核对实际结构。当前 CREATE TABLE IF NOT EXISTS 可重复执行，但不能用它代替未来 ALTER 升级或检测结构漂移。

文件系统和队列不与 SQL 处在同一事务，详见 technical-design.md 的补投、租约和 checkpoint 方案。错误行与成功行必须互斥，由任务行锁和连续 checkpoint 保证，不能只依赖两张表各自的唯一约束。
