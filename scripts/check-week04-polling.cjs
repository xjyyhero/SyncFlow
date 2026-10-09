/* Real React/StrictMode browser checks; API responses and browser time are controlled.
 * Start Vite, then WEB_ORIGIN=http://127.0.0.1:15174 node scripts/check-week04-polling.cjs.
 * Reuses installed Playwright/Chrome; NODE_PATH may point to the bundled modules.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");
const origin = process.env.WEB_ORIGIN || "http://127.0.0.1:15174";
const output = path.resolve(process.env.REPORT_DIR || "output/week-04-polling");
const cases = [];
const errors = [];
const job = (id, status = "PENDING", success = 0) => ({
  id,
  name: `轮询验收-${id}`,
  status,
  source_file_name: "test.csv",
  total_records: 10,
  success_records: success,
  failed_records: 0,
  retry_count: 0,
  created_at: "2026-10-08T00:00:00Z",
  started_at: null,
  finished_at: null,
  last_error_code: null,
  last_error_message: null,
});
const fail = (route) =>
  route.fulfill({
    status: 503,
    json: {
      error: {
        code: "DATABASE_UNAVAILABLE",
        message: "验收：服务暂不可用",
        details: [],
      },
    },
  });
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  async function check(name, run) {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 900 },
    });
    page.setDefaultTimeout(8000);
    page.on("pageerror", (error) => errors.push(error.message));
    await page.clock.install({ time: new Date("2026-10-08T12:00:00Z") });
    await page.clock.pauseAt(new Date("2026-10-08T12:00:01Z"));
    await page.addInitScript(() => {
      const set = window.setInterval.bind(window);
      const clear = window.clearInterval.bind(window);
      const active = new Set();
      window.pollingIntervals = active;
      window.setInterval = (callback, delay, ...args) => {
        const id = set(callback, delay, ...args);
        if (delay === 3000) active.add(id);
        return id;
      };
      window.clearInterval = (id) => {
        active.delete(id);
        clear(id);
      };
    });
    const requests = [];
    const server = {
      reply: (route, id) =>
        route.fulfill({ json: { data: job(id), meta: {} } }),
    };
    await page.route("**/api/v1/**", async (route) => {
      const url = new URL(route.request().url());
      requests.push(url.pathname);
      if (url.pathname === "/api/v1/jobs") {
        await route.fulfill({
          json: { data: [], meta: { page: 1, page_size: 20, total: 0 } },
        });
      } else {
        await server.reply(route, url.pathname.split("/").pop());
      }
    });
    const count = (id) =>
      requests.filter((p) => p === `/api/v1/jobs/${id}`).length;
    const intervals = () => page.evaluate(() => window.pollingIntervals.size);
    try {
      await run({ page, server, count, intervals });
      cases.push({ name, passed: true });
      console.log(`PASS ${name}`);
    } finally {
      await page.close();
    }
  }
  try {
    await check(
      "四个非终态均按固定 3 秒轮询并刷新统计",
      async ({ page, server, count, intervals }) => {
        let state = job("active");
        server.reply = (route) =>
          route.fulfill({ json: { data: state, meta: {} } });
        await page.goto(`${origin}/jobs/active`);
        await page.locator(".badge.PENDING").waitFor();
        assert.equal(await intervals(), 1);
        for (const [i, status] of [
          "PENDING",
          "RUNNING",
          "RETRYING",
          "CANCELING",
        ].entries()) {
          const before = count("active");
          state = { ...state, status, success_records: i + 1 };
          await page.clock.runFor(2999);
          assert.equal(count("active"), before);
          await page.clock.runFor(1);
          await page.waitForFunction(
            (n) =>
              document.querySelectorAll(".stats strong")[1]?.textContent ===
              String(n),
            i + 1,
          );
          assert.equal(count("active"), before + 1);
          assert.equal(await intervals(), 1);
          await page.locator(`.badge.${status}`).waitFor();
        }
      },
    );
    await check(
      "四个终态停止定时器，首次加载终态也不轮询",
      async ({ page, server, count, intervals }) => {
        let state;
        server.reply = (route) =>
          route.fulfill({ json: { data: state, meta: {} } });
        for (const terminal of [
          "SUCCESS",
          "PARTIAL_SUCCESS",
          "FAILED",
          "CANCELED",
        ]) {
          state = job(terminal, "RUNNING");
          await page.goto(`${origin}/jobs/${terminal}`);
          await page.locator(".badge.RUNNING").waitFor();
          state = { ...state, status: terminal };
          await page.clock.runFor(3000);
          await page.locator(`.badge.${terminal}`).waitFor();
          assert.equal(await intervals(), 0);
          const stopped = count(terminal);
          await page.clock.runFor(12000);
          assert.equal(count(terminal), stopped);
          await page.reload();
          await page.locator(`.badge.${terminal}`).waitFor();
          assert.equal(await intervals(), 0);
          const loaded = count(terminal);
          await page.clock.runFor(6000);
          assert.equal(count(terminal), loaded);
        }
      },
    );
    await check(
      "刷新失败保留数据，自动重试后清除错误",
      async ({ page, server, count, intervals }) => {
        await page.goto(`${origin}/jobs/recover`);
        await page.locator(".badge.PENDING").waitFor();
        server.reply = fail;
        await page.clock.runFor(3000);
        await page.getByRole("alert").filter({ hasText: "刷新失败" }).waitFor();
        await page.locator(".badge.PENDING").waitFor();
        assert.equal(await intervals(), 1);
        await page.screenshot({
          path: path.join(output, "retry-error.png"),
          fullPage: true,
        });
        server.reply = (route) =>
          route.fulfill({
            json: { data: job("recover", "RUNNING", 5), meta: {} },
          });
        const before = count("recover");
        await page.clock.runFor(3000);
        await page.locator(".badge.RUNNING").waitFor();
        assert.equal(count("recover"), before + 1);
        assert.equal(await page.getByRole("alert").count(), 0);
        assert.equal(
          await page.locator(".stats strong").nth(1).innerText(),
          "5",
        );
      },
    );
    await check(
      "首次网络失败仍自动恢复；手动刷新终态失败不重新轮询",
      async ({ page, server, count, intervals }) => {
        server.reply = (route) => route.abort("failed");
        await page.goto(`${origin}/jobs/initial`);
        await page.getByRole("alert").waitFor();
        assert.equal(await intervals(), 1);
        server.reply = (route) =>
          route.fulfill({
            json: { data: job("initial", "SUCCESS", 10), meta: {} },
          });
        await page.clock.runFor(3000);
        await page.locator(".badge.SUCCESS").waitFor();
        server.reply = fail;
        await page
          .getByRole("button", { name: "刷新任务", exact: true })
          .click();
        await page.getByRole("alert").waitFor();
        await page.locator(".badge.SUCCESS").waitFor();
        assert.equal(await intervals(), 0);
        const before = count("initial");
        await page.clock.runFor(9000);
        assert.equal(count("initial"), before);
      },
    );
    await check(
      "慢请求不重叠，完成后按下一固定时间点刷新",
      async ({ page, server, count }) => {
        await page.goto(`${origin}/jobs/slow`);
        await page.locator(".badge.PENDING").waitFor();
        let release;
        const gate = new Promise((resolve) => {
          release = resolve;
        });
        server.reply = async (route) => {
          await gate;
          await route.fulfill({
            json: { data: job("slow", "RUNNING", 1), meta: {} },
          });
        };
        const before = count("slow");
        const requested = page.waitForRequest((r) =>
          r.url().endsWith("/jobs/slow"),
        );
        await page.clock.runFor(3000);
        await requested;
        await page.clock.runFor(6000);
        assert.equal(count("slow"), before + 1);
        release();
        await page.locator(".badge.RUNNING").waitFor();
        server.reply = (route) =>
          route.fulfill({
            json: { data: job("slow", "SUCCESS", 10), meta: {} },
          });
        await page.clock.runFor(3000);
        await page.locator(".badge.SUCCESS").waitFor();
        assert.equal(count("slow"), before + 2);
      },
    );
    await check(
      "StrictMode、手动重试、重复进入和任务切换均仅一个定时器",
      async ({ page, count, intervals }) => {
        await page.goto(`${origin}/jobs/old`);
        await page.locator(".badge.PENDING").waitFor();
        assert.equal(await intervals(), 1);
        for (let i = 0; i < 3; i++) {
          const response = page.waitForResponse((r) =>
            r.url().endsWith("/jobs/old"),
          );
          await page
            .getByRole("button", { name: "刷新任务", exact: true })
            .click();
          await response;
          assert.equal(await intervals(), 1);
        }
        const old = count("old");
        await page.evaluate(() => {
          history.pushState({}, "", "/jobs/other");
          dispatchEvent(new PopStateEvent("popstate"));
        });
        await page.getByText("other", { exact: true }).waitFor();
        const before = count("other");
        const refreshed = page.waitForResponse((r) =>
          r.url().endsWith("/jobs/other"),
        );
        await page.clock.runFor(3000);
        await refreshed;
        assert.equal(count("other"), before + 1);
        assert.equal(count("old"), old);
        for (let i = 0; i < 3; i++) {
          await page.getByRole("link", { name: "← 返回任务列表" }).click();
          await page.getByRole("heading", { name: "任务列表" }).waitFor();
          assert.equal(await intervals(), 0);
          const stopped = count("other");
          await page.clock.runFor(6000);
          assert.equal(count("other"), stopped);
          await page.goBack();
          await page.getByText("other", { exact: true }).waitFor();
          assert.equal(await intervals(), 1);
        }
      },
    );
    await check(
      "卸载时取消慢请求，旧响应不会覆盖新任务",
      async ({ page, server, intervals }) => {
        let release;
        const gate = new Promise((resolve) => {
          release = resolve;
        });
        server.reply = async (route, id) => {
          if (id === "stale") await gate;
          await route.fulfill({
            json: {
              data: job(id, id === "stale" ? "FAILED" : "SUCCESS"),
              meta: {},
            },
          });
        };
        const requested = page.waitForRequest((r) =>
          r.url().endsWith("/jobs/stale"),
        );
        await page.goto(`${origin}/jobs/stale`);
        await requested;
        const aborted = page.waitForEvent("requestfailed", (r) =>
          r.url().endsWith("/jobs/stale"),
        );
        await page.evaluate(() => {
          history.pushState({}, "", "/jobs/current");
          dispatchEvent(new PopStateEvent("popstate"));
        });
        await page.locator(".badge.SUCCESS").waitFor();
        await aborted;
        release();
        await page.clock.runFor(9000);
        await page.getByText("current", { exact: true }).waitFor();
        assert.equal(await page.locator(".badge.FAILED").count(), 0);
        assert.equal(await intervals(), 0);
        await page.screenshot({
          path: path.join(output, "terminal.png"),
          fullPage: true,
        });
      },
    );
    assert.deepEqual(errors, []);
    await fs.writeFile(
      path.join(output, "browser-results.json"),
      JSON.stringify({ passed: true, cases }, null, 2),
    );
  } finally {
    await browser.close();
  }
})().catch(async (error) => {
  await fs.mkdir(output, { recursive: true });
  await fs.writeFile(
    path.join(output, "browser-results.json"),
    JSON.stringify({ passed: false, cases, error: error.stack }, null, 2),
  );
  console.error(error);
  process.exitCode = 1;
});
