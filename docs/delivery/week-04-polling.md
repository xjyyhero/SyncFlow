# Week 4 第五节：React 任务详情页轮询

详情页复用现有 `useRequest`，不增加依赖。首次进入立即加载；PENDING / RUNNING / RETRYING / CANCELING 通过 `setInterval` 每 3 秒刷新状态及统计。上次请求未完成时跳过本轮，避免重叠；完成后在下一定时点继续。

SUCCESS / PARTIAL_SUCCESS / FAILED / CANCELED 清除定时器。`useEffect` cleanup 同时清除定时器并取消请求，覆盖路由离开、任务切换、手动刷新和 StrictMode 重复执行；已取消请求的响应不会覆盖新页面。

刷新失败显示错误并保留已有数据，后续定时点继续重试，也可立即手动重试；首次网络失败同样自动重试。恢复成功后清除错误提示。已知终态的手动刷新失败不会重新开启轮询。

## 验证记录

2026-10-08：真实 Chrome 中运行 React 页面，使用受控 API 响应及浏览器时钟，以下 **7 组验收全部通过**：

1. 四个非终态均以 3 秒间隔更新，状态与统计同步变化。
2. 四个终态停止轮询，首次加载已完成任务也停止。
3. 刷新返回 503 时保留数据，下一轮自动恢复并清除错误。
4. 首次网络失败后自动恢复；终态手动刷新失败不重新轮询。
5. 请求跨越多个定时点时不重叠，完成后继续刷新。
6. StrictMode、反复手动刷新、进出详情页和切换任务均只有一个定时器；离开详情页后没有轮询请求。
7. 离开页面取消未完成请求，旧响应不会覆盖新任务。

`sh scripts/check.sh` 通过：14 项 CSV 测试、6 项前端测试、格式与类型检查、生产构建、Compose 配置和 OpenAPI 一致性。本节浏览器验收使用模拟 API，不替代第六节的完整前后端联合验收。

## 复跑

启动前端：

```sh
npm --prefix frontend run dev -- --host 127.0.0.1 --port 15174
```

在已安装 Playwright、Google Chrome 的环境运行（必要时用 `NODE_PATH` 指向已有 Playwright 模块目录）：

```sh
WEB_ORIGIN=http://127.0.0.1:15174 node scripts/check-week04-polling.cjs
```

脚本：[`scripts/check-week04-polling.cjs`](../../scripts/check-week04-polling.cjs)。机器可读结果及错误提示、终态截图保存于本地 `output/week-04-polling/`；该目录不提交 Git。最终功能演示仍按第七节单独整理并上传。
