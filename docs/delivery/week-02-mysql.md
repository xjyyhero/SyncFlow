# Week 2 MySQL 交付记录

验证日期：2026-09-16（Australia/Sydney）。范围：Week 2 清单第一部分「MySQL 与数据访问层」。

## 已完成

- [三表初始化脚本](../../backend/app/schema.sql)：`sync_jobs`、`sync_records`、`sync_errors`，包含要求的字段、类型、默认值、索引及唯一约束。
- [数据库连接与初始化](../../backend/app/database.py)：读取 MySQL 环境配置、UTC 会话、严格 SQL 模式、事务提交/回滚及连接关闭；使用驱动原生多语句执行初始化。
- [数据访问层](../../backend/app/repository.py)：任务创建、详情、状态筛选与稳定分页、开始/完成/失败更新、文件级与行级错误记录。SQL 参数化，业务调用方拥有事务边界。
- [健康检查](../../backend/app/main.py)：`/healthz` 检查存活；`/readyz` 检查 MySQL 连通性，故障返回 503，固定错误响应不包含连接地址、凭据或堆栈。
- [启动配置](../../compose.yaml)：一次性 `mysql-init` 服务在 API 和 Worker 启动前完成初始化。
- [独立测试配置](../../compose.test.yaml)：单独 Compose 项目、用户、数据库和临时存储，不加载开发 `.env`、不挂载开发数据库卷。

## 实测结果

| 验证 | 结果 |
| --- | --- |
| `sh scripts/test-db.sh` | 6 项 MySQL 8.4 集成测试全部通过，退出码 0，测试容器与网络已清理 |
| 重复初始化 | 空库初始化成功，写入后再次连续初始化两次，表结构和已有数据保留 |
| 任务数据访问 | 默认值、UTC、合法状态转换、重复状态更新保护、任务不存在场景通过 |
| 分页与筛选 | 同创建时间按 ID 稳定排序、跨页、空页、最大页大小、非法参数与参数化查询通过 |
| 事务与错误记录 | 创建回滚、失败状态与错误记录一起回滚/提交、可空文件级错误、JSON 行摘要通过 |
| 数据约束 | 精确 Decimal 金额、同任务业务标识唯一、跨任务重复允许、大写标识、非法状态及负计数拒绝通过 |
| 数据库异常 | 实际连接不可用时检查失败；健康响应 503 且不泄露连接信息 |
| `sh scripts/check.sh` | 后端静态与格式检查、前端格式/类型/生产构建、两份 Compose 配置校验通过 |
| 开发环境启动 | `docker compose up -d --build --wait --wait-timeout 180 api` 成功；初始化服务正常退出，API 健康 |
| 开发库核对 | 三张 `sync_` 表存在，重复初始化成功，三表业务行数均为 0，未写入测试数据 |
| 真实 HTTP 请求 | `/healthz` 和 `/readyz` 均为 HTTP 200；后者返回 `{"status":"ok","checks":{"mysql":"ok"}}` |

## 复现

在项目根目录运行：

```sh
# 完整独立数据库测试，自动构建并清理测试环境
sh scripts/test-db.sh

# 项目静态检查与构建
sh scripts/check.sh

# 初始化开发库（需要已有 .env；首次启动可用 scripts/start.sh）
docker compose run --build --rm mysql-init
```

## 当前边界

- 初始化可重复执行且不删除已有数据；`CREATE TABLE IF NOT EXISTS` 不会修复已有同名表的结构差异，后续升级需要新增 ALTER 迁移。
- Week 1 的 [SQL 设计提案](../design/schema.sql) 保留作历史参考，实际执行脚本为 `backend/app/schema.sql`。
- 数据访问方法返回内部数据库行，后续任务 API 需要选择公开字段，不能直接返回存储路径。
- `/readyz` 仅检查 MySQL 连通性，不代表 Redis、业务表结构或完整任务处理流程已就绪。
- 任务 HTTP API、Redis 投递、Worker 消费、CSV 解析和前端页面不属于本次 MySQL 交付；幂等与重试字段仅预留。
