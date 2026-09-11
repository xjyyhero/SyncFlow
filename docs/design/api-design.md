# API 设计说明

机器可读契约为 [openapi.json](openapi.json)，采用 OpenAPI 3.0.3。它是完整计划接口；当前运行服务的 /docs 只展示已实现的 /healthz 和 /api/v1/info，不代表业务接口已经实现。

## 接口清单

| 方法 | 路径 | 用途 | 成功状态 |
| --- | --- | --- | --- |
| GET | /healthz | 已实现，进程存活检查 | 200 |
| GET | /api/v1/info | 已实现，骨架信息 | 200 |
| POST | /api/v1/jobs | V1.0 multipart file 创建任务 | 202；V1.1 幂等重放200 |
| GET | /api/v1/jobs | V1.0 任务列表，page/page_size/status | 200 |
| GET | /api/v1/jobs/{job_id} | V1.0 详情与统计 | 200 |
| GET | /api/v1/jobs/{job_id}/errors | V1.0 错误分页 | 200 |
| GET | /api/v1/jobs/{job_id}/attempts | V1.1 执行尝试分页 | 200 |
| POST | /api/v1/jobs/{job_id}/cancel | V1.1 取消或请求取消 | 200/202 |
| GET | /api/v1/jobs/{job_id}/export | V1.1 导出，kind=records/errors | 200 CSV |

业务 JSON 成功返回 data/meta；列表 meta 包含 page、page_size、total，默认第1页每页20条，最大100条。错误返回 error.code/message/details，details 始终为数组；无详细字段错误时为空数组。健康检查与CSV文件下载为显式例外。

POST 上传只接受 multipart/form-data；文件内容按实际 CSV 解析，不依赖客户端 MIME 判定。文件最大值以配置 MB × 1024 × 1024 字节计算。文件过大413、请求媒体类型错误415、预检/参数错误422；任务不存在404；状态或幂等冲突409；依赖不可用503；未知内部错误500。Redis 暂时不可用且任务已持久化时仍可202，依赖补投恢复。

## 错误码

| 类别 | 错误码 |
| --- | --- |
| 文件/请求 | FILE_TOO_LARGE、UNSUPPORTED_MEDIA_TYPE、EMPTY_FILE、INVALID_HEADER、INVALID_CSV、INVALID_ENCODING、TOO_MANY_RECORDS、VALIDATION_ERROR |
| 查询/操作 | JOB_NOT_FOUND、JOB_NOT_CANCELLABLE、JOB_NOT_FINISHED、IDEMPOTENCY_CONFLICT |
| 系统 | SERVICE_UNAVAILABLE、INTERNAL_ERROR |
| 行级（错误记录内，不作为整次HTTP错误） | REQUIRED_FIELD、FIELD_TOO_LONG、INVALID_AMOUNT、INVALID_DATE、DUPLICATE_EXTERNAL_ID |
| 执行尝试/任务级 | DATABASE_UNAVAILABLE、WORKER_LOST、JOB_TIMEOUT、FILE_MISSING、FILE_CHANGED |

错误消息不得暴露 SQL、路径、连接信息或原始堆栈。一个错误行只记录一个主要错误，优先顺序为 external_id → name → amount → record_date → 重复标识；所有其他合法行继续处理。该顺序属于待确认提案。

## 字段与数据库映射

Job.job_id ← jobs.id；其余统计和时间同名映射；Job.last_error ← last_error_code/message（无错误时 null）。filename 为安全展示名，不包含路径。RowError ← job_errors 的 row_number/code/message/field。Attempt ← job_attempts 除内部自增 id/job_id 外的公开字段。records 导出四个 CSV 业务字段；amount 转为两位小数十进制字符串。page/meta.total 由查询计算，不落业务表。

客户端提交的日期仅为业务 DATE，任务时间为 UTC RFC3339（例如 2026-09-09T03:00:00.000Z）。设计中的空值应返回 null 而不是字符串 "null"。
