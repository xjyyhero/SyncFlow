"""Run isolated tests and export a read-only snapshot of the real project MySQL."""

import csv
import html
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Executed inside the existing API container using its configured MySQL connection.
SNAPSHOT = """
import json
from datetime import datetime, timezone
from app.database import connection
with connection() as db, db.cursor(dictionary=True) as cursor:
    db.start_transaction(consistent_snapshot=True, readonly=True)
    cursor.execute("SELECT DATABASE() AS name, VERSION() AS version")
    info = cursor.fetchone()
    cursor.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
    names = [next(iter(row.values())) for row in cursor.fetchall()]
    tables = []
    for name in names:
        identifier = "`" + name.replace("`", "``") + "`"
        cursor.execute("SHOW CREATE TABLE " + identifier)
        ddl = cursor.fetchone()["Create Table"]
        cursor.execute("SHOW FULL COLUMNS FROM " + identifier)
        columns = cursor.fetchall()
        cursor.execute("SHOW INDEX FROM " + identifier)
        indexes = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS total FROM " + identifier)
        total = cursor.fetchone()["total"]
        primary = sorted((i for i in indexes if i["Key_name"] == "PRIMARY"),
                         key=lambda i: i["Seq_in_index"])
        order = ", ".join("`" + i["Column_name"].replace("`", "``") + "`" for i in primary)
        cursor.execute("SELECT * FROM " + identifier + (" ORDER BY " + order if order else "") + " LIMIT 500")
        rows = cursor.fetchall()
        tables.append({"name": name, "ddl": ddl, "columns": columns,
                       "indexes": indexes, "total_rows": total,
                       "rows": rows, "truncated": total > len(rows)})
    print(json.dumps({"captured_at": datetime.now(timezone.utc).isoformat(),
                      "source": "当前项目 API 配置连接的 MySQL（只读事务）",
                      "database": info, "tables": tables}, ensure_ascii=False, default=str))
"""


def esc(value):
    if value is None:
        value = "NULL"
    elif isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return html.escape(str(value))


def table(headers, rows):
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join(f"<th>{esc(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table></div>"
    )


