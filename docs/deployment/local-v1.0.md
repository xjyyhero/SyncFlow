# V1.0 本地部署

本指南使用源码包和 Docker Compose 启动完整应用。只需要 Docker Desktop/Engine 与 Compose v2，首次构建需要网络下载镜像和锁定依赖；无需本机 Python/Node。浏览器自动验收和源码打包另需相应本机运行时，不影响正常部署。

## 校验并解压

将 `syncflow-v1.0.0.tar.gz` 与同名 `.sha256` 文件放在同一目录：

```sh
shasum -a 256 -c syncflow-v1.0.0.tar.gz.sha256
tar -xzf syncflow-v1.0.0.tar.gz
cd syncflow-v1.0.0
shasum -a 256 -c MANIFEST.sha256
```

Linux 也可用 `sha256sum -c`。清单验证的是原始源码包，配置或编辑后再次验证会报告对应文件变更。

## 配置与启动

```sh
cp .env.example .env
chmod 600 .env
sh scripts/start.sh
```

脚本不会覆盖已有 `.env`。使用五个常驻服务（MySQL、Redis、API、Worker、Web），`mysql-init` 是正常退出的一次性第六个容器。启动顺序：数据库就绪 → 初始化/迁移成功 → API 与 Worker → Web。初始化保留已有数据，可重复运行。

默认入口：Web `http://127.0.0.1:5173`，API 文档 `http://127.0.0.1:8000/docs`。前端容器通过 Vite preview 提供已构建页面，`/api` 转发给 `http://api:8000`；刷新深层页面也返回 SPA。健康检查确认 API 和 Web 可访问；`docker compose ps -a` 中 Worker 应为 running，mysql-init 应为 Exited (0)。

| 配置 | 默认 | 生效范围 |
| --- | --- | --- |
| MYSQL_HOST / MYSQL_PORT | mysql / 3306 | 容器内部地址，不要填写宿主映射端口 |
| MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD | syncflow / syncflow / 示例密码 | API、Worker、初始化；空卷首次创建库与用户；示例密码仅限本地 |
| REDIS_ADDR / REDIS_JOB_QUEUE | redis:6379 / syncflow:jobs | 创建接口投递和 Worker 消费 |
| MAX_UPLOAD_FILE_SIZE_MB | 10 | 每 MB 按 1024² 字节；API 与 Worker 均校验 |
| MAX_RECORDS_PER_JOB | 10000 | 所有非空数据记录，包括行校验失败记录；空行不计入 |
| UPLOAD_DIR | /app/uploads | Compose 固定同一路径：API 可写，Worker 只读 |
| WORKER_SHUTDOWN_TIMEOUT_SECONDS | 30 | Compose 停止宽限时间，不是任务超时 |
| MYSQL_HOST_PORT / REDIS_HOST_PORT | 3306 / 6379 | 本机工具连接端口 |
| API_HOST_PORT / WEB_HOST_PORT | 8000 / 5173 | 浏览器/curl 入口端口 |

`WORKER_CONCURRENCY`、`JOB_TIMEOUT_SECONDS`、`MAX_JOB_ATTEMPTS`、`RETRY_BACKOFF_SECONDS`、`IDEMPOTENCY_KEY_TTL_HOURS` 仅预留，V1.0 不执行。当前 Worker 单进程串行处理；前端上传仍使用默认 10 MB 上限。

已有 MySQL 卷不会因改 `.env` 自动改库名或密码；修改配置后需与实际数据库用户一致。单独重跑初始化：

```sh
docker compose run --rm mysql-init
```

## 不影响开发环境的空环境复现

在新解压目录中编辑 `.env`，保留容器内部主机名与端口，只修改以下宿主端口和测试库名：

```dotenv
COMPOSE_PROJECT_NAME=syncflow-v1-verify
MYSQL_DATABASE=syncflow_release_test
MYSQL_HOST_PORT=13316
REDIS_HOST_PORT=16389
API_HOST_PORT=18080
WEB_HOST_PORT=15180
```

