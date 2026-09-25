# Week 3 错误明细查询 API

对应 [Week 3 清单第五节](week-03-checklist.md)，八项要求均已完成。新增接口读取 Worker 已写入 MySQL 的真实错误，复用现有分页校验和统一响应，不新增依赖或数据表。

## 接口与边界

```http
GET /api/v1/jobs/{job_id}/errors?page=1&page_size=20
```

- `page` 默认 1、最小 1；`page_size` 默认 20、范围 1–100。拒绝小数、科学计数法、布尔值和空字符串，非法参数在访问数据库前返回 `400 INVALID_REQUEST`。
- 只查询指定任务，按 `row_number ASC, id ASC` 排序。空行号优先；同一行号按内部 ID 升序，内部 ID 不返回。
- 每条错误返回七个字段。`raw_row` 解码为原始 JSON，CSV 错误保持字符串数组及原始空白、中文、引号和换行；不存在原始行时返回 `null`。`created_at` 使用 UTC ISO 时间和 `Z` 后缀。
- 文件级错误的 `row_number` 为 `null`，不伪造行号；无字段定位时 `field_name` 也为 `null`。
- 已有任务无错误返回 `200`、空数组及 `total=0`。超出末页也返回空数组，保留真实总数；超大合法页码不会造成 MySQL OFFSET 溢出。
- 任务不存在返回 `404 JOB_NOT_FOUND`；任务标识超过 36 字符返回 `400 INVALID_REQUEST`。数据库故障返回脱敏 `500 INTERNAL_ERROR`，查询不依赖 Redis。
- `meta.total` 是该任务错误条数。运行中也能查询已提交错误；排序确定，但跨请求新增错误时页边界仍可能移动，不承诺跨页冻结快照。

行级错误示例：

```json
{
  "data": [
    {
      "job_id": "example-job",
      "row_number": 2,
      "field_name": "amount",
      "error_code": "AMOUNT_INVALID",
      "error_message": "amount 不能为负数",
      "raw_row": ["S001", "商品A", "-1", "2026-01-01"],
      "created_at": "2026-01-01T10:00:00Z"
    }
  ],
  "meta": {"page": 1, "page_size": 20, "total": 1}
}
```

运行时 `/openapi.json` 与 [导出契约](../design/openapi.json)均包含参数限制、成功响应模型、行级/文件级/空页示例及 400/404/500 错误示例。生成时保留示例中的显式 `null`，避免 FastAPI 默认序列化删除这些必需字段。

## 验证结果（2026-09-25）

完整后端 **73 项测试通过**。在原有 66 项基础上新增七项错误查询测试，复用独立 MySQL 测试环境：

| 验证项 | 结果 |
| --- | --- |
| 七个公开字段、真实 JSON、中文/引号/换行、空值、UTC 时间 | 通过 |
| 已有任务空列表、缺失任务、SQL 注入样式 ID、超长 ID | 通过 |
| 105 条错误的默认页、100 条最大页、末页及超大页码 | 通过 |
| 乱序插入、重复行号、多个文件错误、跨页排序和任务隔离 | 通过 |
| 非法分页参数返回 400，且不访问数据库 | 通过 |
| 真实数据库连接故障返回脱敏 500；Redis 不可用仍可查询 | 通过 |
| OpenAPI 参数、响应模型和示例与实际契约一致 | 通过 |

同时扩展既有真实 HTTP / Redis / MySQL / 独立 Worker 验收：上传正常、混合错误、非法表头和数据库约束失败文件，处理后经新 API 查询错误，逐字段与 MySQL 中的记录核对，包括 `raw_row` 和文件级空行号；成功任务返回空页。

`sh scripts/check.sh` 通过：后端静态与格式检查、14 项 CSV 测试、3 项前端测试、前端格式/类型检查及生产构建、Compose 配置和 OpenAPI 一致性。

本次证据快照保存在本地 `output/week-03-errors/`（输出目录不入库）：[73 项测试报告](../../output/week-03-errors/test-results.json)、[完整测试日志](../../output/week-03-errors/test.log)、[项目检查日志](../../output/week-03-errors/check.log)、[真实任务场景](../../output/week-03-errors/scenarios.json)、[API 日志](../../output/week-03-errors/api.log)、[Worker 日志](../../output/week-03-errors/worker.log)。

## 复跑

```sh
sh scripts/check.sh
sh scripts/test-db.sh
```

本次复用已缓存测试镜像，只读挂载当前业务代码执行完整测试集：

```sh
docker compose -f compose.test.yaml up -d --wait mysql-test redis-test
docker compose -f compose.test.yaml run --no-deps --rm -v "$PWD/backend/app:/app/app:ro" db-tests
docker compose -f compose.test.yaml down
```

测试只使用独立数据库、Redis 和临时文件，已清理测试容器；未重启开发环境。错误明细页面与页面刷新属于第六节。
