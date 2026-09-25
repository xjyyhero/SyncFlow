# Week 3 Redis 队列与真实 Worker

对应 [Week 3 清单第四节](week-03-checklist.md)。前三节已完成真实 CSV 处理与分批事务；本节补齐处理阶段日志，并通过独立 HTTP API、真实 Redis/MySQL 和独立 Worker 进程验证完整异步流程。

## 实际流程

1. API 接收上传，保存文件、创建 `PENDING` 任务，将任务 ID 投递到 Redis 后返回 `201`。请求不等待 Worker 消费或 CSV 处理完成。
2. 独立 Worker 通过 `BLPOP` 从 `REDIS_JOB_QUEUE` 取得任务 ID。只有 `PENDING` 任务可以被认领；认领时提交 `RUNNING` 和首次 `started_at`，重复或无效消息跳过。
3. Worker 检查 `stored_file_path` 位于受控上传目录内，执行完整文件预检和逐行校验、规范化。文件级错误进入 `FAILED` 并记录空行号错误。
4. 按原始数据顺序，每 250 条非空记录一个事务，写入合法记录和错误明细，并更新已提交的成功数、失败数和文件总数。详情接口能查询每批提交后的进度；不逐行提交数据库事务。
5. 最后一批原子提交最终统计、终态和 `finished_at`。批次系统写入失败只回滚本批，记录错误后继续后续批次；规则见 [第三节交付记录](week-03-batches.md)。

Compose 保持 API 与 Worker 独立服务，共享 `uploads` 卷，Worker 只读挂载。数据库初始化在二者启动前完成；文件和任务查询仍使用现有配置，无新增依赖。

## 日志

日志使用同一个 `job_id` 关联 API 创建、Redis 消费、开始处理、预检、批次提交和失败结果。

- `received`：Worker 收到队列消息。
- `status=RUNNING`：任务认领与首次开始时间已提交。
- `stage=csv_validated total_records=...`：完整文件预检通过。
- `batch=... source_lines=... processed=.../... success=... failed=...`：该批提交后的源文件行范围、累计记录进度和统计。
- `stage=batch_write write_error=... persisted_progress=...`：批次写库出错及核对后的持久化进度。
- 任务级失败日志包含 `status`、`stage=file_access/csv_validation/batch_write` 和稳定 `error_code`，不输出文件原始内容或数据库异常原文。

501 行正常文件的实际批次进度为 `250/501 → 500/501 → 501/501`；源文件物理行范围为 `2–251`、`252–501`、`502–502`。

## 端到端验收（2026-09-25）

测试启动独立 Uvicorn API 进程，通过真实 HTTP 上传五个文件，**此时 Worker 尚未启动**。五次请求均返回 `201/PENDING`，开始/结束时间均为空；Redis 中的消息顺序与任务 ID 完全一致。本次单个创建请求耗时约 63–68 毫秒，这是本机测试观测值，不作为性能保证。

随后启动未打补丁的 `python -m app.worker` 进程。测试用数据库读锁短暂阻止首批写入，从 HTTP 详情接口观察到 `RUNNING`、非空 `started_at` 和空 `finished_at`，再释放锁继续处理。

| 场景 | 终态 | 总数 / 成功 / 失败 | 验证结果 |
| --- | --- | --- | --- |
| 501 行正常 CSV | `SUCCESS` | `501 / 501 / 0` | 三批全部入库，标识和名称去空白、标识大写、金额两位小数 |
| 一行非法金额、一行合法 | `PARTIAL_SUCCESS` | `2 / 1 / 1` | 合法行入库，保存 `AMOUNT_INVALID` |
| 非法表头 | `FAILED` | `0 / 0 / 0` | 无业务记录，保存 `CSV_HEADER_INVALID` 文件错误 |
| 两行触发数据库写入故障 | `FAILED` | `2 / 0 / 2` | MySQL 检查约束实际拒绝写入，错误号 3819，两行均保存 `BATCH_WRITE_FAILED` |
| 系统失败之后的合法任务 | `SUCCESS` | `1 / 1 / 0` | 队列继续消费，Worker 未退出，后续数据正常落库 |

系统故障由测试库临时检查约束触发，不修改 Worker 方法或模拟 SQL 调用；测试后删除约束。每个场景都通过 HTTP 查询终态和统计，并与真实数据库中的成功行、错误明细核对；验证 `created_at ≤ started_at ≤ finished_at`。Worker 收到 SIGTERM 后正常退出，队列为空。

**完整后端 66 项测试通过**，包括原有 65 项及新增端到端验收。`sh scripts/check.sh` 全部通过，覆盖后端静态/格式检查、14 项 CSV 测试、3 项前端测试、前端格式/类型/生产构建、Compose 配置与 OpenAPI 一致性。

证据：

- [五个场景的真实任务结果与创建耗时](../../output/week-03-worker/scenarios.json)
- [独立 API 日志](../../output/week-03-worker/api.log)
- [独立 Worker 日志](../../output/week-03-worker/worker.log)
- [66 项测试报告](../../output/week-03-worker/test-results.json)
- [完整测试日志](../../output/week-03-worker/test.log)

## 复跑与范围

```sh
sh scripts/check.sh
sh scripts/test-db.sh
```

本次复用已缓存测试镜像，只读挂载当前业务代码执行同一测试集：

```sh
docker compose -f compose.test.yaml up -d --wait mysql-test redis-test
docker compose -f compose.test.yaml run --no-deps --rm -v "$PWD/backend/app:/app/app:ro" db-tests
docker compose -f compose.test.yaml down
```

验证仅使用独立测试数据库、Redis 和临时文件；未修改或重启开发环境。当前仍为基础 `BLPOP` Worker，不提供消息确认、进程崩溃后的自动恢复或任务重试；持续数据库不可用时可能保留 `RUNNING` 与最后已提交进度，详见第三节边界说明。错误明细 API 和相关页面属于后续章节。
