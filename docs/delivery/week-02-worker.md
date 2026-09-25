# Week 2 最小 Worker 验收

## 已完成

- 独立 Worker 服务使用 Redis BLPOP 消费任务 ID。
- 条件更新认领 PENDING 任务，提交 RUNNING，保留首次 started_at；重复消息、不存在和非 PENDING 任务跳过。
- 确认上传文件在受控目录中且可读，再模拟完成，提交 SUCCESS 和 finished_at。
- 文件缺失或目录越界时记录 FAILED 和 WORKER_PROCESSING_FAILED。
- API 创建入队及 Worker 接收、运行、完成日志均包含 job_id。
- Compose 中 API、Worker 分开启动；共享 uploads 卷，Worker 只读挂载。

## 自动验证

35 项自动测试全部通过，使用隔离 MySQL、Redis；新增 4 项 Worker 测试覆盖状态提交与时间戳、文件缺失/越界、重复及无效任务、独立进程真实消费和 SIGTERM 正常退出。代码检查、前端构建、Compose 配置与 OpenAPI 一致性检查通过。

复跑：`python3 scripts/review-db.py`。

[本次可视化报告及真实数据库快照](../../output/db-review/20260918T081218836636Z/index.html)

## 真实容器验收

2026-09-18 使用开发 API 上传 worker-acceptance.csv，创建接口返回 201 / PENDING；独立 Worker 经 Redis 消费后，详情接口返回 SUCCESS。

| 字段 | 结果 |
| --- | --- |
| 任务名称 | Week 2 Worker 链路验收 |
| 任务 ID | 6e247fc2-cc87-45e4-93d2-b1836f7e41d8 |
| created_at | 2026-09-18T08:12:02.808000Z |
| started_at | 2026-09-18T08:12:02.824000Z |
| finished_at | 2026-09-18T08:12:02.832000Z |
| 文件访问 | Worker 容器可读取共享文件，SHA-256 与数据库一致 |

日志已核对同一个 job_id 的 created and queued、received、RUNNING、upload readable、SUCCESS。验收任务及文件保留在开发环境，供人工审查。

## 本周范围

仅模拟完成，不解析 CSV，记录统计保持 0。BLPOP 暂无消息确认、崩溃恢复及自动重试；强制终止或数据库中断后需要人工核查已出队任务。真正 CSV 处理与重试按后续周次实现。