确认 `docker volume ls` 中没有 `syncflow-v1-verify_mysql_data` 和 `syncflow-v1-verify_uploads`，再运行 `sh scripts/start.sh`。Compose 项目名会隔离容器、网络、镜像名和数据卷，启动脚本显示实际映射端口。验收环境为 Web `15180`、API `18080`。使用新卷验证首次初始化，无需删除开发卷。镜像层允许复用本机缓存；这不是断网安装包。

## 完整流程与 API 示例

在 Web 创建页依次上传 `samples/` 中三类文件，预期见 [样例说明](../../samples/README.md)。详情会自动刷新，终态停止；失败或部分成功可进入错误页。正常 5/5/0、混合 22/1/21、错误表头 0/0/0 且有一条文件错误。

以下以默认端口为例，验收环境改为 18080：

```sh
curl -f http://127.0.0.1:8000/readyz
curl -i http://127.0.0.1:8000/api/v1/jobs \
  -F 'file=@samples/sample-mixed.csv' -F 'name=V1.0 混合样例'
```

返回 201 的 `data.id` 填入下面的 `JOB_ID`（名称可省略）：

```sh
curl 'http://127.0.0.1:8000/api/v1/jobs?page=1&page_size=20&status=PARTIAL_SUCCESS'
curl 'http://127.0.0.1:8000/api/v1/jobs/JOB_ID'
curl 'http://127.0.0.1:8000/api/v1/jobs/JOB_ID/errors?page=1&page_size=20'
curl 'http://127.0.0.1:8000/api/v1/jobs/JOB_ID/errors?page=2&page_size=20'
```

分页最大 100；非法参数为 400，不存在任务为 404 JOB_NOT_FOUND。文件级行号为 null，页面显示 `-`，错误计数与失败行数可能不同。

## 运行检查与文件共享

```sh
docker compose ps -a
docker compose logs --tail=50 api worker mysql-init
docker compose exec redis redis-cli ping
docker compose exec api ls -l /app/uploads
docker compose exec worker ls -l /app/uploads
```

同一任务对应同名 UUID.csv；API 文件权限 0600，当前两个容器均以相同用户读取，Worker 挂载为只读。运行中不要单独修改某一服务的用户或上传路径；若调整用户，需同时调整共享卷权限。上传内容不打印到正常应用日志，日志以 job_id 关联。

## 停止、数据与故障处理

```sh
docker compose down
```

停止保留 MySQL 与 uploads 数据卷。只有明确要清空当前项目的测试数据时使用 `docker compose down -v`。不要在有待处理任务时把停止/重启当作自动恢复；当前队列无 ACK，参见 [已知边界](../delivery/v1.0-release-notes.md)。

- 端口占用：修改对应 `*_HOST_PORT`，不用改内部服务地址。
- 首次构建失败：检查 Docker、网络和依赖下载日志，再重试启动。
- mysql-init 非零退出：查看其日志，核对库名/用户/密码和数据库就绪情况。
- PENDING 不前进：检查 Redis、队列配置与 Worker 日志；RUNNING 长期不变则按 job_id 核查进程与已提交统计，人工决定后续处理。
- FILE_UNREADABLE：检查 API/Worker 卷是否相同、容器路径和读取权限。

## 测试与重新打包

有本机开发依赖时执行 `sh scripts/check.sh`。完整隔离测试与浏览器/数据库验收步骤见 [第七节报告](../delivery/week-03-tests.md)；默认测试项目独立于本部署。

在源码根目录用 Python 3.13 运行 `python3 scripts/package-release.py`，得到 `output/releases/syncflow-v1.0.0.tar.gz` 与校验文件。脚本包含当前工作区实际代码（包括未提交文件），只选择发布所需路径，不依赖 Git 提交快照；相同输入产生相同 SHA-256。准备演示时按 [演示与复盘初稿](../delivery/week-03-demo-retrospective.md)操作。
