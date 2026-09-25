import assert from "node:assert/strict";
import { test } from "node:test";
import {
  api,
  ApiError,
  formatTime,
  isTerminal,
  pageQuery,
  request,
  validateFile,
} from "./api.ts";

test("分页保留状态，切换筛选回到第一页", () => {
  const current = new URLSearchParams("status=SUCCESS&page=2&page_size=1");
  assert.equal(
    pageQuery(current, { page: "3" }).toString(),
    "status=SUCCESS&page=3&page_size=1",
  );
  assert.equal(
    pageQuery(current, { status: "FAILED", page: "1" }).get("page"),
    "1",
  );
  assert.equal(pageQuery(current, { status: "" }).has("status"), false);
  assert.equal(current.get("page"), "2");
});
test("本地时间格式与缺失时间", () => {
  assert.equal(
    formatTime(new Date(2026, 0, 1, 18, 0, 0).toISOString()),
    "2026-01-01 18:00:00",
  );
  assert.equal(formatTime(null), "—");
  assert.equal(formatTime("invalid"), "—");
});
test("CSV 选择校验包含扩展名和 10 MB 精确边界", () => {
  assert.match(validateFile(null), /请选择/);
  assert.match(validateFile({ name: "data.csv.txt", size: 1 }), /\.csv/);
  assert.equal(validateFile({ name: "数据.CSV", size: 10 * 1024 * 1024 }), "");
  assert.match(
    validateFile({ name: "data.csv", size: 10 * 1024 * 1024 + 1 }),
    /10 MB/,
  );
  // Empty CSV is a file-level worker error, not an upload validation error.
  assert.equal(validateFile({ name: "empty.csv", size: 0 }), "");
});
test("仅三个终态停止刷新", () => {
  for (const status of ["SUCCESS", "PARTIAL_SUCCESS", "FAILED"] as const)
    assert.equal(isTerminal(status), true);
  for (const status of ["PENDING", "RUNNING"] as const)
    assert.equal(isTerminal(status), false);
});
test("错误查询编码任务 ID 并传递分页与取消信号", async () => {
  const original = globalThis.fetch;
  const controller = new AbortController();
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, "/api/v1/jobs/job%2Fid/errors?page=2&page_size=1");
      assert.equal(options?.signal, controller.signal);
      return new Response(
        JSON.stringify({ data: [], meta: { page: 2, page_size: 1, total: 1 } }),
      );
    };
    const response = await api.errors(
      "job/id",
      "page=2&page_size=1",
      controller.signal,
    );
    assert.equal(response.meta.total, 1);
  } finally {
    globalThis.fetch = original;
  }
});
test("API 成功、业务错误和非 JSON 错误", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ data: [], meta: { total: 0 } }));
    assert.deepEqual(await request("/jobs"), { data: [], meta: { total: 0 } });
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          error: { code: "JOB_NOT_FOUND", message: "任务不存在", details: [] },
        }),
        { status: 404 },
      );
    await assert.rejects(
      request("/jobs/missing"),
      (error: unknown) =>
        error instanceof ApiError && error.code === "JOB_NOT_FOUND",
    );
    globalThis.fetch = async () => new Response("Bad gateway", { status: 502 });
    await assert.rejects(request("/jobs"), /服务响应异常/);
  } finally {
    globalThis.fetch = original;
  }
});
