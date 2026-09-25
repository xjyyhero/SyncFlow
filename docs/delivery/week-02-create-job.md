# Week 2 创建任务 API 交付记录

验证日期：2026-09-18（Australia/Sydney）。对应清单第三部分，全部完成。

## 已实现

- 正式挂载 `POST /api/v1/jobs`，接受 multipart 必填 file、可选 name（最长 128 字符），缺省自动生成名称。
- 复用 `.csv` 扩展名和实际文件字节数校验，默认上限 10 MiB；超过上限返回 `400 FILE_TOO_LARGE`。
- 上传文件用 UUID 命名，独占创建，权限 0600；客户端文件名仅作为元数据保存，不参与本地路径拼接。
- 保存文件并分块计算 SHA-256，提交 PENDING 任务后再投递 Redis。队列消息仅为任务 ID。
- Redis 投递失败返回脱敏 `500 INTERNAL_ERROR`，任务标 FAILED，记录 `QUEUE_DISPATCH_FAILED`、文件级错误和结束时间。
- SQL 提交时保留 `QUEUE_DISPATCH_PENDING` 最近错误标记；正常投递后清除。队列失败且数据库回写也失败时仍可识别异常任务。
- 成功返回 201，包含 data.id/name/status/created_at 和空 meta，不等待 Worker。
- Compose 使用 uploads 持久卷，API 写入，Worker 同路径只读挂载。新增配置见 `.env.example`。
- OpenAPI 的创建操作改为正式接口，直接复用运行应用生成的请求与响应契约；列表/详情 GET 仍标 planned。

主要实现：[创建服务](../../backend/app/jobs.py)、[HTTP 入口](../../backend/app/main.py)、[参数校验](../../backend/app/api_contract.py)、[创建集成测试](../../backend/tests/test_create_job.py)。

## 验证

[最终可视化报告](../../output/db-review/20260918T072638914864Z/index.html)：23 项测试、338 条断言全部通过（含原有 HTTP/MySQL 测试）。

新增 10 项测试直接调用正式 POST 路由，使用独立 MySQL、Redis 和临时文件目录：

1. 正常上传、显式名称、201/PENDING、UUID、原始文件名、SHA-256、文件内容/权限和真实 Redis 消息。
2. 相对/绝对/反斜线路径穿越文件名及多次上传不覆盖。
3. 缺文件、非 CSV、超长名称/原始文件名、文件大小边界；拒绝时无入库、文件或队列副作用。
4. Redis 实际连接失败后的 FAILED 状态与文件级错误记录。
5. Redis 失败且 SQL 错误回写失败时保留待投递标记。
6. 入库和磁盘写入失败不投递，确认未入库时清理输入。
7. 正式 OpenAPI 的 multipart 与 201/400/500 声明。
8. SQL 提交确认丢失时保留已提交任务的输入文件。
9. Redis 已接受消息但确认丢失时不自动重试，FAILED 任务无法被 start_job 认领。
10. 发布成功但标记清理失败仍返回 201，避免诱发重复上传。

静态检查、格式检查、前端类型/生产构建、Compose 校验和 OpenAPI 一致性检查通过。本地 API 已重新构建并健康启动：真实无文件请求返回 400 INVALID_REQUEST；Redis PING 成功；上传卷存在；开发库任务数仍为 0。

## 使用

```sh
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -F 'file=@samples/sample-valid.csv' \
  -F 'name=人工测试导入'
```

删除 name 参数即可自动生成任务名称。此命令会真实创建开发库任务和队列消息。

自动验证：`python3 scripts/review-db.py`。

## 当前边界

- Redis 列表默认 `syncflow:jobs`，API 使用 RPUSH；后续 Worker 使用 BLPOP 并通过 PENDING 条件更新认领任务。当前 Worker 尚未消费，正常创建的任务保持 PENDING。
- Redis 客户端连接/命令超时为 3 秒，不自动重试发布。确认丢失时队列可能已有消息，后续 Worker 必须跳过无法认领的任务。
- 文件系统、MySQL 和 Redis 没有跨系统原子事务。本节提供持久化错误标记和补偿，不实现自动补投；确认结果不明时保留文件供恢复。
- 真正 CSV 解析、记录写入、任务列表/详情和 Worker 消费仍属于后续清单。
