# SyncFlow

数据同步与任务管理服务：用户上传 CSV，系统异步导入数据，并提供任务状态、统计和错误明细查询。

已完成 Week 2 MySQL 三表初始化、连接、数据访问层、统一响应/参数校验模块和 HTTP/数据库测试；创建任务 API 已支持文件保存、SHA-256、入库与 Redis 投递；任务列表和详情查询已接入真实数据库；最小 Worker 已接入 Redis 消费并模拟完成任务。此前交付 Week 1 的需求与技术设计包、开发环境、五服务骨架及 `/healthz` 存活检查，完整任务对应关系见 [Week 1 交付索引](docs/delivery/week-01-delivery.md)。React 查询页面已接入真实 API，支持列表、筛选、分页、详情和创建；CSV 解析尚未实现；Worker 会检查上传文件可读性，再将任务更新为 SUCCESS。

## 技术栈与目录

后端使用 Python 3.13 + FastAPI + Uvicorn；前端使用 React、TypeScript、Vite、React Router；数据服务使用 MySQL 8.4 和 Redis 7，统一由 Docker Compose 启动。

```text
backend/app/       API 与 Worker
frontend/src/      React 页面与样式
docker/            前后端 Dockerfile
scripts/           统一启动与检查脚本
docs/              原始需求、需求理解与工作清单
compose.yaml       五服务编排
.env.example       本地配置示例
```

需求说明见 [需求理解](docs/requirements/requirements-understanding.md)，任务进度见 [完整交付索引](docs/delivery/week-01-delivery.md)

## 设计文档

全部文档按类别整理，见 [文档导航](docs/README.md)。

- [系统边界、流程与事务](docs/design/technical-design.md)
- [API 说明](docs/design/api-design.md)与 [OpenAPI 契约](docs/design/openapi.json)
- [数据库设计](docs/design/database-design.md)、[SQL 附件](docs/design/schema.sql)、[状态机](docs/design/state-machine.md)
- [Web 页面](docs/design/web-design.md)、[异常与测试计划](docs/design/test-plan.md)
- [AI 自查与验证记录](docs/delivery/review.md)

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

`GET /healthz` 返回 HTTP 200 和 `{"status":"ok"}`，仅检查 API 进程能否响应，不连接 MySQL 或 Redis。Docker 使用该接口检查 API 存活；`GET /readyz` 检查 MySQL 连通性，正常返回 200，失败返回 503 和固定错误响应；不检查 Redis 或业务表结构。

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

`.env.example` 包含 Week 1 要求的全部配置项；示例密码仅用于本地开发。MySQL、上传大小、存储路径和队列配置已接入；幂等、重试等后续业务配置仍预留。

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

Week 2 的实际建表脚本为 [backend/app/schema.sql](backend/app/schema.sql)，创建 `sync_jobs`、`sync_records`、`sync_errors`。完整启动时 `mysql-init` 自动执行，成功后 API 与 Worker 才启动。单独初始化或重复执行：

```sh
docker compose run --build --rm mysql-init
```

初始化保留现有数据，不会修改已有同名表结构；后续字段升级需要新增迁移。连接设置 UTC、严格 SQL 模式和 5 秒连接超时。MySQL 配置来自 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。

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

Vite 将 `/api` 请求代理到本机 `8000` 端口；容器内则通过 `API_PROXY_TARGET` 指向 API 服务。本机 API 连接数据库前，需将 `MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` 导出到进程环境，并将 `MYSQL_HOST` 设为 `127.0.0.1`（默认值），端口默认 3306。程序不自动加载 `.env`；Compose 通过 `env_file` 注入配置，使用 `mysql` 和 `redis` 服务名。

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

MySQL 集成测试（需要 Docker，自动构建测试镜像）：

```sh
sh scripts/test-db.sh
```

生成可展开的测试报告，并只读导出当前开发库供人工审查（需要 API 正在运行）：

```sh
python3 scripts/review-db.py
```

终端会输出报告路径：`output/db-review/<UTC时间>/index.html`，可用浏览器打开。报告包含本次测试的预期/实际断言、真实表结构、索引和数据；同目录提供 `schema.sql`、`database-snapshot.json`、`test-results.json` 及逐表 CSV。每表最多导出 500 行，超出会明确标注；原始总行数始终保留。生成目录仅保留本地，已加入 Git 忽略规则。详细说明见 [数据库人工审查说明](docs/delivery/week-02-pr1.md)。

