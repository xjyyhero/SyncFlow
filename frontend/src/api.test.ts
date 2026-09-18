import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError, formatTime, pageQuery, request } from "./api.ts";

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
