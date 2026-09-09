# Week 1 交付索引

## 十一项任务对应产出

| 任务 | 产出与完成情况 |
| --- | --- |
| 1. 阅读 PRD | 已阅读 product-requirements.pdf；原文提到的 Markdown 源文件未提供，不伪造原始需求 |
| 2. 名词、角色、核心流程 | [需求理解](requirements-understanding.md)、[角色与流程](roles-and-core-flow.md)、[CSV 样例](csv-format.md) |
| 3. 开发环境 | Python/FastAPI 虚拟环境，React/TypeScript/Vite/React Router；依赖锁定文件 |
| 4. MySQL/Redis 与开发库 | Compose 本地五服务，syncflow 开发数据库；实测记录见 review.md |
| 5. 项目骨架、README、启动脚本 | [README](../README.md)、backend/、frontend/、docker/、scripts/start.sh |
| 6. /healthz | 已实现并通过 HTTP 与 Docker 健康检查 |
| 7. 首个分支与 PR | chore/week-01；关联 Issue #1，PR 提交后由 GitHub 展示 |
| 8. 系统边界与核心流程图 | [技术设计](technical-design.md)，包含正常和主要异常分支 |
| 9. API 设计 | [接口说明](api-design.md)、[OpenAPI 3.0.3](openapi.json) |
| 10. MySQL 设计与状态机 | [数据库设计](database-design.md)、[SQL 附件](schema.sql)、[状态机](state-machine.md) |
| 11. 异常与测试计划 | [测试计划与追溯矩阵](test-plan.md) |

额外交付：[Web 页面设计](web-design.md)、[整体系统讲解](system-overview.md)、[AI 自查与验证记录](review.md)、[首页截图](images/week-01-home.png)。

## 交付边界

本周交付设计和骨架，业务接口、CSV Worker、完整迁移、前端业务页面及业务测试套件尚未实现。计划接口已显式标注，运行中的 /docs 只显示现有接口。

待确认规则以 D01–D08 设计提案给出，包含字段、金额、去重、版本范围等；它们不是需求方确认结论。需求确认后应同步修改 API、SQL、页面和测试，不必推翻骨架。

## 需要本人完成的验收

- 阅读并理解代码和配置，亲手按 README 复现运行；目前运行检查由 AI 完成。
- 能口头说明系统边界、异步流程、事务边界与版本范围。
- 对设计提案作人工确认，并完成 PR 最终审核。提交 PR 不等于已合并；本轮不自动合并主分支。
- “新成员 15 分钟启动”尚未经另一位新成员独立复现，不能勾选为已完成。
