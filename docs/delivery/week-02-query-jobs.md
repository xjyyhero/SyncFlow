# Week 2 任务查询 API 交付记录

验证日期：2026-09-18（Australia/Sydney）。对应清单第四部分，全部完成。

## 实现

- `GET /api/v1/jobs/{job_id}`：返回 13 个公开字段及空 meta，包括名称、状态、源文件名、四项统计、最近错误和创建/开始/结束时间。
- 未找到任务返回 `404 JOB_NOT_FOUND`；超长 ID 等无效参数返回 `400 INVALID_REQUEST`。
- `GET /api/v1/jobs`：page 默认 1 且至少 1；page_size 默认 20，范围 1–100；status 只接受 PENDING、RUNNING、SUCCESS、FAILED。
- 按 `created_at DESC, id DESC` 排序，total 使用同样的筛选条件计算；列表项复用详情公开模型。
- 返回 `data: []` 与 `meta: {page, page_size, total}`，包括空库、没有匹配任务或超出末页的情况。超大合法页码不会导致 MySQL OFFSET 溢出。
- 响应过滤 stored_file_path、file_sha256、updated_at 等非公开字段；日期序列化为 UTC Z，空时间为 null。
- 数据库异常统一为脱敏 `500 INTERNAL_ERROR`。查询仅依赖 MySQL，不要求 Redis 可用。
- OpenAPI 直接从运行应用导出；不再维护手写的计划路由。

主要代码：[路由](../../backend/app/main.py)、[业务层](../../backend/app/jobs.py)、[数据访问](../../backend/app/repository.py)、[公开响应模型](../../backend/app/api_contract.py)。

## 验证

[可视化测试报告](../../output/db-review/20260918T073308363682Z/index.html)：31 项测试、427 条断言通过。

新增 [8 项查询集成测试](../../backend/tests/test_query_jobs.py)，直接调用正式 HTTP 路由并查询独立 MySQL：

1. 详情完整字段、默认计数、UTC 时间、空值及内部字段过滤。
2. 数据库中的真实统计、错误摘要和开始/结束时间。
3. 任务不存在、SQL 注入样式 ID、超长 ID。
4. 空表、105 条数据、默认 20 条、最大 100 条、第二页剩余 5 条、超大页码。
5. 创建时间优先、相同时间按 ID 倒序、跨页顺序、四种状态筛选及筛选后总数。
6. 无效分页与状态在访问数据库前即返回 400。
7. 实际数据库连接失败时脱敏；Redis 不可用时查询仍成功。
8. 在线 OpenAPI 的真实列表、详情、分页模型和错误码。

创建接口原有集成测试增加“创建 → 详情 → 按 PENDING 查询列表”的链路验证。

项目静态/格式检查、前端类型/生产构建、Compose 配置和 OpenAPI 一致性检查通过。测试数据只写入隔离测试库。本地 API 已更新并健康启动：真实列表与状态筛选返回 200，不存在的任务返回 404 JOB_NOT_FOUND，非法分页/状态返回 400 INVALID_REQUEST；在线 OpenAPI 已包含两个 GET 操作。开发库仍为空。

## 使用

```sh
curl 'http://127.0.0.1:8000/api/v1/jobs?page=1&page_size=20&status=PENDING'
curl 'http://127.0.0.1:8000/api/v1/jobs/替换为创建返回的任务ID'
```

重新生成完整验证报告：`python3 scripts/review-db.py`。

本节不实现 Worker 消费；正常新任务保持 PENDING。页码分页在跨请求新增任务时可能移动页边界，排序稳定不代表跨请求冻结数据快照。
