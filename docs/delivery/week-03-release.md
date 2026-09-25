# Week 3 部署说明与 V1.0 发布包

对应 [清单第八节](week-03-checklist.md)。运行说明见 [V1.0 本地部署](../deployment/local-v1.0.md)，变更与限制见 [Release Notes](v1.0-release-notes.md)，演示与复盘见 [初稿](week-03-demo-retrospective.md)。

本节生成包含当前源码、锁定依赖、Compose、样例、测试报告、部署说明和 Release Notes 的 `syncflow-v1.0.0.tar.gz`，附外部 SHA-256 与包内文件清单。工作区未提交文件也纳入发布输入；不依赖 Git archive，避免遗漏本周实现。第九节负责 PR、Git 标签和正式发布。

## 本节交付

- Compose 支持五个常驻服务及一次性初始化。宿主端口可配置，API/Worker 上传路径固定一致；Web 增加健康检查，镜像先构建前端再提供页面。
- `.env.example` 说明必要配置、10 MB/10,000 行默认值、宿主与内部端口区别，并明确未来预留项不生效。
- API 文档和版本接口、前端包元数据统一为 1.0.0；README 补齐部署、配置、API 调用、测试与打包入口，并修正本机 Worker 启动目录，避免与 API 默认上传路径不一致。
- 源码包包含 [正常和异常样例](../../samples/README.md)、第七节报告与截图、[本地部署说明](../deployment/local-v1.0.md)、[Release Notes](v1.0-release-notes.md)和[演示/复盘初稿](week-03-demo-retrospective.md)。`.dockerignore` 排除输出目录和本地上传，避免构建上下文夹带验收文件或递归包含发布包。

## 空环境复现（2026-09-25）

从源码包解压到新的临时目录，校验全部 MANIFEST 条目，复制配置示例，仅修改测试库名、Compose 项目名与宿主端口，运行包内 `sh scripts/start.sh`。没有绑定原工作区源码、node_modules 或 Python 虚拟环境。复用 Docker 镜像层缓存；“空环境”指新解压目录、新项目、全新 MySQL/上传卷，不意味着重新安装 Docker 或禁止镜像缓存。

项目为 `syncflow-v1-verify`，数据库 `syncflow_release_test`；宿主端口为 MySQL 13316、Redis 16389、API 18080、Web 15180，内部端口不变。启动前不存在该项目命名数据卷；启动后查询任务数为 **0**。

| 检查 | 实际结果 |
| --- | --- |
| 镜像构建、锁定依赖与前端构建 | API、Worker、mysql-init、Web 均从包内 Dockerfile 构建成功 |
| 数据库首次初始化 | mysql-init 正常退出 0，业务表就绪 |
| 服务就绪 | API、MySQL、Redis、Web 健康，Worker running；Worker 无独立健康探针，实际消费由样例验证 |
| API 与 Web 代理 | 两个入口均返回版本 1.0.0，浏览器刷新深层路径正常 |
| 共享卷 | API 与 Worker 均挂载 `syncflow-v1-verify_uploads` 到 `/app/uploads`，无宿主源码挂载 |
| 权限 | API 成功写入 0600 文件，Worker 可读；Worker 写入被 EROFS（errno 30）拒绝；探针已删除 |
| 浏览器验收 | 使用包内脚本，9 / 9 组通过，真实上传三类固定样例 |
| 数据库对账 | 页面、API、实际记录和错误字段全部一致，3 / 3 通过 |
| 初始化可重复 | 样例完成后再次运行 mysql-init 返回 0，再核对数据仍一致 |
| 本地项目检查 | 格式/静态、14 项 CSV、6 项前端、类型/构建、Compose、OpenAPI 通过；另运行 7 项 HTTP 契约测试通过 |

三类样例结果：

| 文件 | 状态 | 总数 / 成功 / 失败 | 错误 |
| --- | --- | --- | --- |
| sample-valid.csv | SUCCESS | 5 / 5 / 0 | 无 |
| sample-mixed.csv | PARTIAL_SUCCESS | 22 / 1 / 21 | 21 条 AMOUNT_INVALID，行号 3–23 |
| sample-invalid-header.csv | FAILED | 0 / 0 / 0 | 1 条 CSV_HEADER_INVALID，行号 null |

结构化结果保存在 [部署验收快照](week-03-release-results.json)，包含服务状态、挂载权限、浏览器用例和数据库实际值。完整本地日志见 `output/week-03-release/`：[构建](../../output/week-03-release/build.log)、[启动](../../output/week-03-release/start.log)、[项目检查](../../output/week-03-release/check.log)、[HTTP 契约](../../output/week-03-release/api-contract.log)、[浏览器](../../output/week-03-release/browser.log)、[服务日志](../../output/week-03-release/services.log)、[数据库对账](../../output/week-03-release/browser-database.json)。

## 复跑与制品校验

打包及干净部署步骤见 [部署说明](../deployment/local-v1.0.md)。启动验收端口后，在**解压目录**运行（需要已安装 Playwright/Chrome，必要时配置 NODE_PATH）：

```sh
WEB_ORIGIN=http://127.0.0.1:15180 REPORT_DIR=output/week-03-release node scripts/check-week03-pages.cjs
docker compose run --rm mysql-init
docker compose cp samples api:/tmp/samples
docker compose cp output/week-03-release/browser-scenarios.json api:/tmp/browser-scenarios.json
docker compose exec -T api python - /tmp /tmp/samples < backend/tests/verify_browser.py
docker compose cp api:/tmp/browser-database.json output/week-03-release/browser-database.json
```

数据库核对脚本仅允许测试环境 `mysql-test/syncflow_test` 或 `mysql/syncflow_release_test`。本次未在开发库执行验收写入。共享权限验证额外使用临时探针，并核对 Docker 挂载的 RW 标记；无需更改正式上传文件。

最终源码包在验收后纳入本记录、结果快照和完成清单；应用源码、Docker/Compose、配置示例、锁文件、样例及部署/验收脚本与实际部署验证的内容逐字节一致。打包脚本另补充排除 macOS `.DS_Store`。连续打包产生相同 SHA-256，最终包所有清单哈希均通过，且未包含 `.env`、上传文件、依赖缓存、`.DS_Store` 或 Git 元数据。

输出：`output/releases/syncflow-v1.0.0.tar.gz`、`output/releases/syncflow-v1.0.0.tar.gz.sha256`。发布包属于本地交付制品；Git 标签与远程发布未执行，留待第九节。

验收结束后已清理 `syncflow-v1-verify` 的容器、网络和测试数据卷，并确认无残留容器或命名卷；开发项目未改动。
