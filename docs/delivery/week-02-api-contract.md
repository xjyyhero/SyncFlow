# Week 2 统一响应与参数校验交付记录

验证日期：2026-09-16（Australia/Sydney）。对应清单第二部分。

## 完成内容

- [共享契约模块](../../backend/app/api_contract.py)：成功 data/meta、失败 error.code/message/details；分页、状态、表单名称、CSV 扩展名与实际文件字节数校验。
- [应用入口](../../backend/app/main.py)：注册业务错误、请求校验、框架 HTTP 错误和未知异常处理器；保留健康探针的成功结构。
- [HTTP 契约测试](../../backend/tests/test_api.py)：覆盖五种本周错误码、参数/文件边界、内部异常脱敏、公开字段过滤及 OpenAPI。
- [OpenAPI](../design/openapi.json)：更新为 Week 2 的 201/400/404/500、PENDING 状态和字段命名；从 Python 模型生成，检查脚本自动检测文档漂移。
- [历史 Week 1 契约](../design/openapi-week-01.json)：保留原始提案供追溯。

## 验证结果

- 独立容器中的 7 项 HTTP 契约测试与 6 项 MySQL 集成测试，共 13 项通过，报告采集 259 条断言。
- [本轮可视化报告](../../output/db-review/20260915T203234329574Z/index.html)包含每条断言的预期和实际值。
- 后端静态/格式检查、前端格式/类型/生产构建、Compose 配置校验及 OpenAPI 模型一致性检查全部通过。
- 补齐健康检查响应模型后，7 项 HTTP 测试再次通过。
- 本地 API 已重新构建并启动；真实请求确认 info 返回 data/meta，404/405 返回统一错误，healthz/readyz 返回 200，在线 OpenAPI 版本为 0.2.0。
- 导出契约引用均可解析，创建任务契约为 201/400/500，公开任务字段与 Week 2 一致。

## 范围与后续接入

本节完成共享响应/校验模块及测试。创建、详情和列表三个业务路由尚未挂载；OpenAPI 导出文件明确标记 planned。HTTP 测试专用路由不出现在正式应用中，不能将这些测试解释为完整上传、入库、入队链路已完成。

后续任务路由使用 `SuccessResponse` 及公开 `JobCreated/JobDetail` 模型，列表参数使用 `JobQuery`，上传使用 `validate_upload` 依赖，缺失任务抛出 `APIError("JOB_NOT_FOUND")`。不得直接序列化 repository 内部行。

单独验证本节：

```sh
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -p test_api.py -v
backend/.venv/bin/python scripts/export-openapi.py --check
```
