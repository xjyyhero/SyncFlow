# Web 页面设计（计划实现）

当前只有首页占位。以下页面为后续实现设计，不新增未实现业务按钮冒充功能。

| 路由 | 页面与主要交互 | API | 加载 / 空数据 / 错误 |
| --- | --- | --- | --- |
| /jobs | 任务列表；分页、状态筛选，点击进入详情 | GET /api/v1/jobs | 骨架或加载文字 / 引导创建 / 错误消息和重试按钮 |
| /jobs/new | 文件选择；显示名称与大小；提交后跳到详情；提交中禁用重复点击 | POST /api/v1/jobs | 提交中提示 / 未选文件禁用提交 / 展示文件级错误并保留选择 |
| /jobs/:jobId | 状态、总数/成功/失败/进度，链接错误明细 | GET /api/v1/jobs/{job_id} | 加载 / 404 任务不存在 / 请求失败可重试 |
| /jobs/:jobId/errors | 行号、错误码、可读原因，分页 | GET /api/v1/jobs/{job_id}/errors | 加载 / 无错误记录 / 错误提示与重试 |

根路由未来跳转 /jobs，未知路由展示 404。当前 React Router 已提供首页及 404。

任务详情在 QUEUED/RUNNING/RETRY_WAIT 时每 2 秒查询，卸载或终态时停止；请求串行，禁止慢响应覆盖新筛选结果。列表筛选改变后回到第 1 页。页码与状态同步 URL 查询参数。

状态文案：QUEUED 等待中、RUNNING 执行中、SUCCESS 成功、PARTIAL_SUCCESS 部分成功、FAILED 失败、RETRY_WAIT 等待重试、CANCELLED 已取消。错误与状态不能只靠颜色识别。

前端不计算金额，金额按 API 十进制字符串显示；record_date 原样显示，不做时区转换。created_at 等 UTC 时间转本地时区。进度为 processed_count/total_count，FAILED/CANCELLED 可能不足 100%。

V1.1 详情增加取消请求、执行尝试分页和成功/错误 CSV 导出；仅允许适用状态的操作，服务端仍验证。导出前展示结果类型，不显示服务器路径。无登录页和权限菜单。

结构约定：src/pages 页面、src/components 可复用展示、src/api HTTP 客户端、src/types 响应与领域类型；有实际功能时再创建目录。前端验证仅提升体验，后端是最终校验边界。错误用可读 message 展示，必要时显示稳定 code，避免直接渲染 HTML。
