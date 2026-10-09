# Week 4 异常路径日志摘录

以下摘自 2026-10-08 本次隔离验收的实际日志及断言记录，均为主动故障注入产生；完整日志在 `output/week-04-tests/`。任务 ID 可与结果快照及完整报告交叉检索。

```text
2026-10-08 10:51:18,795 ERROR app.jobs job_id=f47b7d76-23ff-4a03-8403-e4ebcee96578 queue dispatch failed
2026-10-08 10:51:18,801 ERROR app.jobs job_id=f47b7d76-23ff-4a03-8403-e4ebcee96578 failed to record dispatch failure; pending marker retained
2026-10-08 10:51:34,695 ERROR app.worker job_id=a1a1a2ce-3529-4ef5-b068-7e80010d105d task supervision failed
2026-10-08 10:51:45,635 ERROR app.worker Stopped with unconfirmed task states; recovery required
2026-10-08 10:51:46,654 ERROR app.worker job_id=3566ab14-d054-4c5f-b96b-f762f5ea11b6 worker operation failed
ERROR:app.worker:job_id=0cbf9067-0685-4972-b107-fe1041052a6f final state persistence failed; recovery required
2026-10-08 10:51:30,776 ERROR worker job_id=8ffe62c1-0fa1-4d25-850e-1f936dd51cb7 status=FAILED stage=csv_validation failure_recorded=True error_code=CSV_HEADER_INVALID
2026-10-08 10:51:31,227 WARNING worker job_id=5389ac17-6f02-4bb5-9241-5efe903ad1ac stage=batch_write batch=1 write_error=3819 persisted_progress=2
```

最终状态回写失败测试确认：记录 `recovery required`、服务退出码为 1、数据库暂留 RUNNING，恢复扫描后进入 FAILED。队列失败及标记回写失败保留待投递标记；Worker 内部异常和数据库批次失败均记录原因，故障后的普通任务仍可完成。

并发、超时、取消与停止恢复的具体断言见本地 `backend-results.json`；各项结果与说明见[验收结果快照](week-04-test-results.json)及[第六节报告](week-04-tests.md)。