def render(report, snapshot, test_code, export_error):
    cases = report.get("cases", [])
    passed = sum(case["status"] == "passed" for case in cases)
    total = report.get("tests_run", 0)
    success = test_code == 0 and report.get("successful") and total > 0
    status = "全部通过" if success else "未通过 / 未完成"
    parts = [
        f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MySQL 测试与人工审查</title>
<style>
:root {{color-scheme:light; font-family:system-ui,-apple-system,"PingFang SC",sans-serif;color:#18283e;background:#f3f6fa}}
body {{max-width:1200px;margin:auto;padding:36px 24px 64px;line-height:1.65}}
h1 {{font-size:30px;margin:8px 0}} h2 {{font-size:23px;margin:32px 0 12px}} h3 {{font-size:18px}}
a {{color:#145bbc}} .muted {{color:#4d5f73}} .eyebrow {{color:#145bbc;letter-spacing:2px;font-size:13px}}
nav {{display:flex;gap:20px;flex-wrap:wrap;margin:22px 0}} .summary {{display:flex;gap:30px;flex-wrap:wrap;background:#132b48;color:white;padding:22px;border-radius:12px}}
.summary strong {{font-size:26px;display:block}} .summary span {{color:#d9e5f5}} section,details.case {{background:white;border:1px solid #d8e1ec;border-radius:10px;padding:18px;margin:12px 0}}
summary {{cursor:pointer;font-weight:600;overflow-wrap:anywhere}} .badge {{display:inline-block;padding:2px 9px;border-radius:5px;margin-right:8px;font-size:13px}}
.passed {{background:#def3e7;color:#12643a}} .failed,.error {{background:#ffe7e7;color:#9a2020}} .skipped {{background:#fff0c9;color:#694900}}
.scroll {{overflow-x:auto}} table {{border-collapse:collapse;width:100%;font-size:14px;margin:12px 0}} th,td {{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #e0e6ee;overflow-wrap:anywhere;min-width:75px}}
th {{background:#f0f4f9;white-space:nowrap}} td:first-child {{white-space:nowrap}} pre {{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f4f9;padding:14px;border-radius:6px;font-size:13px}}
progress {{width:100%;height:12px;accent-color:#218555;margin-top:16px}} .empty {{padding:18px;background:#f0f4f9;border-left:3px solid #698199}}
@media(max-width:600px) {{body {{padding:18px 12px}} h1 {{font-size:25px}} section,details.case {{padding:12px}}}}
@media print {{body {{background:white}} details {{break-inside:avoid}} nav {{display:none}}}}
</style></head><body><header><div class="eyebrow">SYNCFLOW · WEEK 02</div>
<h1>MySQL 测试与人工审查</h1><p class="muted">测试断言来自本次真实执行；数据库部分为当前开发库的只读快照。</p>
<nav><a href="#tests">查看测试例子</a><a href="#database">审查真实数据库表</a><a href="test-results.json">测试原始结果</a><a href="database-snapshot.json">数据库原始快照</a><a href="schema.sql">真实建表 SQL</a></nav>
<div class="summary"><div><span>自动测试</span><strong>{esc(status)}</strong></div><div><span>通过 / 执行用例</span><strong>{passed} / {total}</strong></div><div><span>测试数据库</span><strong>{esc(report.get("database", "未获取"))}</strong></div></div>
<progress aria-label="测试通过比例" value="{passed}" max="{max(total, 1)}"></progress></header>
<h2 id="tests">01 · 测试例子与实测断言</h2><p class="muted">执行时间（UTC）：{esc(report.get("generated_at", "未生成报告"))}。点击用例查看预期、实际值和断言结果。测试数据仅写入隔离测试库。</p>'''
    ]
    names = {"passed": "通过", "failed": "失败", "error": "错误", "skipped": "跳过"}
    rules = {
        "assertEqual": "相等",
        "assertTrue": "为真",
        "assertFalse": "为假",
        "assertIsNone": "为空",
        "assertIsNotNone": "非空",
        "assertGreaterEqual": "大于等于",
        "assertIn": "包含于",
        "assertNotIn": "不包含于",
        "assertRaises": "抛出异常",
    }
    for case in cases:
        state = case["status"]
        parts.append(
            f'<details class="case"><summary><span class="badge {esc(state)}">{names.get(state, esc(state))}</span>{esc(case["description"])}</summary>'
        )
        parts.append(f'<p class="muted">{esc(case["id"])} · {case["seconds"]} 秒</p>')
        parts.append(
            table(
                ["检查规则", "实际值 / 输入", "预期值 / 比较对象", "结果"],
                [
                    [
                        rules.get(a["assertion"], a["assertion"]),
                        a["actual"],
                        a["expected"],
                        "通过" if a["passed"] else "失败",
                    ]
                    for a in case["assertions"]
                ],
            )
        )
        if case.get("detail"):
            parts.append(f"<pre>{esc(case['detail'])}</pre>")
        parts.append("</details>")
    for error in report.get("errors", []):
        parts.append(
            f"<section><h3>执行错误 · {esc(error['id'])}</h3><pre>{esc(error['detail'])}</pre></section>"
        )
    if not cases:
        parts.append(
            '<p class="empty">本次未执行测试用例，请检查运行日志，不能据此判断测试通过。</p>'
        )
    parts.append('<h2 id="database">02 · 项目真实数据库表</h2>')
    if export_error:
        parts.append(
            f'<p class="empty">数据库快照未完成：{esc(export_error)}。没有使用旧快照替代。</p>'
        )
    else:
        info = snapshot["database"]
        parts.append(
            f'<p class="muted">数据库：{esc(info["name"])} · MySQL {esc(info["version"])}<br>采集时间（UTC）：{esc(snapshot["captured_at"])} · {esc(snapshot["source"])}</p>'
        )
        parts.append(
            table(
                ["实际表名", "总数据行数", "导出行数", "数据完整性"],
                [
                    [
                        t["name"],
                        t["total_rows"],
                        len(t["rows"]),
                        "前 500 行" if t["truncated"] else "全部",
                    ]
                    for t in snapshot["tables"]
                ],
            )
        )
        for index, t in enumerate(snapshot["tables"], 1):
            parts.append(f"<section><h3>{esc(t['name'])} · {t['total_rows']} 行</h3>")
            parts.append(
                table(
                    ["字段", "真实类型", "允许 NULL", "默认值", "键", "额外属性"],
                    [
                        [
                            c["Field"],
                            c["Type"],
                            c["Null"],
                            c["Default"],
                            c["Key"],
                            c["Extra"],
                        ]
                        for c in t["columns"]
                    ],
                )
            )
            parts.append("<details><summary>查看实际索引</summary>")
            parts.append(
                table(
                    ["索引", "唯一", "列序号", "列", "类型"],
                    [
                        [
                            i["Key_name"],
                            "是" if i["Non_unique"] == 0 else "否",
                            i["Seq_in_index"],
                            i["Column_name"],
                            i["Index_type"],
                        ]
                        for i in t["indexes"]
                    ],
                )
            )
            parts.append(
                f"</details><details><summary>查看 SHOW CREATE TABLE 原文（含检查约束）</summary><pre>{esc(t['ddl'])}</pre></details>"
            )
            parts.append(
                f'<h3>当前真实数据</h3><a href="table-{index}.csv">下载此表数据 CSV</a>'
            )
            if t["rows"]:
                headers = [c["Field"] for c in t["columns"]]
                parts.append(
                    table(headers, [[row.get(h) for h in headers] for row in t["rows"]])
                )
            else:
                parts.append(
                    '<p class="empty">当前表为空（0 行）。这里没有填入模拟数据。</p>'
                )
            if t["truncated"]:
                parts.append("<p>仅展示并导出前 500 行；完整行数见上表。</p>")
            parts.append("</section>")
    parts.append(
        '<footer class="muted">静态审查快照，不自动刷新。重新运行 python3 scripts/review-db.py 可生成新报告。导出不会改写开发库。</footer></body></html>'
    )
    return "".join(parts)


def main():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = ROOT / "output" / "db-review" / stamp
    output.mkdir(parents=True)
    print("运行独立数据库自动测试…", flush=True)
    test = subprocess.run(["sh", "scripts/test-db.sh"], cwd=ROOT, check=False)
    result_file = ROOT / "output/db-tests/test-results.json"
    report = json.loads(result_file.read_text()) if result_file.exists() else {}
    (output / "test-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print("读取当前项目真实数据库（只读）…", flush=True)
    exported = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-"],
        cwd=ROOT,
        input=SNAPSHOT,
        text=True,
        capture_output=True,
        check=False,
    )
    snapshot, export_error = {}, ""
    if exported.returncode:
        export_error = (
            "无法从运行中的 API 读取数据库；请先启动项目并检查 MySQL 连通性。"
        )
        print(export_error, file=sys.stderr)
    else:
        try:
            snapshot = json.loads(exported.stdout)
        except json.JSONDecodeError:
            export_error = "数据库返回内容不是有效快照。"
    if snapshot:
        (output / "database-snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2)
        )
        (output / "schema.sql").write_text(
            f"-- Actual SHOW CREATE TABLE snapshot: {snapshot['captured_at']}\n"
            + "\n\n".join(t["ddl"] + ";" for t in snapshot["tables"])
            + "\n"
        )
        for index, t in enumerate(snapshot["tables"], 1):
            # Ordinal filenames avoid treating database identifiers as file paths.
            with (output / f"table-{index}.csv").open(
                "w", newline="", encoding="utf-8-sig"
            ) as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=[c["Field"] for c in t["columns"]]
                )
                writer.writeheader()
                writer.writerows(t["rows"])
    (output / "index.html").write_text(
        render(report, snapshot, test.returncode, export_error)
    )
    print(f"审查报告：{output / 'index.html'}")
    return (
        0
        if test.returncode == 0
        and report.get("successful")
        and report.get("tests_run", 0) > 0
        and not export_error
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
