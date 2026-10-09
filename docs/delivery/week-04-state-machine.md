# Week 4 第一节：任务状态机

已完成：八状态定义、全部合法转换、终态保护、状态更新时间与原因、非法转换 409 和日志、CSV 处理结果判定。

## 实现

- `backend/app/repository.py`：集中状态转换、原子领取、完成与失败入口、批次终态更新。来源状态在 SQL 中检查，避免先读后写竞争。
- `backend/app/schema.sql`、`database.py`：八状态 CHECK 约束，支持从 Week 2 四状态、Week 3 五状态无损升级，重复初始化安全。
- `backend/app/api_contract.py`：八状态模型及 `INVALID_JOB_TRANSITION` 错误；OpenAPI 已重新生成。
- `frontend/src/api.ts`：新增状态中文展示、筛选与类型，CANCELED 作为终态停止刷新。详情中的原因区域改称“最近状态说明”，正常状态原因不伪装成错误码。
- 队列投递失败改用 PENDING → CANCELED，记录具体原因；已被领取或完成的任务不被补偿覆盖。详细决策见[状态机说明](../design/state-machine.md)。

## 验证

2026-10-01 验证通过：

- `sh scripts/check.sh`：代码检查、格式检查、14 项 CSV 测试、6 项前端测试、TypeScript 与生产构建、Compose 配置、OpenAPI 一致性。
- `docker compose -f compose.test.yaml up --build --attach db-tests --abort-on-container-exit --exit-code-from db-tests`：独立 MySQL / Redis 完整后端 **78 项测试通过**。
- 新增 `test_job_states.py`：全部 64 个状态组合，另加 8 个非法目标状态；非法转换通过真实 HTTP 返回 409，日志可定位且数据库记录不变；合法转换更新原因和时间。
- 两个独立数据库连接同时领取一个任务，仅一个成功；Worker 跳过所有非 PENDING 状态。
- 旧五状态约束升级、三种新增状态的详情与列表筛选、投递确认丢失后的状态保护。
- 现有 Worker 成功、部分成功、全失败、文件错误、数据库错误、批次回滚与确认丢失测试全部通过。

机器可读报告位于本地 `output/db-tests/test-results.json`（该目录不上传 Git）；测试源码和本交付记录可用于复跑和评审。

## 后续范围

本节没有实现取消 API、自动重试、执行超时、并发数配置和重启恢复；后续章节按 checklist 继续完成。部署现有数据库前需运行初始化迁移，再启动新版 API 和 Worker。
