# Week 2 PR 1：数据库、任务 API 与最小 Worker

分支：`feature/week2-backend-api-worker`。本 PR 实现完整后端链路，前端继续使用 Week 1 骨架；React 任务查询页面另行提交 PR 2。

## 实现

- 初始化 sync_jobs、sync_records、sync_errors 三表，UTC 毫秒时间、索引与唯一约束，初始化可重复执行。
- 创建、详情及列表 API；统一 data/meta 与 error 响应、输入校验、公开字段模型和 OpenAPI。
- CSV 扩展名与可配置上传大小限制；服务端 UUID 文件名、SHA-256、受控目录保存。
- 数据库提交后投递 Redis；投递失败记录 FAILED 与错误。数据库回写也失败时保留此前已提交的待投递标记。
- 独立 Worker 消费 Redis、条件认领 PENDING、提交 RUNNING，再模拟 SUCCESS，保留首次 started_at 与 finished_at，日志包含 job_id。
- API 与 Worker 共享上传卷，Worker 只读挂载；文件无法读取时记录 FAILED。

## 验证

2026-09-18 在此独立分支重跑，而非使用含 PR 2 的工作区结果：

- `sh scripts/check.sh`：Ruff、前端骨架格式/类型/生产构建、Compose 配置和 OpenAPI 一致性通过。[检查输出](evidence/week-02-pr1/checks.txt)
- `sh scripts/test-db.sh`：35 项测试、473 条断言通过。[逐项结果](evidence/week-02-pr1/tests.txt)
- 测试覆盖真实 MySQL 与 Redis、独立 Worker 子进程，上传创建→队列消费→SUCCESS、重复消息和正常停止。
- 测试库使用单独 Compose 项目与临时存储，不使用开发数据库凭据或数据卷。

复现：按 README 安装依赖；将 .env.example 复制为本地 .env；运行上述两条命令。后端集成测试只需 Docker，会自动构建测试镜像。

数据库人工审查：开发服务启动后运行 `python3 scripts/review-db.py`，生成本地 HTML、SQL、JSON 和 CSV；导出采用只读事务，每表最多 500 行并标明截断。真实数据库快照和上传文件不纳入 PR。

## AI 自查记录

这是实现者 Codex 的自查，不冒充独立审阅者，也不替代人工最终审核。

- 核对路由仅承担 HTTP/模型映射；业务层管理文件、事务与队列；数据访问层使用参数化 SQL。
- 核对路径使用服务端 UUID，独占创建防止覆盖与已有符号链接；原始文件名仅存元数据。
- 核对查询输出不包含内部路径，未预期异常使用固定对外错误；相关测试通过。
- 核对数据库提交确认丢失时保留可能已提交任务的输入文件；Redis 确认丢失不重试投递；测试覆盖这些边界。
- 核对 Worker 通过 PENDING 条件认领避免重复处理，首次 started_at 不覆盖。
- 拆分检查发现共享 README 与检查脚本依赖前端 PR 2，已移除本 PR 中的相关声明和测试引用，独立检查通过。
- 分支 diff 未包含前端页面改动、真实 .env、上传文件或数据库快照。

## 已知范围与最终审核

本周模拟完成，不解析 CSV，统计保持 0。BLPOP 无消息确认和崩溃恢复，数据库中断或进程强制终止后的已出队任务需要人工核查；自动重试与恢复后续实现。

初始化只对缺失表执行 CREATE TABLE IF NOT EXISTS，不负责升级已有表结构；后续变更需独立迁移。

人工最终 Review 与合并仍待完成；本 PR 不自动合并。
