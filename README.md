# SyncFlow

数据同步与任务管理服务：用户上传 CSV，系统异步导入数据，并提供任务状态、统计和错误明细查询。

当前交付 Week 1 的需求与技术设计包、开发环境、五服务骨架及 `/healthz` 存活检查，完整任务对应关系见 [Week 1 交付索引](docs/week-01-delivery.md)。当前首页与 `/api/v1/info` 用于验证骨架，尚无 CSV 处理业务；Worker 仅支持启动与正常停止。

## 技术栈与目录

后端使用 Python 3.13 + FastAPI + Uvicorn；前端使用 React、TypeScript、Vite、React Router；数据服务使用 MySQL 8.4 和 Redis 7，统一由 Docker Compose 启动。

```text
backend/app/       API 与 Worker 骨架
frontend/src/      React 页面与样式
docker/            前后端 Dockerfile
scripts/           统一启动与检查脚本
docs/              原始需求、需求理解与工作清单
compose.yaml       五服务编排
.env.example       本地配置示例
```

需求说明见 [需求理解](docs/requirements-understanding.md)，任务进度见 [完整交付索引](docs/week-01-delivery.md)。原始 PRD 为 [PDF](docs/product-requirements.pdf)，目前未提供任务原文提到的 `docs/01-product-requirements.md`，需求整理使用该 PDF。

## 设计文档

- [系统边界、流程与事务](docs/technical-design.md)
- [API 说明](docs/api-design.md)与 [OpenAPI 契约](docs/openapi.json)
- [数据库设计](docs/database-design.md)、[SQL 附件](docs/schema.sql)、[状态机](docs/state-machine.md)
- [Web 页面](docs/web-design.md)、[异常与测试计划](docs/test-plan.md)
- [AI 自查与验证记录](docs/review.md)

上述业务设计为待审核提案；完整契约与运行服务自动生成的 OpenAPI 有意分开，避免展示未实现接口为可用功能。正常 CSV 示例位于 `samples/sample-valid.csv`。

## 环境要求

- Docker Desktop 已安装并启动，支持 Docker Compose。
- 本机开发需要 Python 3.13、Node.js 22.12+（建议 Node.js 24 LTS）与 npm；纯容器启动不需要本机安装语言依赖。
- 首次安装需要网络下载镜像和依赖；运行时核心服务均在本地。
- 确保本机 `3306`、`6379`、`8000`、`5173` 端口空闲。

## 一键启动与停止

在项目根目录执行：

```sh
sh scripts/start.sh
```

脚本仅在 `.env` 不存在时复制示例配置，随后构建并启动五个服务。首次镜像下载所需时间取决于网络。

- Web：http://localhost:5173
- API 文档：http://localhost:8000/docs
- API 骨架信息：http://localhost:8000/api/v1/info
- API 存活检查：http://localhost:8000/healthz

`GET /healthz` 返回 HTTP 200 和 `{"status":"ok"}`，仅检查 API 进程能否响应，不连接 MySQL 或 Redis。Docker 使用该接口检查 API 存活；依赖就绪检查 `/readyz` 尚未实现。

已有 `.env` 时，也可使用 `docker compose up -d --build`。检查状态、查看日志及停止：

```sh
docker compose ps
docker compose logs --tail=50 api worker
docker compose down
```

停止不会删除 MySQL 数据。以下命令会删除本项目数据库卷，仅在明确需要重置本地数据时使用：

```sh
docker compose down -v
```

## 配置与数据库

`.env.example` 包含 Week 1 要求的全部配置项；复制后的 `.env` 不提交到 Git。示例密码仅用于本地开发。业务相关配置目前仅预留，后续实现时接入。

Compose 首次初始化 MySQL 数据卷时自动创建 `MYSQL_DATABASE` 指定的数据库和 `MYSQL_USER` 用户。已有数据卷不会因修改这些配置而重新初始化。

仅启动依赖：

```sh
docker compose up -d --wait mysql redis
```

验证开发数据库及 Redis：

```sh
docker compose exec mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -u "$MYSQL_USER" -D "$MYSQL_DATABASE" -e "SELECT DATABASE(), VERSION();"'
docker compose exec redis redis-cli ping
```

数据库尚未创建业务表，迁移脚本将在数据库设计明确后添加；当前没有业务迁移命令，不要手工创建未经确认的业务表。

## 本机开发环境

安装项目独立依赖：

```sh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
```

若已运行全套容器，先执行 `docker compose stop api worker web`，避免本机端口冲突。数据库和 Redis 可继续使用容器。

在三个终端分别执行：

```sh
# 终端 1：API（项目根目录）
backend/.venv/bin/uvicorn app.main:app --app-dir backend --reload

# 终端 2：Web（项目根目录）
npm --prefix frontend run dev

# 终端 3：Worker
cd backend
.venv/bin/python -m app.worker
```

Vite 将 `/api` 请求代理到本机 `8000` 端口；容器内则通过 `API_PROXY_TARGET` 指向 API 服务。本机开发时，未来涉及数据库和 Redis 的业务需将主机地址设为 `127.0.0.1`，Compose 内使用 `mysql` 和 `redis` 服务名。

## 格式、检查与构建

```sh
backend/.venv/bin/ruff format backend/app
npm --prefix frontend run format
sh scripts/check.sh
```

统一检查包括后端静态检查与格式、前端格式、TypeScript 类型检查、前端生产构建及 Compose 配置校验。仅构建前端可执行：

```sh
npm --prefix frontend run build
```

启动五个服务后，运行骨架冒烟检查（API、Web、代理与 Worker 正常停止）：

```sh
backend/.venv/bin/python scripts/smoke.py
```

当前没有业务单元测试或集成测试套件；这些将随业务实现添加。目前采用构建、服务请求、数据库查询和 Redis PING 验证骨架，不能据此认定 CSV 业务已通过测试。

依赖精确版本记录在 `backend/requirements.txt`、`backend/requirements-dev.txt` 和 `frontend/package-lock.json`。正常安装使用这些锁定文件；`.in` 文件仅列出后端直接依赖。

## 协作

改动通过 Issue → 分支 → 本地检查 → PR → AI Code Review → 人工审核流程交付。本轮使用 `chore/week-01` 提交 Week 1 PR；AI 自查记录不替代人工最终审核，不自动合并。

原始 `docs/product-requirements.pdf` 和 `docs/week-01.pdf` 仅保留在本地，按所有者要求不提交公开仓库。文档中的原始 PDF 链接仅在本地文件存在时可用。
