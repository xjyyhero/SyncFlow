# API 响应与参数契约（Week 2 / Week 3）

当前契约为 [openapi.json](openapi.json)，由共享 Python 模型生成，使用 OpenAPI 3.1。历史 Week 1 提案保留在 [openapi-week-01.json](openapi-week-01.json)；其中的 202/413/422、QUEUED 和旧字段名不适用于本周接口。

## 当前实现范围

- 已运行：任务创建、任务列表、任务详情、错误明细分页、`/api/v1/info`、`/healthz`、`/readyz`，以及全局统一错误处理。
- 已提供可复用模块：成功/错误响应模型、分页与状态校验、上传字段/扩展名/文件大小校验、公开任务字段模型。
- `GET /api/v1/jobs`、`GET /api/v1/jobs/{job_id}` 已接入真实 MySQL；导出 OpenAPI 直接使用运行应用契约，不再手写计划接口。
- 原有 HTTP 契约测试使用测试专用路由验证共享模块；新增创建任务测试直接调用正式 POST 路由，连接独立 MySQL、Redis 和临时存储目录。创建与查询流程均已实现。

## 统一响应

业务成功响应必须包含 `data` 和 `meta`：

```json
{"data":{"id":"01JABC00000000000000000000","name":"商品导入","status":"PENDING","created_at":"2026-01-01T10:00:00Z"},"meta":{}}
```

列表 `data` 为数组，`meta` 包含 `page`、`page_size`、`total`。没有额外信息时 `meta` 为 `{}`。

失败响应必须包含 `error.code`、`error.message` 和数组 `error.details`：

```json
{"error":{"code":"INVALID_REQUEST","message":"请求参数不合法","details":[{"field":"page","message":"小于允许下限"}]}}
```

不带字段详情时 `details` 为 `[]`，不能省略。健康探针的成功响应保持现有约定：`/healthz` 为 `{"status":"ok"}`，`/readyz` 为 `{"status":"ok","checks":{"mysql":"ok"}}`，不作为业务 data/meta 响应。

## HTTP 状态码与错误码

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | 缺必填字段、名称超长、分页或状态值非法、错误请求体 |
| 400 | INVALID_FILE_EXTENSION | 文件名扩展名不是 `.csv`（大小写不敏感） |
| 400 | FILE_TOO_LARGE | 文件字节数超过配置上限 |
| 404 | JOB_NOT_FOUND | 业务层查不到任务，抛出 `APIError("JOB_NOT_FOUND")` |
| 500 | INTERNAL_ERROR | 数据库异常、未知内部异常、响应字段校验失败 |
| 503 | DATABASE_UNAVAILABLE | `/readyz` 无法连接数据库，保留健康检查约定 |
| 404 | NOT_FOUND | 请求未挂载的路由；与已存在业务路由中的任务缺失区分 |
| 405 | METHOD_NOT_ALLOWED | 路由存在但方法不支持，保留 Allow 响应头 |

FastAPI 请求参数校验默认的 422 已统一映射为 400，OpenAPI 也不再声明默认 422 响应。异常消息、SQL、数据库地址、凭据、存储路径、校验输入和异常堆栈都不会从全局异常处理器返回。参数错误只输出白名单字段名称和固定描述，不回显输入内容。

## 请求校验

### 查询参数

`PageQuery` 用于错误明细分页，`JobQuery` 继承相同分页校验并增加任务状态筛选：

| 字段 | 默认 | 规则 |
| --- | --- | --- |
| page | 1 | 十进制整数，至少 1；拒绝小数、科学计数、布尔和空字符串 |
| page_size | 20 | 十进制整数，范围 1–100 |
| status | null | 可省略；提供时仅允许 PENDING / RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED |

### 创建任务表单

`validate_upload` 作为 `Depends` 依赖使用：

- `multipart/form-data`，`file` 必填，`name` 可选且最长 128 字符。
- 只接受 `.csv` 扩展名，不使用客户端 MIME 类型判定 CSV。
- `MAX_UPLOAD_FILE_SIZE_MB` 默认 10，上限换算为 `MB × 1024 × 1024` 字节；等于上限允许，超过 1 字节也拒绝。
- 分块读取实际文件字节进行计数，不信任客户端声明大小；校验后重置文件读取位置，交给后续文件保存逻辑。
- 校验模块不解析 CSV 行；创建服务随后使用 UUID 文件名保存输入、计算 SHA-256、提交任务并入队。原始文件名只作为数据库元数据，超过 255 字符或包含 NUL 时返回 INVALID_REQUEST。

## 公开字段

`JobCreated`：`id`、`name`、`status`、`created_at`。

`JobDetail`：上述字段，以及 `source_file_name`、`total_records`、`success_records`、`failed_records`、`retry_count`、`last_error_code`、`last_error_message`、`started_at`、`finished_at`。

通过模型从数据库行构建响应，额外内部字段会被过滤；不要原样返回 repository 的内部行。数据库 UTC DATETIME 无时区值按 UTC 序列化为带 `Z` 的 ISO 时间，空时间为 JSON `null`。后续业务层必须只将安全、可读的错误摘要写入对外可见的 `last_error_message`。

## 生成与验证

```sh
backend/.venv/bin/python scripts/export-openapi.py
backend/.venv/bin/python scripts/export-openapi.py --check
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -p test_api.py -v
```

`sh scripts/check.sh` 检查文档是否与模型一致。`python3 scripts/review-db.py` 运行 HTTP 契约测试和 MySQL 集成测试，并保留可视化报告。

