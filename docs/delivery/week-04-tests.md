# Week 4 第六节：测试与验收

2026-10-08 使用当前工作区代码，在独立 MySQL / Redis 环境完成验收。复用现有测试与 Chrome，不增加依赖；测试数据与开发环境隔离。

## 结果与覆盖

| 清单要求 | 本次证据 | 结果 |
| --- | --- | --- |
| 创建快速返回，随后实际执行 | Worker 尚未启动时，5 次真实 HTTP 上传均返回 201 / PENDING，耗时 63.7–69.2 ms；启动 Worker 后完成成功、部分成功、文件失败、数据库失败及故障后成功场景 | 通过 |
| 状态机、终态、重复消息、并发 | 全部合法/非法转换；终态不可重复领取；真实 Redis 重复消息；并发 2 个阻塞任务时第三任务保持等待，释放槽位后继续 | 通过 |
| 投递补偿、Worker 异常、超时、取消 | 投递失败与确认丢失、扫描补投、子进程启动/退出失败、真实超时中断、事务回滚、取消后保留已提交批次 | 通过 |
| 停止及重启恢复 | 真实 SIGTERM / SIGINT、共用收尾期限、允许期内完成、父进程 SIGKILL、遗留任务恢复、最终状态回写失败后非零退出 | 通过 |
| 页面轮询与清理 | 7 组浏览器检查覆盖四个非终态/终态、失败重试、保留数据、慢请求、StrictMode、重入与路由切换、取消旧请求 | 通过 |
| 结果及异常可追溯 | 机器可读结果、API / Worker 日志、异常摘录；回写失败包含 job_id 和 recovery required，并断言退出码为 1 | 通过 |

完整后端 **96 项测试通过，0 失败、0 跳过**。项目检查通过：14 项 CSV 测试（已包含在 96 项内）、6 项前端测试、格式与类型检查、生产构建、Compose 配置和 OpenAPI 一致性。

真实浏览器联合验收复用 `check-week03-pages.cjs`，**9 组检查全部通过**：3 个 CSV 样例从页面上传，经实际 API / Redis / Worker 执行后，页面状态、统计和错误明细与 MySQL 对账一致。脚本中的模拟计时检查与真实上传阶段分开；额外的 Week 4 轮询脚本覆盖全部 8 个状态。

创建耗时是本机样例的观测值，不作为吞吐量或生产延迟承诺。取消、超时和停止使用真实进程/信号及受控阻塞输入；数据库故障与提交确认丢失包含故障注入。恢复扫描测试会调整任务时间以模拟过期。完整自动重试机制仍属于第 5 周。

## 验收中修复

旧浏览器脚本点击返回列表后立即推进时钟，可能在 React 完成路由卸载前触发下一轮请求，首次运行在请求计数断言失败。现已固定受控时钟，并等待列表页面出现后检查卸载结果；重跑通过。没有为通过测试修改业务逻辑或放宽断言。首次失败日志保存在本地 `output/week-04-tests/live-browser-first-attempt.log`。

## 证据

- 随仓库保存：[结果快照](week-04-test-results.json)、[异常日志摘录](week-04-failure-evidence.md)。
- 本地完整记录：`output/week-04-tests/` 下的 `project-check.log`、`backend.log`、`backend-results.json`、`worker-scenarios.json`、`worker.log`、`api.log`。
- 浏览器记录：`polling/` 与 `live/` 内的 JSON 报告及截图，另有 `polling.log`、`live-browser.log`、`database-check.log`、`live-api.log` 和 `live-worker.log`。
- 第七节的功能演示与第八节最终提交尚未完成；本节不代替最终推送。

## 复跑

前置：项目依赖、Docker、Chrome 和 Playwright 已安装。共享运行时的 Playwright 可通过 `NODE_PATH` 指定，不需要加入项目依赖。首次没有测试镜像时先执行 `docker compose -f compose.test.yaml build db-tests`。

```sh
mkdir -p output/week-04-tests
sh scripts/check.sh > output/week-04-tests/project-check.log 2>&1
docker compose -f compose.test.yaml run --rm \
  -v "$PWD/backend/app:/app/app:ro" db-tests \
  > output/week-04-tests/backend.log 2>&1
cp output/db-tests/test-results.json output/week-04-tests/backend-results.json
cp output/db-tests/week03-worker-scenarios.json output/week-04-tests/worker-scenarios.json
cp output/db-tests/week03-worker.log output/week-04-tests/worker.log
cp output/db-tests/week03-api.log output/week-04-tests/api.log
```

后端测试完成后再启动联合验收服务，避免测试清表影响浏览器：

```sh
docker compose -f compose.test.yaml run -d --no-deps \
  --name syncflow-week04-acceptance-api -p 127.0.0.1:18004:8000 \
  -e UPLOAD_DIR=/tmp/week04-uploads -v "$PWD/backend/app:/app/app:ro" db-tests \
  sh -c 'python -m app.database && exec uvicorn app.main:app --host 0.0.0.0 --port 8000'
```

等待 `http://127.0.0.1:18004/readyz` 返回 200 后：

```sh
docker exec -d syncflow-week04-acceptance-api \
  sh -c 'exec python -m app.worker > /tmp/week04-worker.log 2>&1'
API_PROXY_TARGET=http://127.0.0.1:18004 npm --prefix frontend run dev -- --host 127.0.0.1 --port 15174
```

保持前端运行，在另一终端执行：

```sh
WEB_ORIGIN=http://127.0.0.1:15174 REPORT_DIR=output/week-04-tests/polling \
  node scripts/check-week04-polling.cjs > output/week-04-tests/polling.log 2>&1
WEB_ORIGIN=http://127.0.0.1:15174 REPORT_DIR=output/week-04-tests/live \
  node scripts/check-week03-pages.cjs > output/week-04-tests/live-browser.log 2>&1
docker compose -f compose.test.yaml run --no-deps --rm \
  -v "$PWD/backend/app:/app/app:ro" -v "$PWD/samples:/samples:ro" \
  -v "$PWD/output/week-04-tests/live:/acceptance" db-tests \
  python tests/verify_browser.py /acceptance /samples \
  > output/week-04-tests/database-check.log 2>&1
docker logs syncflow-week04-acceptance-api > output/week-04-tests/live-api.log 2>&1
docker cp syncflow-week04-acceptance-api:/tmp/week04-worker.log output/week-04-tests/live-worker.log
```

最后停止本次前端进程，清理隔离容器；失败时也执行清理：

```sh
docker rm -f syncflow-week04-acceptance-api
docker compose -f compose.test.yaml down
```
