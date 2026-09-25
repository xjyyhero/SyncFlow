# Week 2 测试与验收记录

验收日期：2026-09-18。第七部分全部通过。本记录区分自动测试、浏览器实测和真实服务链路。

## 结果摘要

| 验证层级 | 本次结果 | 证据 |
| --- | --- | --- |
| 后端契约及 MySQL / Redis / Worker | 35 项测试、473 条断言全部通过 | [可视化报告](../../output/db-review/20260918T082418313271Z/index.html)、[原始日志](../../output/week-02-acceptance/backend-tests.log)、[结构化结果](../../output/week-02-acceptance/test-results.json) |
| 前端辅助逻辑单元测试 | 3 项全部通过 | [检查日志](../../output/week-02-acceptance/check.log) |
| 类型、格式、构建、Compose、OpenAPI | 全部通过 | 同上 |
| 服务冒烟 | API、Web、代理、Worker 启停通过 | [日志](../../output/week-02-acceptance/smoke.log) |
| 真实端到端 | Web 代理上传 201 → Redis → Worker → 查询 SUCCESS | [响应记录](../../output/week-02-acceptance/e2e.json)、[执行日志](../../output/week-02-acceptance/e2e.log)、[任务日志](../../output/week-02-acceptance/service-logs.txt) |
| 页面状态 | 加载、空数据、断连失败、恢复后重试、404、筛选分页、详情均通过 | 下方截图及复现步骤 |

数据库测试使用独立 Compose 项目 `syncflow-db-tests`、临时 `syncflow_test` 数据库与独立 Redis，不读取开发环境数据；测试完成后自动清理测试容器。报告中的开发数据库快照是只读导出，生成于本次新端到端任务创建之前；新任务以 e2e.json 和截图为准。

## 要求与测试对应

| 要求 | 测试证据 |
| --- | --- |
| 正常流程、参数异常、统一错误响应 | test_api.py 中 7 项 HTTP 契约测试；前端 api.test.ts |
| 重复迁移、连接及数据库异常 | test_database.py：repeat_initialization、health_and_real_connection_failure |
| 上传正常、缺少文件、名称超长、非 CSV | test_create_job.py：real_upload、rejected_requests；test_api.py：upload_required_name_and_extension |
| 大小边界及超限 | 配置 1 MiB，恰好上限成功、多 1 字节失败；上传失败无文件/任务/队列副作用 |
| 文件名目录穿越 | test_create_job.py：traversal_names_and_repeat_uploads_are_safe |
| 队列投递失败有错误记录 | redis_failure_records_failed_job；数据库回写也失败时保留 QUEUE_DISPATCH_PENDING 标记 |
| 详情存在、不存在及信息脱敏 | test_query_jobs.py：detail、missing_id、database_failure；test_api.py：internal_errors_are_safe |
| 分页默认、非法、最大值、空结果、稳定排序 | test_query_jobs.py：empty_default_and_maximum_page_size、stable_order_and_status_filter、invalid_parameters |
| 筛选、URL 同步和翻页保留条件 | api.test.ts；浏览器在两条真实 SUCCESS 数据间翻页，URL 保留 status=SUCCESS 和 page_size=1；前阶段已实测下拉选择同步 FAILED |
| 加载、空数据、失败、重试及 404 | 本次暂停 API 捕获加载，停止 API 捕获错误，启动 API 后点击重试恢复列表；FAILED 筛选空结果、/not-found 404 |
| Worker 完整链路 | test_worker.py 独立进程真实 Redis 消费；本次 acceptance-e2e.py 经 Web 代理创建任务并轮询详情 |

测试源代码：[后端测试](../../backend/tests/) · [前端测试](../../frontend/src/api.test.ts)。

## 可复现命令

在项目根目录执行，需 Docker Desktop 已启动，开发依赖按 README 安装，`.env` 已配置。Node 运行 TypeScript 测试需支持类型擦除，建议 Node 24（与 Docker 镜像一致）。

```sh
# 静态检查、前端单元测试、类型检查和生产构建
sh scripts/check.sh

# 隔离后端测试 + 真实数据库只读快照 + 可视化报告
python3 scripts/review-db.py

# 启动真实服务后执行冒烟与端到端
# 每次端到端执行都会在开发库新增一条名称含时间戳的验收任务并保留文件
docker compose up -d --build --wait api worker web
backend/.venv/bin/python scripts/smoke.py
python3 scripts/acceptance-e2e.py
```

端到端脚本最多轮询 20 秒，每次 HTTP 请求最多 10 秒；非 SUCCESS 或时间戳异常时退出失败。结果写入 output/week-02-acceptance/e2e.json；该文件会在下一次执行时更新。

## 真实端到端结果

任务 ID：`ec8f7b46-e091-4650-8831-0972919ec322`。文件：`acceptance.csv`。

- 创建：201，PENDING，2026-09-18T08:24:55.078000Z。
- 开始：2026-09-18T08:24:55.105000Z。
- 完成：SUCCESS，2026-09-18T08:24:55.116000Z。
- 轮询实测状态：RUNNING → SUCCESS。
- 页面本地时间显示 2026-09-18 18:24:55；记录数为 0，符合本周模拟处理范围。

## 浏览器复现与截图

1. 打开 http://127.0.0.1:5173/ ，查看真实列表与详情。
2. 访问 `/?status=SUCCESS&page=1&page_size=1`，点击下一页；检查 URL 为 page=2 且保留 status、page_size，数据变化。
3. 选择“失败”，检查 URL 的 status=FAILED；当前开发库没有失败任务，显示空状态及创建入口。
4. **仅本地开发环境**执行 `docker compose pause api`，刷新页面观察加载；随后务必执行 `docker compose unpause api`。
5. 执行 `docker compose stop api`，刷新页面等待加载失败；执行 `docker compose start api`，服务启动后点击“重试”，列表恢复。本次验收结束后已恢复 API。
6. 访问 `/not-found` 查看 404；访问不存在的任务 ID，查看“任务不存在”和返回入口（第六部分已有实测记录）。

以上为人工可重复操作的浏览器步骤，不计入 38 项自动测试总数。

### 加载中

![加载中](../../output/week-02-acceptance/loading.png)

### 请求失败

![请求失败](../../output/week-02-acceptance/request-failed.png)

### 服务恢复后重试成功

![重试成功](../../output/week-02-acceptance/retry-success.png)

### 分页保留状态筛选

![分页](../../output/week-02-acceptance/pagination.png)

### 空数据

![空数据](../../output/week-02-acceptance/empty.png)

### 404

![404](../../output/week-02-acceptance/404.png)

### 真实任务成功详情

![SUCCESS 详情](../../output/week-02-acceptance/detail-success.png)

## 范围说明

Worker 目前模拟处理，不解析 CSV；不提供崩溃自动恢复与自动重试。上述限制不影响本周验收。第八部分 PR 与 Review 尚未执行，不在本次完成范围内。
