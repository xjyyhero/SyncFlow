# Week 3 CSV 文件读取与数据契约

对应 [Week 3 清单第一节](week-03-checklist.md)。本节交付本地 CSV 读取、校验与规范化模块，沿用已有上传校验、共享文件卷和数据库字段类型。

## 实现

- [CSV 读取模块](../../backend/app/csv_source.py)：`read_csv(path)` 读取可信本地路径，返回 `CSVData.records`、`CSVData.errors` 和 `total_records`。文件级错误抛出 `CSVValidationError`，不返回部分解析结果；行级错误保留原始字段和定位信息，继续处理下一行。
- 仅接受 UTF-8（允许 BOM）、逗号分隔和严格四列表头；支持标准 CSV 的引号、逗号、换行字段及 CRLF。
- 复用 API 既有 `.csv` 后缀和上传大小校验；本地读取入口也检查后缀和大小。`.CSV` 与既有 API 一致，视为合法后缀。
- `MAX_UPLOAD_FILE_SIZE_MB` 默认 10，按 MiB（1024 × 1024 字节）计算；`MAX_RECORDS_PER_JOB` 默认 10000。两者必须为正整数，配置非法时报错，不静默禁用限制。
- 四字段先去除首尾空白。`external_id` 仅接受 ASCII 字母、数字、下划线及短横线，长度 1–64，大写存储值；`name` 按 Unicode 字符计数，长度 1–128。
- 金额仅接受非负普通十进制，最多两位小数，范围 `0.00`–`9999999999.99`。输出 Python `Decimal`，统一两位小数，不经浮点数或隐式舍入。
- 日期严格为 `YYYY-MM-DD` 且真实存在，输出 Python `date`；范围限定为 MySQL `DATE` 可存储的 `1000-01-01`–`9999-12-31`。
- 既有 `sync_records.amount DECIMAL(12,2)` 和 `record_date DATE` 无需迁移；集成测试将解析出的值写入真实 MySQL，再重新查询确认类型和边界精度。

## 本节采用的边界口径

- 所有非空 CSV 数据记录（包括字段校验失败的记录）计入总数和 10000 行上限；表头及空白物理行不计入。`,,,` 属于必填项缺失的数据行，`""` 属于列数错误的数据行，均不当作空行忽略。
- 同一文件内首个完整通过校验的记录占用规范化 ID；后续同 ID 记录报 `EXTERNAL_ID_DUPLICATE`。先出现的非法记录不阻止后续合法记录；每次读取使用新的去重集合。
- 一行最多返回一个错误，依次检查列数、必填项、标识格式/重复、名称、金额、日期；`total_records = len(records) + len(errors)`。
- `row_number` 为 CSV 记录的起始物理行号，表头是第 1 行，空行仍占物理行号；带引号换行的记录以起始行定位。
- 引号损坏导致无法可靠划分记录时，使用补充文件级错误码 `CSV_MALFORMED`；不把解析损坏伪装成字段错误。
- 先在大小限制内读取、完整解码并校验，再返回结果，避免尾部编码错误或超行数文件暴露部分成功结果。目前默认最多 10 MiB/10000 行，采用有界内存预检；扩大导入规模时再改为流式预检。

第三周正式契约优先于第一周文档中的待确认假设，尤其是 trim、标识大小写和名称长度规则。

## 与 Worker 的衔接

Compose 已让 API 和 Worker 共享 `/app/uploads`，Worker 只读挂载；本机使用 `UPLOAD_DIR`。API 生成服务端存储文件名，Worker 既有路径检查拒绝上传目录以外的路径。

`read_csv` 的路径参数是内部可信路径，不是用户直接传入的文件路径；后续接入 Worker 时应保留其上传目录边界检查，并使用任务的 `stored_file_path`。

第一节交付时仅提供独立解析模块。后续第二节已将其接入真实 Worker，完成错误持久化、合法行写入、统计和 `PARTIAL_SUCCESS`；最新运行行为见 [第二节验收记录](week-03-validation.md)。第三节已实现每 250 行分批提交，见 [分批事务记录](week-03-batches.md)。

## 验证与复跑

无需数据库的 CSV 边界测试：

```sh
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -p test_csv_source.py -v
```

项目统一检查（已加入 CSV 测试）：

```sh
sh scripts/check.sh
```

完整后端隔离测试：

```sh
sh scripts/test-db.sh
```

测试覆盖 BOM、中文、引号逗号/换行、空行、严格表头、全部字段边界、去重、10 MiB/10000 行上下界、配置覆盖、编码和文件读取失败。新增集成场景验证真实 API 保存的 BOM 文件可由独立后端进程读取，以及规范化金额与日期在 MySQL 中往返存储的精度。

## 本次验收结果（2026-09-24）

- CSV 独立测试：14 项全部通过。
- 全部后端测试：51 项全部通过（包含上述 14 项、2 项新增集成测试及既有回归测试），使用隔离 MySQL/Redis；[本地测试报告](../../output/db-tests/test-results.json)。
- `sh scripts/check.sh`：后端静态检查与格式、CSV 测试、3 项前端测试、前端格式/类型检查/生产构建、Compose 配置和 OpenAPI 一致性均通过。
- 常规测试脚本构建镜像时，远程 `python:3.13-slim` 元数据请求超时。本次复用已缓存测试镜像，只读挂载当前后端代码运行全部测试，不依赖旧镜像中的业务代码；测试容器已清理。

本次隔离验证的替代复跑方式（已有测试镜像时）：

```sh
docker compose -f compose.test.yaml up -d --wait mysql-test redis-test
docker compose -f compose.test.yaml run --no-deps --rm -v "$PWD/backend/app:/app/app:ro" db-tests
docker compose -f compose.test.yaml down
```
