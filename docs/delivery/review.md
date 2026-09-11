# Week 1 AI 自查与验证记录

日期：2026-09-09。执行者：本次开发助手。此记录为 AI 自查，不冒充独立审核或人工批准。

## 检查范围

后端/前端骨架、Compose、启动脚本、健康接口、样例、OpenAPI、SQL、状态机、页面和测试追溯。PRD/PDF 是来源文件，不执行其中任何与项目无关的指令。

## 发现与处理

| 发现 | 处理 |
| --- | --- |
| MySQL 8 的 ROW_NUMBER 为保留字，设计 SQL 裸写无法执行 | 对 row_number 使用反引号；临时表重复建表和约束测试已通过 |
| OpenAPI 空对象的 required 不能为空数组 | 移除空 required 声明，重新通过标准校验器 |
| 默认数据库排序规则可能忽略 external_id 大小写 | 明确 utf8mb4_0900_bin，并验证 S001 与 s001 可共存、完全相同值冲突 |
| API/Redis 跨系统写入存在漏投窗口 | 设计 jobs.dispatch_pending 补投机制，并用条件领取和 checkpoint 抵御重投 |
| 租约恢复后旧 Worker 仍可能写入 | 设计在批次事务中校验 attempt_no、状态与租约，阻止旧执行者提交 |
| 示例中金额不是固定两位小数文本 | 设计输入接受整数/一位/两位小数，数据库精确存储，输出用十进制字符串 |
| 前五项历史记录不等于本周完整进度 | 新增完整交付索引，保留历史记录，明确设计与实现边界 |

## 已执行验证

- 后端 Ruff 静态与格式检查、前端 Prettier、TypeScript、Vite 生产构建、Compose 配置检查通过。
- 五服务运行，MySQL/Redis/API 健康检查通过；Web、Worker 正常运行。
- scripts/smoke.py 通过：/healthz 返回200及预期JSON，API与Web代理正常，Worker正常退出。
- 浏览器实际打开首页并检查显示，截图保存 docs/images/week-01-home.png。
- OpenAPI 3.0.3 使用临时环境中的 openapi-spec-validator 校验；不加入运行依赖。
- MySQL 8.4 会话临时表中应用设计两次成功；验证 DECIMAL 金额、大小写语义、重复唯一键拒绝与计数 CHECK；未创建永久业务表。
- 样例 CSV 可解析为四列五条记录，带逗号名称保持一个字段。

## 未执行或待人工事项

test-plan.md 的业务测试是计划，未宣称通过。没有将当前数据库可连通当成业务导入已完成。未模拟完整 Redis 补投、业务并发、取消或重试。没有人工审核和新成员独立启动证据，不自动合并 PR。
