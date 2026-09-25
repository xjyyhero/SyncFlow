# Week 3 React 页面与状态刷新

对应 [Week 3 清单第六节](week-03-checklist.md)。复用现有 API 客户端、请求取消、状态展示和分页逻辑，完成上传校验、错误明细页面和详情自动刷新；无新增项目依赖。

## 页面行为

- `/jobs/new`：文件选择限制 `.csv`，展示文件名、MB 和精确字节数。扩展名大小写不敏感，超过 `10 × 1024 × 1024` 字节立即提示并禁止提交，等于上限允许。名称选填、最多 128 字符。提交期间禁用表单和按钮，并防止重复提交；服务失败保留名称和所选文件，展示后端消息；成功跳转到真实任务详情。后端继续独立校验。前端使用本节规定的默认 10 MB 限制；若服务端配置更小，其拒绝信息仍会显示。
- `/jobs/:jobId`：非终态自动获取最新状态、统计。每次成功请求完成后等待 3 秒再发下一次，避免慢请求重叠；自动刷新保留当前详情，避免周期性闪烁。`SUCCESS`、`PARTIAL_SUCCESS`、`FAILED` 停止轮询。切换任务、离开页面或手动重试时清理定时器并取消旧请求；已取消请求的结果不更新界面。请求失败展示重试入口，点击后恢复查询。
- 失败数大于 0、状态为 `FAILED` 或有最近错误码时显示“查看错误明细”。文件级失败即使失败行数为 0，仍能进入错误页。
- `/jobs/:jobId/errors`：调用真实错误 API，显示行号、字段、错误码、消息、原始行摘要和记录时间。文件级空行号显示 `-`；原始 JSON 可展开查看，内容按文本渲染。分别处理加载、无错误、超出末页、请求失败和重试。
- 列表与错误页共享分页组件，分页保留 URL 参数，并提供第一页入口。列表筛选仍回到第一页；列表、详情使用相同“部分成功”文案和颜色。错误页可返回详情或列表，列表可进入创建页。窄屏表格在容器内横向滚动。

## 验证结果（2026-09-25）

`sh scripts/check.sh` 通过：6 项前端测试、14 项 CSV 测试、前后端格式和静态检查、TypeScript 类型检查、前端生产构建、Compose 配置及 OpenAPI 一致性。

浏览器使用独立 Chrome、独立前端端口与隔离 MySQL/Redis/API/Worker，九组验收通过：

| 验收 | 验证内容 |
| --- | --- |
| 上传校验 | 后缀、超限文件、大小显示、名称上限、提交按钮状态 |
| 提交失败 | 请求挂起时禁用按钮、重复提交仅一次请求、失败保留文件和名称 |
| 轮询时序 | 浏览器受控时钟验证 2999 ms 不请求、3000 ms 刷新状态和统计；三个终态各等待 12 秒不再请求 |
| 路由清理 | 客户端切换任务不再查询旧 ID；离开详情后推进时钟仍无详情请求 |
| 正常 CSV | React 上传、真实 Worker 处理成功、错误页显示暂无错误 |
| 混合 CSV | 22 条记录中 1 条成功、21 条失败；页面部分成功，错误页两页分别 20/1 条，重载保留页码 |
| 文件错误 | 非法表头失败，失败行数为 0 仍有入口；错误页行号显示 `-` 和 `CSV_HEADER_INVALID` |
| 错误页反馈 | 加载、服务失败、重试恢复真实 API、超出末页、任务不存在 |
| 页面回归 | 列表分页、筛选回第一页、详情跳转、统一部分成功状态、390 px 窄屏导航 |

故障和轮询时序场景使用受控 HTTP 响应；正常/混合/文件错误三类流程使用真实后端和独立 Worker。截图已检查，浏览器无未捕获页面异常。

验收脚本：[check-week03-pages.cjs](../../scripts/check-week03-pages.cjs)。证据保存在本地输出目录（不入库）：[浏览器报告](../../output/week-03-pages/browser-results.json)、[浏览器日志](../../output/week-03-pages/browser.log)、[项目检查日志](../../output/week-03-pages/check.log)、[详情截图](../../output/week-03-pages/detail.png)、[错误页截图](../../output/week-03-pages/errors.png)、[文件错误截图](../../output/week-03-pages/file-error.png)、[窄屏截图](../../output/week-03-pages/mobile-errors.png)。

## 复跑浏览器验收

需要已安装 Playwright 和 Chrome；`NODE_PATH` 可指向已有 Playwright 的模块目录。脚本会创建三条验收任务，应使用独立测试后端。首次没有缓存测试镜像时，先运行 `docker compose -f compose.test.yaml build db-tests`。

```sh
docker compose -f compose.test.yaml up -d --wait mysql-test redis-test
docker compose -f compose.test.yaml run -d --no-deps --name syncflow-week03-pages-api \
  -p 127.0.0.1:18000:8000 -e UPLOAD_DIR=/tmp/week03-uploads \
  -v "$PWD/backend/app:/app/app:ro" db-tests \
  sh -c 'python -m app.database && exec uvicorn app.main:app --host 0.0.0.0 --port 8000'
```

等待 `http://127.0.0.1:18000/healthz` 就绪后启动 Worker，并在另一个终端保持前端运行：

```sh
docker exec -d syncflow-week03-pages-api python -m app.worker
API_PROXY_TARGET=http://127.0.0.1:18000 npm --prefix frontend run dev -- --host 127.0.0.1 --port 15173
```

在项目根目录运行验收：

```sh
WEB_ORIGIN=http://127.0.0.1:15173 node scripts/check-week03-pages.cjs
```

完成后停止验收用前端，并清理隔离容器：

```sh
docker rm -f syncflow-week03-pages-api
docker compose -f compose.test.yaml down
```

本次独立测试服务已清理，开发环境未重启。