测试使用独立 Compose 项目与临时 MySQL 数据库，不读取开发 `.env`，不挂载开发数据卷，结束后自动清理测试容器。覆盖重复初始化、任务增查与分页、状态转换、事务回滚、错误记录、金额精度、唯一约束和数据库不可用。测试也覆盖独立 Worker 消费、重复消息、文件访问失败和正常停止；CSV 解析尚未实现。

依赖精确版本记录在 `backend/requirements.txt`、`backend/requirements-dev.txt` 和 `frontend/package-lock.json`。正常安装使用这些锁定文件；`.in` 文件仅列出后端直接依赖。

## 协作

改动通过 Issue → 分支 → 本地检查 → PR → AI Code Review → 人工审核流程交付。后端通过 `feature/week2-backend-api-worker` 交付；前端通过 `feature/week2-react-job-pages` 交付；AI 自查记录不替代人工最终审核，不自动合并。

## 创建任务

```sh
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -F 'file=@samples/sample-valid.csv' \
  -F 'name=人工测试导入'
```

成功返回 201，data 包含 id、name、PENDING 状态及创建时间，meta 为 `{}`。名称可省略，最长 128 字符。仅接收 CSV，默认上限 10 MiB，使用 `MAX_UPLOAD_FILE_SIZE_MB` 调整。

Compose 使用 `uploads` 持久卷：API 写入 `/app/uploads`，Worker 同路径只读挂载。本机运行默认保存到 `var/uploads`，可通过 `UPLOAD_DIR` 配置。原始文件名只存元数据，本地存储名称由 UUID 生成。`REDIS_ADDR` 沿用原配置（也支持 Redis URL），`REDIS_JOB_QUEUE` 默认 `syncflow:jobs`；Worker 从该列表 BLPOP 取任务 ID，通过条件更新将 PENDING 改为 RUNNING，确认文件可读后模拟完成为 SUCCESS。记录统计暂为 0；真正 CSV 解析在第 3 周实现。BLPOP 暂无崩溃恢复或自动重试，异常退出后的任务需要人工检查。详见 [Worker 验收记录](docs/delivery/week-02-pr1.md)。

入队失败会记录任务失败及文件级错误；详情见 [创建任务交付记录](docs/delivery/week-02-pr1.md)。自动验证继续使用 `python3 scripts/review-db.py`，所有测试写操作仅发生在独立 MySQL、Redis 和临时文件目录中。


## 查询任务

```sh
# 列表：省略参数时默认第 1 页，每页 20 条
curl 'http://127.0.0.1:8000/api/v1/jobs?page=1&page_size=20&status=PENDING'

# 详情：把任务 ID 替换为创建接口返回的 data.id
curl 'http://127.0.0.1:8000/api/v1/jobs/任务ID'
```

详情包含公开任务字段、记录统计、最近错误和 UTC 时间；内部存储路径不返回。任务不存在返回 404 JOB_NOT_FOUND。列表支持四种状态筛选，page_size 最大 100，非法参数返回 400 INVALID_REQUEST。空页返回空数组及正确的 meta.total。详见 [任务查询交付记录](docs/delivery/week-02-pr1.md)。

## React 任务页面

访问 http://127.0.0.1:5173/ 查看任务列表，`/jobs/:jobId` 查看详情，`/jobs/new` 上传创建。筛选、分页同步 URL，时间使用浏览器本地时区。

前端单元测试使用 Node 原生 TypeScript 类型擦除，推荐 Node 24（与 Docker 镜像一致）。执行 `node --test frontend/src/api.test.ts`；`sh scripts/check.sh` 同时运行这些测试、类型检查和生产构建。

完整服务启动后，运行 `python3 scripts/acceptance-e2e.py` 经 Web 代理创建验收任务并等待 SUCCESS。每次执行会在开发库新增一条带时间戳的验收任务，并保留上传文件；隔离后端测试仍使用独立测试数据库。

[PR 2 交付、截图与验收记录](docs/delivery/week-02-pr2.md)。
