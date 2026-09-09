# 异常场景与测试计划

以下业务用例是计划，尚未实现业务测试。当前已运行的检查由 review.md 记录。设计采用 D01–D08 提案，确认变更时同步修改测试。

| ID | 场景与预期 | 层级 | 接口 / 数据 |
| --- | --- | --- | --- |
| T01 | samples/sample-valid.csv 五行全部成功；金额准确为 19.90、29.50、19.00、0.00、1299.50；含逗号名称保持完整 | 单元 + MySQL 集成 | POST jobs；records、jobs；SUCCESS |
| T02 | 0 字节、仅表头、仅空白记录：422 EMPTY_FILE，不创建任务 | API 集成 | POST jobs |
| T03 | 错表头、少列、多列、损坏引号、非 UTF-8：422 文件级错误，清理临时文件 | 单元 + API 集成 | POST jobs |
| T04 | name/external_id 长度边界、空值、空格、大小写按 D02 执行 | 单元 | records 校验 |
| T05 | 0/19/1299.5 合法；负数、三位小数、NaN、指数、超最大金额拒绝，不隐式舍入 | 单元 + MySQL 集成 | amount DECIMAL |
| T06 | 闰年日期有效；2026-02-29 无效；范围端点与 YYYY-MM-DD 格式 | 单元 | record_date |
| T07 | 同任务重复 external_id 只保留第一条合法记录，后条记 DUPLICATE_EXTERNAL_ID；跨任务可重复 | MySQL 并发集成 | uq_records_external、PARTIAL_SUCCESS |
| T08 | 全行字段失败：FAILED；部分失败：PARTIAL_SUCCESS；全部成功：SUCCESS；计数一致 | API + MySQL 集成 | 状态机、统计 |
| T09 | 10 MB 和10000行边界允许；超过一字节或一条非空记录拒绝；带引号多行按逻辑记录计数 | 单元 + API 集成 | FILE_TOO_LARGE、TOO_MANY_RECORDS |
| T10 | 创建后 Redis 不可用仍有可查询 QUEUED 任务，恢复后补投；重复投递只领取一次 | 故障集成 | dispatch_pending、attempt_count |
| T11 | 写一批时数据库失败：整批回滚，checkpoint/计数不前移；恢复不重复写和计数 | MySQL 集成 | 事务边界 |
| T12 | 终态提交后 ACK 前崩溃：重投只 ACK；租约过期恢复后旧 Worker 不能提交 | 故障并发集成 | attempt_no、lease_expires_at |
| T13 | 不存在任务404；page=0/page_size>100/非法状态422；分页顺序稳定 | API 集成 | JOB_NOT_FOUND、VALIDATION_ERROR |
| T14 | 请求文件名 ../../x、异常信息含 SQL：受控文件安全，响应和日志不泄漏内部数据 | 安全/API 集成 | storage_key、统一错误 |
| T15 | V1.1 同幂等键相同摘要返回原任务200，不同摘要409；并发与 TTL 边界 | MySQL/API 集成 | idempotency_key |
| T16 | V1.1 重试最多三次，退避5/30秒；取消等待任务立即终态，运行任务批次边界取消，完成竞态受锁保护 | 状态机单元 + 集成 | RETRY_WAIT、CANCELLED |
| T17 | 创建页未选文件不能提交，重复点击禁用，202跳详情，422保留文件并显示错误 | 前端交互 | /jobs/new |
| T18 | 列表加载、空数据、失败重试，状态筛选重置页码，慢响应不覆盖新筛选 | 前端交互 | /jobs |
| T19 | 详情非终态轮询，终态/卸载停止，404友好显示，错误页分页 | 前端交互 | /jobs/:jobId、errors |
| T20 | 1000行不崩溃；10000行流式或分批处理，记录峰值内存与耗时，避免无界内存 | 性能集成 | API 预检、Worker |
| T21 | SIGTERM 停止领取，30秒内退出，未完成批次回滚，恢复不丢已提交结果 | 故障集成 | Worker |
| T22 | /healthz 返回200且不依赖数据库查询；Web到API代理正常 | 当前骨架冒烟 | scripts/smoke.py |
| T23 | V1.1 非终态导出409，终态导出UTF-8 CSV；正确转义逗号、引号和换行，防电子表格公式注入 | API 集成 | export |

## 需求追溯

| 核心需求 | 接口 | 数据结构 | 页面 | 测试 |
| --- | --- | --- | --- | --- |
| 创建异步任务 | POST /jobs | jobs、dispatch_pending | /jobs/new | T01–03、T09–10、T17 |
| 查询任务与进度 | GET /jobs、/jobs/{id} | jobs counters/status | 列表、详情 | T08、T13、T18–19 |
| 合法记录与行错误 | GET /jobs/{id}/errors | records、job_errors | 错误页 | T04–08、T11 |
| 任务可靠性 | 内部 Worker + GET详情 | attempts、lease、checkpoint | 详情 | T10–12、T21 |
| 幂等、重试与取消 V1.1 | POST jobs header、POST cancel、GET attempts | jobs、job_attempts | 详情 | T15–16 |
| 结果导出 V1.1 | GET export | records、job_errors | 详情 | T23 |
| 本地可运行与安全 | /healthz、统一错误 | Compose、受控文件卷 | 首页 | T14、T20、T22 |

后端纯规则测试不依赖数据库；集成测试必须使用真实 MySQL 8，不能用 SQLite 代替精度、排序规则、锁与约束验证。前端计划用 Vitest/Testing Library 模拟接口；业务实现时再安装，Week 1 不添加空测试套件。
