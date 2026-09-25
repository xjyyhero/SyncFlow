# 文档导航

| 目录 | 内容 | 阅读入口 |
| --- | --- | --- |
| `sources/` | 原始 PRD、Week 1 任务 PDF，仅保留本地，不上传 GitHub | 本地原始资料 |
| `requirements/` | 项目需求、角色与流程、CSV 字段、系统入门讲解 | [需求理解](requirements/requirements-understanding.md)、[整体系统讲解](requirements/system-overview.md) |
| `design/` | 技术架构、OpenAPI、数据库 SQL、状态机、页面与测试计划 | [技术设计](design/technical-design.md) |
| `delivery/` | 工作清单、交付索引、验证和 AI 自查记录 | [Week 1 交付索引](delivery/week-01-delivery.md)、[Week 1 工作清单](delivery/week-01-checklist.md)、[Week 2 待完成任务](delivery/week-02-checklist.md) |
| `images/` | 文档和 PR 使用的截图 | [Week 1 首页](images/week-01-home.png) |

建议先读需求理解和整体系统讲解，再看技术设计；验收时从交付索引进入。

第二周已合并交付记录：[后端与 Worker PR](delivery/week-02-pr1.md)、[React 页面 PR](delivery/week-02-pr2.md)，包含当时的测试日志和页面截图。

第三周：[Week 3 工作清单](delivery/week-03-checklist.md)、[CSV 文件读取与数据契约](delivery/week-03-csv-contract.md)、[校验与错误记录](delivery/week-03-validation.md)、[分批事务与统计](delivery/week-03-batches.md)、[Redis 与真实 Worker](delivery/week-03-worker.md)、[错误明细查询 API](delivery/week-03-errors-api.md)、[React 页面与状态刷新](delivery/week-03-pages.md)、[测试与验收报告](delivery/week-03-tests.md)。

原始 PDF 的链接只有本地资料存在时可用。CSV 样例仍位于项目根目录的 [samples/sample-valid.csv](../samples/sample-valid.csv)。启动与操作说明见 [项目 README](../README.md)。

V1.0：[本地部署说明](deployment/local-v1.0.md)、[发布包与部署验收](delivery/week-03-release.md)、[Release Notes](delivery/v1.0-release-notes.md)、[演示与复盘初稿](delivery/week-03-demo-retrospective.md)。

第九节：[PR、AI Review 与发布交接](delivery/week-03-handoff.md)。
