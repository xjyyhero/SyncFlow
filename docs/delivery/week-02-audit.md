# Week 2 完成情况复审

复审日期：2026-09-18。依据：docs/sources/week-02.pdf 全文 6 页、当前工作区代码、已有验收证据及 GitHub PR 列表。

## 结论

核心功能与本地验收已完成；整周正式交付尚未完成。不能把第 1–7 部分完成等同于 PDF 全部完成。

## 未完成项

1. **本周至少两个可独立审阅与合并的 PR 及 Review。** PDF 第 6 页明确要求。远程只查到已合并的 #2「Week 1：需求设计与可运行项目骨架」及 #3「整理 Week 1 文档分类与导航」。当前 Week 2 实现仍为工作区修改及未跟踪文件，没有可对应的 Week 2 PR 和 Review。建议拆分后端/Worker 与前端/验收两个 PR，并各自保存审阅结论。
2. **验收证据尚未随仓库交付。** .gitignore 第 18 行忽略整个 output/，本地验收文档链接的测试报告、截图和日志虽然存在，但他人仅克隆仓库无法看到本次证据。应归档脱敏的精选日志与截图至可跟踪目录，或在 PR 附件中提供；不要将真实数据库快照直接整体纳入版本控制。

本次是复审，没有创建 PR、提交代码、修改业务实现或代替用户进行外部 Review。

## 要求核对

| PDF 要求 | 本地状态 | 对应证据 |
| --- | --- | --- |
| 三张表、字段类型、索引、UTC、可重复初始化 | 已实现且有测试 | schema.sql、database.py、test_database.py |
| 创建接口、上传校验、安全路径、SHA-256、队列错误记录 | 已实现且有测试 | jobs.py、api_contract.py、test_create_job.py |
| 详情与列表、分页筛选、排序、字段脱敏 | 已实现且有测试 | repository.py、main.py、test_query_jobs.py |
| 最小 Worker 和独立 Compose 服务 | 已实现且有端到端证据 | worker.py、compose.yaml、test_worker.py、e2e.json |
| React 列表/详情、URL 同步、状态反馈、时间格式、404 | 已实现且有浏览器实测记录 | frontend/src、week-02-react.md、week-02-acceptance.md |
| 前端类型检查及生产构建 | 本次复跑通过 | scripts/check.sh |
| 正常、异常与独立 MySQL 测试 | 已有通过证据 | 35 项后端测试 / 473 条断言；3 项前端测试 |
| 两个 PR 及其 Review | 未完成 | 当前远程只有 Week 1 PR |
| 能解释路由/业务/数据访问职责 | 代码分层可说明；个人讲解需自行准备 | 下方说明 |

## 本次核验方式与限制

- 提取 PDF 全文，另对第 4 页 Worker 范围与第 6 页交付要求进行页面图像核对。
- 阅读当前 schema、连接、路由、业务、数据访问、Worker 及前端客户端；结合上轮已核查的页面实现与浏览器证据。
- 重新运行 `sh scripts/check.sh`：后端静态检查、前端 3 项测试、TypeScript、生产构建、Compose 配置和 OpenAPI 一致性均通过。
- 核查上次完整后端测试的结构化结果：35 项、473 条断言全部通过。本次未重复运行后端数据库测试，也未新增验收任务。
- 核查本地交付文档链接均存在；查验 git status、git log、git check-ignore 与 `gh pr list --state all`。
- 本次未发现另一个有充分证据的 Week 2 核心功能阻断项；结论不代表穷尽所有生产环境异常。

## 统计为 0 是否遗漏

不是。PDF 第 4–5 页明确允许 Worker 直接更新 SUCCESS 模拟完成，真正 CSV 解析和 MySQL 写入留到第 3 周。当前统计默认 0 符合本周范围。BLPOP 的崩溃恢复与自动重试也尚未实现，已在验收记录说明，不能宣称具备生产级可靠处理能力。

## 分层职责与异步流程

- 路由层 main.py：定义 URL、HTTP 状态、输入依赖、公开响应模型和 OpenAPI。
- 校验及响应 api_contract.py：统一参数规则、错误结构和输出字段。
- 业务层 jobs.py：保存文件、计算摘要、组织数据库事务、投递消息及处理失败。
- 数据访问层 repository.py：参数化 SQL、分页查询、条件状态更新；database.py 管理连接与事务。
- Worker：从 Redis 获取任务 ID，提交 RUNNING，检查共享文件可读，再模拟提交 SUCCESS。
- POST 返回 201 不等待 Worker；前端随后 GET 查询数据库中保存的任务状态。

## 完成剩余交付的判定

Week 2 代码进入至少两个可独立审阅的 PR；每个 PR 有真实 Review 记录；他人能获得本次脱敏验收证据并按文档复跑。满足后再将第八部分标为正式完成。
