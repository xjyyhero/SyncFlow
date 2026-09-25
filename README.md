# SyncFlow

数据同步与任务管理服务：用户上传 CSV，系统异步导入数据，并提供任务状态、统计和错误明细查询。

已完成任务创建、查询、MySQL 数据访问及 Redis 队列。Week 3 第一至八节已将 CSV 读取、校验和错误记录接入真实 Worker，并提供错误明细分页 API 和页面：合法行写入 MySQL，非法行保留明细，任务产生准确的最终统计和成功/部分成功/失败状态。当前每 250 条非空记录独立提交；单批写入失败只回滚该批，保留前批成功数据并继续后批，按批更新统计。页面支持上传前校验、任务列表、详情自动刷新和错误明细分页。见 [Week 3 清单](docs/delivery/week-03-checklist.md)、[真实 Worker 验收记录](docs/delivery/week-03-worker.md)、[错误明细 API 验收记录](docs/delivery/week-03-errors-api.md)及 [React 页面验收记录](docs/delivery/week-03-pages.md)。

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
- 默认使用本机 `3306`、`6379`、`8000`、`5173`；端口占用时修改 `.env` 的 `MYSQL_HOST_PORT`、`REDIS_HOST_PORT`、`API_HOST_PORT`、`WEB_HOST_PORT`。容器内部端口保持原值。

## 一键启动与停止

在项目根目录执行：

```sh
sh scripts/start.sh
```

脚本仅在 `.env` 不存在时复制示例配置，随后构建并启动五个常驻服务及一次性 mysql-init。前端先构建再通过 Vite preview 提供本地页面；启动等待 API 与 Web 健康检查。首次镜像下载所需时间取决于网络。

- Web：http://localhost:5173
- API 文档：http://localhost:8000/docs
- 版本信息：http://localhost:8000/api/v1/info
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

`.env.example` 将当前生效配置与未来预留项分开。MySQL、上传大小、单任务行数、存储路径和队列配置已接入；Worker 并发、任务超时、重试和幂等 TTL 仅预留。示例密码仅用于本地开发。详见 [配置表与部署说明](docs/deployment/local-v1.0.md)。

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

初始化保留现有数据；会将旧任务状态约束升级为支持 PARTIAL_SUCCESS 的五状态约束，重复执行安全。连接设置 UTC、严格 SQL 模式和 5 秒连接超时。MySQL 配置来自 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。

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
PYTHONPATH=backend backend/.venv/bin/python -m app.worker
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

终端会输出报告路径：`output/db-review/<UTC时间>/index.html`，可用浏览器打开。报告包含本次测试的预期/实际断言、真实表结构、索引和数据；同目录提供 `schema.sql`、`database-snapshot.json`、`test-results.json` 及逐表 CSV。每表最多导出 500 行，超出会明确标注；原始总行数始终保留。生成目录仅保留本地，已加入 Git 忽略规则。详细说明见 [数据库人工审查说明](docs/delivery/week-02-db-review.md)。

测试使用独立 Compose 项目与临时 MySQL 数据库，不读取开发 `.env`，不挂载开发数据卷，结束后自动清理测试容器。覆盖重复初始化、任务增查与分页、状态转换、事务回滚、错误记录、金额精度、唯一约束和数据库不可用。测试也覆盖独立 Worker 消费、重复消息、文件访问失败和正常停止；新增 CSV 契约边界测试、上传后独立进程读取及规范化金额/日期的 MySQL 存储验证。

依赖精确版本记录在 `backend/requirements.txt`、`backend/requirements-dev.txt` 和 `frontend/package-lock.json`。正常安装使用这些锁定文件；`.in` 文件仅列出后端直接依赖。

## 协作

第二周已合并的交付记录：[后端与 Worker](docs/delivery/week-02-pr1.md)、[React 页面](docs/delivery/week-02-pr2.md)。前端测试使用 Node 原生 TypeScript 类型擦除，推荐使用与 Docker 镜像一致的 Node 24。

改动通过 Issue → 分支 → 本地检查 → PR → AI Code Review → 人工审核流程交付。AI 自查记录不替代人工最终审核，不自动合并。V1.0 的 PR、标签和正式发布按第九节完成。

## 创建任务

```sh
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -F 'file=@samples/sample-valid.csv' \
  -F 'name=人工测试导入'
```

成功返回 201，data 包含 id、name、PENDING 状态及创建时间，meta 为 `{}`。名称可省略，最长 128 字符。仅接收 CSV，默认上限 10 MiB，使用 `MAX_UPLOAD_FILE_SIZE_MB` 调整。

