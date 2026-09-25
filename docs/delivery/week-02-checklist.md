# Week 2 待完成任务清单

本周目标：完成基础任务 API、MySQL 数据访问和 React 查询页面，跑通“创建任务 → Redis 入队 → Worker 消费 → 状态变为 SUCCESS”的完整流程。

依据：用户提供的《week-02.pdf》（第 2 周：基础 API 与 MySQL）。本清单按文档要求整理；MySQL 部分已完成并通过独立数据库实测，其余项目按实际验收结果更新。

本周 Worker 只模拟处理完成；真正的 CSV 解析和业务数据写入在第 3 周实现，幂等与重试相关字段在本周预留，第 5 周启用。

## 一、MySQL 与数据访问层

- [x] 编写迁移或初始化脚本，空数据库可初始化，连续执行两遍结果一致。
- [x] 创建 `sync_jobs` 任务表，字段完整：
  - `id varchar(36)` 主键，使用 UUID 或 ULID；`name varchar(128)`。
  - `status varchar(32)`，本周使用 `PENDING / RUNNING / SUCCESS / FAILED`。
  - `source_file_name varchar(255)`、`stored_file_path varchar(512)`、`file_sha256 char(64)`。
  - `idempotency_key varchar(128)`，可为空。
  - `total_records`、`success_records`、`failed_records` 均为 `int unsigned`，默认 0。
  - `retry_count tinyint unsigned`，默认 0。
  - `last_error_code varchar(64)`、`last_error_message varchar(512)`，可为空。
  - `created_at`、`started_at`、`finished_at`、`updated_at` 使用 `datetime(3)`、UTC；开始和结束时间可为空。
  - 建立 `(status, created_at)` 和 `(created_at)` 索引。
- [x] 创建 `sync_records` 成功记录表：
  - `id bigint unsigned` 自增主键；`job_id varchar(36)`。
  - `external_id varchar(64)`，统一大写存储；`name varchar(128)`。
  - `amount decimal(12,2)`，禁止使用 FLOAT/DOUBLE；`record_date date`。
  - `created_at`、`updated_at` 使用 `datetime(3)`。
  - 建立 `UNIQUE(job_id, external_id)` 约束及 `(job_id)` 索引。
- [x] 创建 `sync_errors` 错误记录表：
  - `id bigint unsigned` 自增主键；`job_id varchar(36)`。
  - `row_number int unsigned`，文件级错误可为空；`field_name varchar(64)`，可为空。
  - `error_code varchar(64)`、`error_message varchar(512)`。
  - `raw_row json`，可为空；`created_at datetime(3)`。
  - 建立 `(job_id, row_number)` 索引。
- [x] 实现 MySQL 连接、环境配置和健康检查。
- [x] 实现数据访问层，覆盖创建任务、查询详情、筛选分页、更新状态和记录错误。
- [x] 区分路由层、业务层和数据访问层，避免路由直接承担 SQL 或业务规则。
- [x] 配置独立测试数据库，测试不得污染开发库。

MySQL 实现与验证见 [Week 2 MySQL 交付记录](week-02-mysql.md)。

## 二、统一响应与参数校验

- [x] 统一成功响应结构：`{ "data": ..., "meta": ... }`。
- [x] 统一失败响应结构：`{ "error": { "code": ..., "message": ..., "details": [] } }`。
- [x] 实现并验证本周错误码与 HTTP 状态码。

| HTTP 状态码 | 错误码 | 场景 |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | 请求字段、分页参数或状态筛选值非法 |
| 400 | `INVALID_FILE_EXTENSION` | 上传文件扩展名不是 CSV |
| 400 | `FILE_TOO_LARGE` | 上传文件超过大小限制 |
| 404 | `JOB_NOT_FOUND` | 任务不存在 |
| 500 | `INTERNAL_ERROR` | 未预期内部错误 |

- [x] 对外错误信息不包含数据库连接信息、SQL 内部异常或堆栈。
- [x] 统一接口字段命名，补齐 OpenAPI 请求、响应和错误说明。

实现范围：本节的共享响应、校验模块和 HTTP 契约测试已完成；三个任务业务路由在第三、四节接入。详见 [API 响应与参数契约](../design/api-design.md)和 [本节交付记录](week-02-api-contract.md)。

## 三、创建任务 API

接口：`POST /api/v1/jobs`，请求类型为 `multipart/form-data`。

- [x] 接收必填 `file` 和可选 `name`；名称最长 128 字符，未提供时自动生成。
- [x] 校验文件扩展名为 `.csv`，否则返回 `400 INVALID_FILE_EXTENSION`。
- [x] 接入 `MAX_UPLOAD_FILE_SIZE_MB`，默认 10 MB；超限返回 `400 FILE_TOO_LARGE`。
- [x] 将文件保存到本地受控目录，使用安全的服务端存储名称，防止客户端文件名造成目录穿越。
- [x] 保留原始文件名，计算并保存文件 SHA-256。
- [x] 创建初始状态为 `PENDING` 的任务，并将任务 ID 投递到 Redis 队列。
- [x] 处理队列投递失败，避免任务无错误记录地一直停留在 `PENDING`。
- [x] 成功时立即返回 `201 Created`，包含 `id`、`name`、`status`、`created_at`，不等待后台处理完成。

