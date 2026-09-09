# Week 1 前五项交付记录

## 工作范围

本轮只处理 Week 1 任务列表的前五项。第六项 `/healthz`、第七项分支与 PR 及后续设计任务不在本轮范围。

| 项目 | 产出 | 验证状态 |
| --- | --- | --- |
| 1. 阅读 PRD | 已阅读 `product-requirements.pdf`；原任务提到的 Markdown 源文件未提供，使用 PDF 版本 | 已完成 |
| 2. 整理名词、角色和流程 | `requirements-understanding.md` 包含目标、角色、核心流程、版本范围、疑问清单与名词表 | 已完成，未明确的业务规则保留待确认 |
| 3. 开发环境 | Python 独立虚拟环境，FastAPI；Node.js、React、TypeScript、Vite、React Router | 已完成，依赖安装、类型检查及生产构建通过 |
| 4. 数据库环境 | Compose 配置 MySQL 8.4、Redis 7，自动创建本地开发数据库 | 已完成，数据库查询成功，Redis 返回 PONG |
| 5. 项目骨架 | API、Web、Worker 骨架，README、统一启动及检查脚本 | 已完成，五服务启动，Web/API 与代理请求验证通过 |

## 实测记录（2026-09-08）

- 本机 Python 3.13.7、Node.js 25.8.0、npm 11.11.0；容器使用 Python 3.13 和 Node.js 24。
- 后端 FastAPI 0.141.1；前端 React 19.2.8、TypeScript 7.0.2、Vite 8.2.2、React Router 8.3.1。精确依赖已锁定。
- `sh scripts/start.sh` 成功完成镜像构建和五服务启动。
- MySQL、Redis、API 的 Compose 健康检查通过；Web 和 Worker 处于运行状态。
- MySQL 查询返回数据库 `syncflow`、版本 `8.4.11`；Redis PING 返回 `PONG`。
- 后端静态检查、代码格式、TypeScript 检查、前端生产构建和 Compose 配置检查通过。
- `backend/.venv/bin/python scripts/smoke.py` 通过：API、Web、Web 到 API 的代理、Worker 启动和正常停止。
- `.env`、虚拟环境、node_modules 和构建目录被 Git 忽略。
- 首次启动已实测成功；尚未由另一位新成员独立复现“15 分钟启动”验收。

## 使用入口

按照 [README](../README.md) 启动和验证。后端技术栈沿用已有 README 的 Python + FastAPI 选择。

## 已知范围限制

- 当前只有骨架页面和服务信息接口，不提供上传、任务处理、重试或导出。
- Worker 能启动并响应停止信号，尚不消费 Redis 队列。
- 仅创建开发数据库，业务表和迁移在后续设计明确后实现。
- 需求中的 CSV 字段和校验细则仍待后续任务文档或需求方确认。
