# Week 4 第三节：Worker 并发、超时与取消

## 配置与执行方式

| 配置 | 默认值 | 规则 |
| --- | --- | --- |
| WORKER_CONCURRENCY | 根据 CPU 核数取 2–4 | 可显式设置为任意正整数；每个 Worker 服务进程的并发上限 |
| JOB_TIMEOUT_SECONDS | 300 秒 | 正整数，从领取事务提交后开始计时，包含任务子进程启动时间 |

零、负数、非整数配置在启动时拒绝，避免静默使用错误并发数或超时时间。`.env.example` 已将这两个字段从预留配置改为实际生效配置；Compose 通过现有 env_file 注入，本机运行需自行导出环境变量。

固定数量的消费者各自持有 Redis 客户端、阻塞取消息，并使用第一节的条件更新领取任务。每个已领取任务创建一个独立 Python 子进程，复用原有 CSV 校验、批次事务和状态机；父进程中的该消费者只监控自己的子进程、截止时间和数据库状态。一个消费者结束当前任务后才取下一条消息，因此活动任务不会超过 WORKER_CONCURRENCY；部署多个 Worker 服务实例时，各实例并发数相加。

使用标准库 ThreadPoolExecutor 和 subprocess，没有新增依赖。子进程隔离使阻塞文件读取也能被终止，不依赖线程取消或 CSV 自愿返回。

## 超时与资源释放

监控循环最多每 0.2 秒检查一次任务，使用单调时钟判断超时。超时后先终止子进程，等待最多 1 秒，必要时强制结束并等待回收，再将仍未完成的任务置为 FAILED，记录 JOB_TIMEOUT 和文件级错误。

子进程退出会关闭文件句柄和数据库连接，未提交事务回滚，已提交批次及其计数保留；超时不把尚未处理的行伪装成行级失败。监控查询使用 1 秒连接/读写超时，常规数据库操作使用 5 秒连接/读写超时，避免监控本身无限等待。总耗时可能额外包含监控、进程回收和最终数据库更新时间。

启动子进程失败或监控失败记录 WORKER_CONTROL_FAILED；子进程退出却未提交终态记录 WORKER_PROCESS_EXITED。最终更新会锁定任务行重新检查，若任务已经提交 SUCCESS 等终态则保留结果。数据库完全不可用时最终状态可能无法回写，但进程仍先被清理并记录错误日志；该类遗留状态的重启恢复在第四节实现。

## 取消接口与上下文

```sh
curl -X POST 'http://127.0.0.1:8000/api/v1/jobs/JOB_ID/cancel'
```

返回统一 `SuccessResponse[JobDetail]`，HTTP 200：

- PENDING / RETRYING → CANCELED，任务不会再被 Worker 领取。
- RUNNING → CANCELING，表示取消请求已经记录，尚未承诺执行已停止。
- 对应消费者观察到 CANCELING 后，停止并回收该任务子进程，再将状态变为 CANCELED，记录 JOB_CANCELED。
- 重复取消 CANCELING 或终态任务返回 409 INVALID_JOB_TRANSITION；任务不存在返回 404 JOB_NOT_FOUND。

取消状态存在数据库中，任务上下文（子进程和截止时间）由所属消费者独立持有。取消 API 与领取、批次提交使用同一任务行锁串行化；已完成的任务不能被取消覆盖。取消时保留已提交记录、总数和进度，不影响其他任务。取消或监控过程中发生故障时允许按状态机从 CANCELING 进入 FAILED，保留具体原因。

## 验证

2026-10-06：完整后端 **88 项测试通过**；项目检查通过，包含 14 项 CSV 测试、6 项前端测试、类型检查、生产构建、Compose 配置及 OpenAPI 一致性。

新增 `backend/tests/test_worker_control.py` 六项测试：

1. CPU 默认并发、显式配置及非法配置拒绝。
2. 取消 API 的全部八状态、重复取消、404、409 和 OpenAPI 声明。
3. 两个真实 FIFO 文件读取同时阻塞，第三任务保持等待；超时后两个进程退出，第三任务成功，重复消息不重复写入，服务保持运行。
4. 取消阻塞任务后进入 CANCELED，保留预先提交的 250 条记录；同批其他任务和后续任务独立成功。
5. 真实子进程在 MySQL 中插入未提交记录后阻塞，超时结束后确认事务回滚、记录不存在、进程已回收。
6. 子进程启动失败和异常退出均记录 FAILED 与对应错误码，不泄露内部异常内容。

既有 82 项队列、状态机、CSV、数据库及 API 测试全部回归通过。旧的顺序执行场景显式设置并发为 1，新场景使用并发 2 检查上限与隔离。

复跑：

```sh
sh scripts/check.sh
docker compose -f compose.test.yaml run --rm -v "$(pwd)/backend/app:/app/app:ro" db-tests
docker compose -f compose.test.yaml down
```

没有本地测试镜像时先使用 `sh scripts/test-db.sh`。本地机器可读测试报告为 `output/db-tests/test-results.json`。

## 后续

本节完成单任务超时和取消。整个 Worker 服务停止时的统一收尾期限、父进程异常退出后的遗留任务扫描仍由第四节完成；WORKER_SHUTDOWN_TIMEOUT_SECONDS 目前仅配置 Compose 的停止宽限时间。
