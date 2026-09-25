# Week 3 验收样例

使用页面 `/jobs/new` 上传以下 UTF-8 CSV。机器可读预期见 [week-03-expected.json](week-03-expected.json)，浏览器验收脚本直接读取这些文件，随后与 MySQL 核对。

| 文件 | 预期状态 | 总数 / 成功 / 失败 | 错误 |
| --- | --- | --- | --- |
| [sample-valid.csv](sample-valid.csv) | `SUCCESS` | 5 / 5 / 0 | 无；含中文、整数金额、零金额及引号包裹的逗号名称 |
| [sample-mixed.csv](sample-mixed.csv) | `PARTIAL_SUCCESS` | 22 / 1 / 21 | 第 3–23 行 `AMOUNT_INVALID`；21 条错误可验证默认两页分页 |
| [sample-invalid-header.csv](sample-invalid-header.csv) | `FAILED` | 0 / 0 / 0 | 一条 `CSV_HEADER_INVALID`，行号为空 |

混合样例首行标识 ` s1 `、名称 ` 商品 ` 去空白后保存为 `S1`、`商品`，金额 `19.9` 保存为 `19.90`。正常样例的 `19`、`1299.5` 分别保存为 `19.00`、`1299.50`。

文件级错误不计入行失败数，因此非法表头样例的失败数为 0、错误记录数为 1。页面仍提供错误明细入口。行号包含表头，指向源 CSV 的物理起始行。

日期、长度、编码、10 MB 和 10,000 行等边界输入由 `backend/tests/test_csv_source.py` 在临时目录生成；无需把大文件加入仓库。系统写入故障由隔离数据库中的约束触发，不依赖一份“特殊 CSV”在开发库制造故障。
