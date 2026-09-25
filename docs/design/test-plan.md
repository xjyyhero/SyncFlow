# 测试计划与验收范围（Week 3）

本文件描述当前实现的测试口径；实际命令、结果、样例统计及截图见 [第七节测试报告](../delivery/week-03-tests.md)。第一周提案中的 202/422、QUEUED、租约恢复等不作为当前版本的预期。

| 范围 | 当前预期 | 自动验证 |
| --- | --- | --- |
| CSV 契约 | UTF-8/BOM、固定表头与逗号分隔、空行忽略、字符串 trim、行号包含表头及空行 | `test_csv_source.py` |
| 字段边界 | 标识 1–64、名称 1–128、ASCII 标识大小写不敏感去重、金额 DECIMAL(12,2)、严格有效日期 | `test_csv_source.py`、`test_database.py` |
| 文件边界 | 10 MB、10,000 条非空数据记录允许；非法数据行也计入行数上限 | `test_api.py`、`test_csv_source.py` |
| 创建接口 | 上传校验通过后保存 PENDING、投递 ID、返回 201，不等待 Worker；扩展名/大小错误返回 400 | `test_create_job.py`、`test_worker.py` |
| 文件级错误 | 空文件、仅表头、编码、表头等交由 Worker 记录 FAILED 和空行号错误 | `test_worker.py` |
| 行级错误 | 逐行记录具体错误码，继续处理后续行；同任务只保留首个合法的规范化标识 | `test_csv_source.py`、`test_worker.py` |
| 事务与计数 | 每 250 条非空记录一批；失败只回滚本批；前后成功批保留，计数与实际提交相符 | `test_worker.py` |
| 终态 | 全成功 SUCCESS、混合 PARTIAL_SUCCESS、全失败 FAILED；最后批次统计与终态一起提交 | `test_worker.py` |
| Redis/Worker | 真实队列与独立进程；开始/结束时间、进度日志；重复消息跳过；投递失败记录 FAILED | `test_create_job.py`、`test_worker.py` |
| 查询接口 | 不存在返回 404 JOB_NOT_FOUND；分页非法返回 400；默认 1/20、最大 100、稳定排序 | `test_query_jobs.py` |
| 安全与错误 | 参数化 SQL、受控上传路径、统一脱敏内部异常、取消旧页面请求 | 后端集成与浏览器验收 |
| React 上传 | 预检文件后缀/大小、名称上限、文件信息、提交禁用、201 跳转、失败保留输入 | `api.test.ts`、`check-week03-pages.cjs` |
| React 查询 | 列表筛选分页、错误页所有反馈状态、详情 3 秒刷新及终态/卸载清理 | `check-week03-pages.cjs` |
| 样例闭环 | 固定三类样例由 React 上传；页面、API、MySQL 与预期一致 | `check-week03-pages.cjs`、`verify_browser.py` |
| 测试报告 | 子用例失败/异常必须令父用例报告标记失败/异常 | `test_reporting.py` |

纯 CSV/参数测试不依赖数据库。集成测试使用独立 MySQL 8.4、Redis 和临时文件，不用 SQLite 代替金额类型、排序、锁和约束。浏览器使用已有 Playwright/Chrome，无新增前端测试框架。故障与精确轮询时序使用受控响应；三类上传闭环使用真实后端，报告区分两者。

本节不声称已验证或实现 V1.1 幂等、取消、自动重试、租约恢复、导出、跨多次请求冻结快照或性能 SLA。当前 BLPOP 消费在进程崩溃时仍需人工恢复；部署复现、发布包和 PR 验收分别属于第八、九节。
