# Week 3 文件级校验、行级校验与错误记录

对应 [Week 3 清单第二节](week-03-checklist.md)。CSV 校验已接入真实 Worker，错误写入 `sync_errors`，合法记录写入 `sync_records`，任务不再模拟成功。

本文件保留第二节验收时的整文件事务行为；第三节已升级为分批事务，当前规则见 [第三节交付记录](week-03-batches.md)。

## 处理行为

- API 仍只检查上传请求、后缀和大小，保存文件并入队后立即返回。CSV 内容校验由独立 Worker 执行。
- Worker 先提交 `PENDING → RUNNING` 和首次开始时间，再读取受控上传目录内的文件；目录越界与文件不存在均返回安全的 `FILE_UNREADABLE`，不泄露内部路径。
- 完整文件预检失败时，不写入部分业务记录或此前暂存的行错误；在同一个事务中将任务标为 `FAILED`，写入一条文件级错误及结束时间。`row_number`、`field_name`、`raw_row` 均为 SQL NULL。
- 文件预检通过后，非法行每行记录一个错误，保留 `job_id`、起始物理行号、字段名、稳定错误码、可读原因、原始字段数组及数据库生成的 `created_at`；后续合法行继续处理。原始字段保留规范化之前的空白和内容。
- 行号、去重和记录计数沿用 [第一节契约](week-03-csv-contract.md)：表头为第 1 行，空行仍占物理行号；首条完整合法记录占用规范化 ID；失败数按行计数。
- 合法记录、行错误、任务统计和终态目前按文件在一个事务中提交。全部合法为 `SUCCESS`，合法与非法行混合为 `PARTIAL_SUCCESS`，全部行不合法为 `FAILED`。全行失败不额外伪造文件级错误。
- 最近错误摘要采用最后一条行错误；成功任务清空旧错误摘要。重复投递或已进入终态的任务不能再次认领，不重复写入记录。
- 若写入行错误或业务记录时数据库报错，回滚整个文件的写入，再通过新事务记录脱敏的 `WORKER_PROCESSING_FAILED`。若连错误记录也无法写入，失败状态更新一起回滚，任务保留已提交的 `RUNNING`；当前队列仍不具备自动恢复，需人工核查。

## 稳定错误码

| 层级 | 错误码 | 字段 | 含义 |
| --- | --- | --- | --- |
| 文件 | `FILE_UNREADABLE` | NULL | 文件不存在、不可读取或目录越界 |
| 文件 | `FILE_ENCODING_INVALID` | NULL | 非 UTF-8 或编码损坏 |
| 文件 | `FILE_EMPTY` | NULL | 空文件或只有表头及空行 |
| 文件 | `CSV_HEADER_INVALID` | NULL | 表头缺失、重复、错序、缺列或多列 |
| 文件 | `FILE_TOO_MANY_ROWS` | NULL | 非空数据记录超过上限，包含非法记录 |
| 行 | `ROW_COLUMN_COUNT_MISMATCH` | NULL | 列数不正确 |
| 行 | `FIELD_REQUIRED` | 对应字段 | 必填字段去空白后为空 |
| 行 | `EXTERNAL_ID_INVALID` | `external_id` | 标识格式或长度非法 |
| 行 | `EXTERNAL_ID_DUPLICATE` | `external_id` | 规范化后标识重复 |
| 行 | `NAME_TOO_LONG` | `name` | 名称超过 128 字符 |
| 行 | `AMOUNT_INVALID` | `amount` | 金额格式、精度、非负约束或范围不满足 |
| 行 | `DATE_INVALID` | `record_date` | 日期格式、有效性或存储范围不满足 |

补充文件级错误：`CSV_MALFORMED` 表示引号/CSV 结构损坏，`INVALID_FILE_EXTENSION` 和 `FILE_TOO_LARGE` 用于读取入口的二次防护。系统错误使用 `WORKER_PROCESSING_FAILED`，不替换已经识别出的业务错误码。

错误码和消息由 CSV 模块生成后直接存储，任务详情的最近错误直接返回数据库值，前端直接显示，不另行重命名。后续错误 API 和导出也应使用同一存储值，本节未增加导出功能或错误明细 API。

## 状态升级与第三节边界

支持 `PARTIAL_SUCCESS` 是实际处理混合数据的必要配套：已更新数据库约束、后端模型/筛选、OpenAPI、前端中文标签与颜色。初始化入口会检查并升级旧的四状态约束，保留已有任务；已验证连续执行两次结果一致。新库直接使用五状态约束。

已有环境先运行数据库初始化，再重新构建并启动 API、Worker、Web：

```sh
docker compose run --build --rm mysql-init
docker compose up -d --build api worker web
```

本次只在隔离测试环境执行升级，未修改开发数据库。第三节的 **100–500 行分批事务、单批系统失败后继续其他批次、运行期间的统计更新** 尚未实现；当前按文件原子提交，第三节仍保持待验收。

## 验收（2026-09-24）

- 全部后端 **57 项测试通过**，含原有 51 项及新增 6 项测试；[原始测试报告](../../output/db-tests/test-results.json)。
- 文件级错误：验证编码、空文件/仅表头、各类错误表头、行数上限、损坏引号；原有文件缺失/越界测试已更新为 `FILE_UNREADABLE`。
- 行级错误：同一个真实上传文件覆盖全部 7 个错误码，检查数据库中的全部错误字段，确认前后合法行入库，统计为 `9 / 2 / 7`、状态为 `PARTIAL_SUCCESS`，详情和状态筛选正常。
- 全行非法：统计 `2 / 0 / 2`、状态 `FAILED`，保留两条行错误。
- 事务故障：在写入行错误后注入数据库故障，确认业务数据和已写错误一起回滚；文件错误记录失败时，状态更新也回滚。
- 独立 Worker 进程：通过真实 Redis 连续消费合法文件、错误文件、混合文件及重复/无效消息，后续任务正常处理，SIGTERM 正常退出。
- 迁移：模拟旧四状态约束，保留原有任务并连续初始化两次，验证部分成功可保存且未知状态仍被拒绝。
- `sh scripts/check.sh` 通过：后端静态/格式检查、14 项 CSV 测试、3 项前端测试、前端格式/类型/构建、Compose 配置、OpenAPI 一致性。

完整后端复跑：`sh scripts/test-db.sh`。本次为避免重新下载基础镜像，复用本机已缓存测试镜像并只读挂载当前业务代码：

```sh
docker compose -f compose.test.yaml up -d --wait mysql-test redis-test
docker compose -f compose.test.yaml run --no-deps --rm -v "$PWD/backend/app:/app/app:ro" db-tests
docker compose -f compose.test.yaml down
```

测试仅访问独立的 `syncflow_test` 数据库、测试 Redis 和临时上传目录，结束后清理测试容器。