## 创建任务的真实执行顺序

1. 验证文件与名称，生成 UUID；未提供名称时生成 `导入任务-UTC时间-UUID前缀`。
2. 保存到 `UPLOAD_DIR/<UUID>.csv`，独占创建、权限 0600，分块计算 SHA-256 并刷盘。
3. 提交 PENDING 任务和 `QUEUE_DISPATCH_PENDING` 最近错误标记，再向 Redis 列表 `REDIS_JOB_QUEUE` 执行 RPUSH，消息仅为任务 ID。
4. 投递确认后清除标记，返回 `201 {data:{id,name,status,created_at},meta:{}}`，不等待 Worker。

Redis 连接和命令超时均为 3 秒，发布不自动重试，避免确认丢失后重复入队。投递失败返回 `500 INTERNAL_ERROR`；任务更新为 FAILED，写入 `QUEUE_DISPATCH_FAILED` 文件级错误并记录结束时间。若数据库也无法回写，已提交的待投递标记仍留在任务上，日志包含 job_id，便于人工恢复。本周不实现自动补投。

数据库提交结果不明确时，先重新查询：确认任务不存在才删除上传文件；已入库或仍无法确认时保留输入，避免误删。若发布成功但清理标记失败，仍返回 201；标记表示尚未完成确认记录，不等同于 Redis 一定没有消息。

Redis 可能已接受消息但响应丢失；这种情况下任务会标 FAILED，队列中可能留有该 ID。Worker 通过 `start_job` 的 PENDING 条件更新判断是否可处理，忽略无法认领的任务。该实现不承诺跨 MySQL、Redis、文件系统的原子提交。

## 任务查询接口

- `GET /api/v1/jobs/{job_id}`：返回 `SuccessResponse[JobDetail]`，成功 200；标识长度 1–36 字符，任务不存在时 404 JOB_NOT_FOUND，超长参数为 400 INVALID_REQUEST。
- `GET /api/v1/jobs?page=1&page_size=20&status=PENDING`：返回 `JobListResponse`，列表项使用与详情相同的公开字段，meta 必须包含 page、page_size、total。
- 列表按 `created_at DESC, id DESC` 排序；total 按相同状态筛选计算。空库、无匹配任务及超出末页都返回 200 和空 data。超大合法页码直接返回空页，不将超出 MySQL LIMIT 范围的 offset 发给数据库。
- 查询只依赖 MySQL，不连接 Redis。数据库故障由统一处理器转换为 500 INTERNAL_ERROR，不暴露 SQL、连接信息或堆栈。
- 排序保证相同创建时间下顺序确定；分页是页码分页，跨请求新增任务时页边界仍可能移动，不表示跨多次请求的冻结快照。

## 错误明细查询（Week 3 第五节）

`GET /api/v1/jobs/{job_id}/errors?page=1&page_size=20` 返回 `JobErrorListResponse`。分页限制与任务列表相同；不支持状态筛选。任务不存在返回 `404 JOB_NOT_FOUND`，参数非法返回 `400 INVALID_REQUEST`，数据库故障返回脱敏的 `500 INTERNAL_ERROR`。

每条错误只返回 `job_id`、`row_number`、`field_name`、`error_code`、`error_message`、`raw_row`、`created_at`。文件级行号为 JSON `null`；原始行从 MySQL JSON 解码，CSV 原始行保持数组和原始空白，不返回 JSON 编码字符串；无原始行时为 `null`。时间统一为 UTC ISO 格式（`Z`）。

查询按指定任务过滤，按 `row_number ASC, id ASC` 排序，MySQL 将空行号排在前面；同一行的多条错误由内部 ID 决定顺序，ID 不对外返回。`meta.total` 为该任务的错误条数。任务无错误、超出末页均返回 `200` 和空数组，超大合法页码不会把越界 offset 发给 MySQL。运行中也可查询已提交错误；跨请求新增错误时页边界可能改变，不提供跨页冻结快照。实现与验证见 [第五节交付记录](../delivery/week-03-errors-api.md)。

## CSV Worker（Week 3 第四节）

独立 Compose 服务从 Redis 列表阻塞获取任务 ID，通过条件更新认领 PENDING 任务并提交 RUNNING，首次 started_at 保留。读取受控上传目录中的 CSV，规范化合法行并保存行错误；完整文件预检后，每 250 条非空记录独立提交数据、错误与累计统计，最后一批同时提交终态和结束时间。单批系统失败记录 BATCH_WRITE_FAILED 并继续后续批次。终态包括 SUCCESS、PARTIAL_SUCCESS、FAILED。文件错误原子写入 FAILED 与对应错误记录；文件缺失/目录越界为 FILE_UNREADABLE，系统失败为 WORKER_PROCESSING_FAILED。重复消息或不存在的任务跳过。API 与 Worker 的日志均包含 job_id；共享 uploads 卷在 Worker 中只读。

错误明细与验收见 [Week 3 校验交付记录](../delivery/week-03-validation.md)。分批事务和运行期间统计更新已完成，详见 [第三节验收记录](../delivery/week-03-batches.md)。当前 BLPOP 不提供消息确认或崩溃恢复；进程被强制终止、数据库不可用时需要人工核查已出队任务，自动恢复与重试后续实现。

第四节已完成独立 HTTP API 与 Worker 端到端验收，覆盖成功、部分成功、文件失败与真实数据库写入失败；日志补充处理阶段和批次进度，见 [Worker 验收记录](../delivery/week-03-worker.md)。