创建任务实现与验证见 [第三部分交付记录](week-02-create-job.md)。

## 四、任务查询 API

### 任务详情：`GET /api/v1/jobs/{job_id}`

- [x] 返回 `id`、`name`、`status`、`source_file_name`。
- [x] 返回 `total_records`、`success_records`、`failed_records`、`retry_count`。
- [x] 返回 `last_error_code`、`last_error_message`。
- [x] 返回 `created_at`、`started_at`、`finished_at`。
- [x] 任务不存在时返回 `404 JOB_NOT_FOUND`。
- [x] 不返回 `stored_file_path`、数据库连接信息或内部堆栈。

### 任务列表：`GET /api/v1/jobs`

- [x] 支持 `page`，默认 1，最小为 1。
- [x] 支持 `page_size`，默认 20，最大为 100，并拒绝无效值。
- [x] 支持按 `status` 筛选，非法状态返回 `400 INVALID_REQUEST`。
- [x] 默认按 `created_at DESC, id DESC` 排序，保证分页稳定。
- [x] 返回任务列表和分页信息 `page`、`page_size`、`total`。
- [x] 列表数据包含页面所需的名称、状态、文件名、记录统计、创建时间和任务 ID。

任务查询实现与验证见 [第四部分交付记录](week-02-query-jobs.md)。

## 五、最小 Worker

- [x] 从 Redis 队列获取任务 ID。
- [x] 将任务从 `PENDING` 更新为 `RUNNING`，记录首次 `started_at`。
- [x] 模拟处理完成，将状态更新为 `SUCCESS` 并记录 `finished_at`。
- [x] 日志包含 `job_id`，便于追踪 API 和 Worker 的处理过程。
- [x] 在 Docker Compose 中将 Worker 配置为独立服务，与 API 分开启动。
- [x] 确认 API 保存的上传文件能供后续 Worker 处理使用。
- [x] 实测创建任务、入队、消费、状态更新的完整流程。

验收记录：[最小 Worker](week-02-worker.md)。

## 六、React 查询页面

### 任务列表页：`/`

- [x] 首次加载调用真实 `GET /api/v1/jobs`。
- [x] 实现状态筛选，筛选条件与 URL 查询参数同步。
- [x] 实现分页，切换页码时保留状态筛选条件。
- [x] 每项展示名称、状态、文件名、总数、成功数、失败数、创建时间和详情入口。
- [x] 状态使用统一中文名称和颜色，始终保留文字标识。
- [x] 无任务时显示空状态和“创建任务”按钮。
- [x] 请求失败时显示错误反馈和重试按钮。

### 任务详情页：`/jobs/:jobId`

- [x] 首次加载调用真实 `GET /api/v1/jobs/{job_id}`。
- [x] 展示状态、源文件名、记录统计、创建/开始/结束时间和最近错误。
- [x] 任务不存在时显示“任务不存在”和返回列表入口。
- [x] 时间按浏览器本地时区显示，统一为 `2026-01-01 18:00:00` 格式。

### 通用要求

- [x] 集中定义前端 API 客户端与响应类型，供页面复用。
- [x] 每个页面处理加载中、空数据和请求失败状态，避免白屏或崩溃。
- [x] 未匹配路由 `*` 显示 404 页面，并提供返回任务列表入口。
- [x] TypeScript 类型检查无错误，前端生产构建通过。

验收记录：[React 查询页面](week-02-react.md)。

## 七、测试与验收记录

- [x] 单元测试覆盖正常流程、参数异常和错误响应。
- [x] MySQL 集成测试覆盖建表、数据访问及数据库异常，并使用独立测试库。
- [x] 验证迁移连续执行两次结果一致。
- [x] 验证正常上传、缺少文件、名称超长、非 CSV 文件、大小边界及超限场景。
- [x] 验证客户端文件名不能造成目录穿越。
- [x] 验证队列投递失败不会留下无错误记录的悬空任务。
- [x] 验证任务详情存在与不存在场景，以及内部字段不泄露。
- [x] 验证分页默认值、非法值、最大页大小、空结果及排序稳定性。
- [x] 验证状态筛选、URL 同步和翻页保留筛选。
- [x] 验证页面加载、空数据、请求失败、重试和 404 展示。
- [x] 记录端到端结果：上传 CSV 后可查询到任务，Worker 最终将状态更新为 `SUCCESS`。
- [x] 保存测试命令、结果和必要截图，形成可复现的交付记录。

验收汇总：[Week 2 测试与验收记录](week-02-acceptance.md)。

## 八、PR 与最终交付

- [ ] 完成至少 2 个可独立审阅和合并的 PR，并保留各自 Review 记录。
- [ ] 交付后端：可重复迁移、数据访问层、三个 API、分页、统一错误和 OpenAPI。
- [ ] 交付前端：列表、状态筛选、分页、详情、API 类型及页面状态处理。
- [ ] 交付验证记录：单元测试、MySQL 集成测试、PR 和 Review。
- [ ] 能解释路由层、业务层、数据访问层的职责及任务异步处理流程。

建议实施顺序：数据库与连接 → API 与校验 → Redis 与 Worker → React 页面 → 完整测试和交付记录。