Compose 使用 `uploads` 持久卷：API 写入 `/app/uploads`，Worker 同路径只读挂载。本机运行默认保存到 `var/uploads`，可通过 `UPLOAD_DIR` 配置。原始文件名只存元数据，本地存储名称由 UUID 生成。`REDIS_ADDR` 沿用原配置（也支持 Redis URL），`REDIS_JOB_QUEUE` 默认 `syncflow:jobs`；Worker 从该列表 BLPOP 取任务 ID，通过条件更新将 PENDING 改为 RUNNING，完整预检 CSV 后分批处理，逐批原子保存业务数据、错误和统计，末批同时提交终态；文件错误在写入前直接记录为 FAILED。BLPOP 暂无崩溃恢复或自动重试，异常退出后的任务需要人工检查。详见 [Week 3 Worker 验收记录](docs/delivery/week-03-worker.md)。

入队失败会记录任务失败及文件级错误；详情见 [创建任务交付记录](docs/delivery/week-02-create-job.md)。自动验证继续使用 `python3 scripts/review-db.py`，所有测试写操作仅发生在独立 MySQL、Redis 和临时文件目录中。


## 查询任务

```sh
# 列表：省略参数时默认第 1 页，每页 20 条
curl 'http://127.0.0.1:8000/api/v1/jobs?page=1&page_size=20&status=PENDING'

# 详情：把任务 ID 替换为创建接口返回的 data.id
curl 'http://127.0.0.1:8000/api/v1/jobs/任务ID'
```

详情包含公开任务字段、记录统计、最近错误和 UTC 时间；内部存储路径不返回。任务不存在返回 404 JOB_NOT_FOUND。列表支持五种状态筛选（含 PARTIAL_SUCCESS），page_size 最大 100，非法参数返回 400 INVALID_REQUEST。空页返回空数组及正确的 meta.total。详见 [任务查询交付记录](docs/delivery/week-02-query-jobs.md)。

查询错误明细（将 `JOB_ID` 替换为创建接口返回的任务 ID）：

```sh
curl 'http://127.0.0.1:8000/api/v1/jobs/JOB_ID/errors?page=1&page_size=20'
```

默认每页 20 条，最多 100 条；按行号、内部 ID 升序，文件级错误的行号为 `null` 并排在前面。返回错误码、原因、原始 JSON 行和 UTC 时间，`meta.total` 为该任务错误总条数。已有任务无错误返回空数组；任务不存在返回 404 JOB_NOT_FOUND。详见 [错误明细 API 交付记录](docs/delivery/week-03-errors-api.md)。

### React 查询页面

访问 http://127.0.0.1:5173/ 查看任务；`/jobs/new` 上传创建并展示所选文件信息，前端限制 CSV 和默认 10 MB。`/jobs/:jobId` 查看详情，处理中每次请求完成后等待 3 秒自动刷新，成功、部分成功或失败时停止；`/jobs/:jobId/errors` 查看错误并分页，文件级错误显示空行号 `-`。详情提供错误入口，文件级失败也可进入。

状态筛选和分页同步 URL，时间按浏览器本地时区显示。前端客户端与响应类型集中在 `frontend/src/api.ts`。运行 `node --test frontend/src/api.test.ts` 验证查询参数、上传边界、终态、时间格式和 API 错误处理；`sh scripts/check.sh` 同时执行这些测试及类型检查、生产构建。真实浏览器上传、轮询及导航验收见 [第六节交付记录](docs/delivery/week-03-pages.md)。

第七节测试验收已完成：74 项后端测试、6 项前端测试、9 组浏览器验收和 3 类固定样例数据库对账通过。查看 [测试报告、复跑命令与截图](docs/delivery/week-03-tests.md) 和 [样例及预期](samples/README.md)。

## V1.0 本地发布包

[本地部署与空环境复现](docs/deployment/local-v1.0.md) · [Release Notes](docs/delivery/v1.0-release-notes.md) · [第八节验收记录](docs/delivery/week-03-release.md) · [演示与复盘初稿](docs/delivery/week-03-demo-retrospective.md)

```sh
python3 scripts/package-release.py
```

输出 `output/releases/syncflow-v1.0.0.tar.gz` 和 `.sha256`。包中包含源码、锁定依赖、配置示例、正常/异常样例、测试报告和截图；不含真实配置、上传文件、依赖缓存或 Git 历史。解压后按部署说明启动，不依赖本机源码挂载。这是本地 V1.0 源码交付包，正式标签和发布仍由第九节验收。
