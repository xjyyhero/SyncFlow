/* Browser acceptance: Vite at WEB_ORIGIN proxies an isolated API with a live worker.
 * Requires Playwright and Chrome already installed; no project dependency added.
 * NODE_PATH can point to a directory containing the installed playwright package.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const origin = process.env.WEB_ORIGIN || "http://127.0.0.1:15173";
const output = path.resolve(process.env.REPORT_DIR || "output/week-03-pages");
const sampleDir = path.resolve(__dirname, "../samples");
const cases = [];
const header = "external_id,name,amount,record_date\n";
const file = (name, text) => ({
  name,
  mimeType: "text/csv",
  buffer: Buffer.from(text),
});
const job = (id, status = "PENDING") => ({
  id,
  name: `浏览器验收-${id}`,
  status,
  source_file_name: "test.csv",
  total_records: 2,
  success_records: 0,
  failed_records: 0,
  retry_count: 0,
  created_at: "2026-09-25T00:00:00Z",
  started_at: null,
  finished_at: null,
  last_error_code: null,
  last_error_message: null,
});
async function check(name, run) {
  await run();
  cases.push({ name, passed: true });
  console.log(`PASS ${name}`);
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
  });
  page.setDefaultTimeout(10000);
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  try {
    await check("上传校验：后缀、大小、文件信息、名称上限", async () => {
      await page.goto(`${origin}/jobs/new`);
      const input = page.getByLabel("CSV 文件", { exact: true });
      assert.equal(await input.getAttribute("accept"), ".csv");
      assert.equal(
        await page.getByLabel("任务名称（可选）").getAttribute("maxlength"),
        "128",
      );
      assert.equal(
        await page.getByRole("button", { name: "上传并创建" }).isDisabled(),
        true,
      );
      await input.setInputFiles(file("bad.txt", header));
      await page
        .getByRole("alert")
        .filter({ hasText: "仅支持 .csv" })
        .waitFor();
      assert.equal(
        await page.getByRole("button", { name: "上传并创建" }).isDisabled(),
        true,
      );
      await input.setInputFiles({
        name: "big.csv",
        mimeType: "text/csv",
        buffer: Buffer.alloc(10 * 1024 * 1024 + 1),
      });
      await page.getByRole("alert").filter({ hasText: "10 MB" }).waitFor();
      assert.equal(
        await page.getByRole("button", { name: "上传并创建" }).isDisabled(),
        true,
      );
      await input.setInputFiles(file("正常.CSV", header));
      await page.getByRole("status").filter({ hasText: "正常.CSV" }).waitFor();
      assert.match(await page.getByRole("status").innerText(), /字节/);
      assert.equal(
        await page.getByRole("button", { name: "上传并创建" }).isEnabled(),
        true,
      );
    });
    await check("创建中禁用、阻止重复提交、后端失败保留输入", async () => {
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      let posts = 0;
      await page.route("**/api/v1/jobs", async (route) => {
        posts++;
        await gate;
        await route.fulfill({
          status: 500,
          json: {
            error: {
              code: "INTERNAL_ERROR",
              message: "验收：创建暂时失败",
              details: [],
            },
          },
        });
      });
      await page.getByLabel("任务名称（可选）").fill("保留我的输入");
      await page.getByRole("button", { name: "上传并创建" }).click();
      await page.getByRole("button", { name: "正在创建…" }).waitFor();
      assert.equal(
        await page.getByRole("button", { name: "正在创建…" }).isDisabled(),
        true,
      );
      await page.locator("form").dispatchEvent("submit");
      release();
      await page
        .getByRole("alert")
        .filter({ hasText: "验收：创建暂时失败" })
        .waitFor();
      assert.equal(posts, 1);
      assert.equal(
        await page.getByLabel("任务名称（可选）").inputValue(),
        "保留我的输入",
      );
      assert.match(
        await page.getByLabel("CSV 文件", { exact: true }).inputValue(),
        /正常\.CSV$/,
      );
      await page.unroute("**/api/v1/jobs");
    });

    // Deterministic timing tests use controlled responses, real React, and browser timers.
    const counts = {};
    let state = job("poll");
    await page.route("**/api/v1/jobs/*", async (route) => {
      const id = new URL(route.request().url()).pathname.split("/").pop();
      counts[id] = (counts[id] || 0) + 1;
      await route.fulfill({ json: { data: { ...state, id }, meta: {} } });
    });
    await page.clock.install();
    await check("3 秒刷新状态和统计，三个终态均停止轮询", async () => {
      for (const terminal of ["SUCCESS", "PARTIAL_SUCCESS", "FAILED"]) {
        state = job("poll");
        await page.goto(`${origin}/jobs/poll`);
        await page.getByText("每 3 秒自动刷新状态与统计").waitFor();
        const before = counts.poll;
        state = { ...state, status: "RUNNING", success_records: 1 };
        await page.clock.fastForward(2999);
        assert.equal(counts.poll, before);
        await page.clock.fastForward(1);
        await page.locator(".badge.RUNNING").waitFor();
        assert.equal(counts.poll, before + 1);
        assert.equal(
          await page
            .locator(".stats section")
            .nth(1)
            .locator("strong")
            .innerText(),
          "1",
        );
        state = {
          ...state,
          status: terminal,
          failed_records: terminal === "SUCCESS" ? 0 : 1,
        };
        await page.clock.fastForward(3000);
        await page.locator(`.badge.${terminal}`).waitFor();
        const stopped = counts.poll;
        await page.clock.fastForward(12000);
        assert.equal(counts.poll, stopped);
        assert.equal(
          await page.getByText("每 3 秒自动刷新状态与统计").count(),
          0,
        );
      }
    });
    await check("切换任务、离开详情清理定时器，不再请求旧任务", async () => {
      state = job("old");
      await page.goto(`${origin}/jobs/old`);
      await page.locator(".badge.PENDING").waitFor();
      const oldCount = counts.old;
      // Client-side history navigation preserves the Detail component instance.
      await page.evaluate(() => {
        history.pushState({}, "", "/jobs/new-id");
        dispatchEvent(new PopStateEvent("popstate"));
      });
      await page.getByText("new-id", { exact: true }).waitFor();
      const nextResponse = page.waitForResponse((response) =>
        response.url().endsWith("/jobs/new-id"),
      );
      await page.clock.fastForward(3000);
      await nextResponse;
      assert.equal(counts.old, oldCount);
      await page.getByRole("link", { name: "← 返回任务列表" }).click();
      const stopped = counts["new-id"];
      await page.clock.fastForward(9000);
      assert.equal(counts["new-id"], stopped);
    });
    await page.unroute("**/api/v1/jobs/*");
    // A fresh page restores the real clock for genuine API/worker interactions.
    await page.close();
    const live = await browser.newPage({
      viewport: { width: 1280, height: 900 },
    });
    live.setDefaultTimeout(15000);
    live.on("pageerror", (error) => pageErrors.push(error.message));
    let mixedId;
    const scenarios = [];
    const samples = JSON.parse(
      await fs.readFile(path.join(sampleDir, "week-03-expected.json"), "utf8"),
    );
    for (const expected of samples) {
      const { label, status: terminal } = expected;
      const expectedErrors = expected.error_rows.length;
      await check(
        `真实上传 → Worker → ${terminal} → 错误页面（${label}）`,
        async () => {
          await live.goto(`${origin}/jobs/new`);
          await live
            .getByLabel("任务名称（可选）")
            .fill(`Week 3 验收-${label}`);
          await live
            .getByLabel("CSV 文件", { exact: true })
            .setInputFiles(path.join(sampleDir, expected.file));
          await live.getByRole("button", { name: "上传并创建" }).click();
          await live.waitForURL(/\/jobs\/[\da-f-]{36}$/);
          const id = new URL(live.url()).pathname.split("/").pop();
          await live.locator(`.badge.${terminal}`).waitFor();
          const detailResponse = await live.request.get(
            `${origin}/api/v1/jobs/${id}`,
          );
          assert.equal(detailResponse.status(), 200);
          const detail = (await detailResponse.json()).data;
          for (const [index, key] of [
            "total_records",
            "success_records",
            "failed_records",
          ].entries()) {
            assert.equal(detail[key], expected[key]);
            assert.equal(
              await live
                .locator(".stats section")
                .nth(index)
                .locator("strong")
                .innerText(),
              String(expected[key]),
            );
          }
          assert.equal(detail.status, expected.status);
          const errorsResponse = await live.request.get(
            `${origin}/api/v1/jobs/${id}/errors?page_size=100`,
          );
          assert.equal(errorsResponse.status(), 200);
          const errors = await errorsResponse.json();
          assert.equal(errors.meta.total, expectedErrors);
          assert.deepEqual(
            errors.data.map((error) => error.row_number),
            expected.error_rows,
          );
          for (const error of errors.data)
            assert.equal(error.error_code, expected.error_code);
          scenarios.push({
            sample: expected.file,
            id,
            detail,
            errors: errors.data,
          });
          if (terminal === "SUCCESS") {
            await live.screenshot({
              path: path.join(output, "success.png"),
              fullPage: true,
            });
          }
          if (terminal === "PARTIAL_SUCCESS") {
            mixedId = id;
            assert.equal(
              await live
                .locator(".stats section")
                .nth(2)
                .locator("strong")
                .innerText(),
              "21",
            );
            await live.screenshot({
              path: path.join(output, "detail.png"),
              fullPage: true,
            });
          }
          if (terminal === "FAILED") {
            assert.equal(
              await live
                .locator(".stats section")
                .nth(2)
                .locator("strong")
                .innerText(),
              "0",
            );
          }
          if (expectedErrors)
            await live.getByRole("link", { name: "查看错误明细" }).click();
          else await live.goto(`${origin}/jobs/${id}/errors`);
          if (!expectedErrors)
            await live
              .getByRole("heading", { name: "暂无错误", exact: true })
              .waitFor();
          else {
            await live.locator("tbody tr").first().waitFor();
            assert.equal(
              await live.locator("tbody tr").count(),
              Math.min(20, expectedErrors),
            );
            if (terminal === "FAILED") {
              assert.equal(
                await live
                  .locator("tbody tr")
                  .first()
                  .locator("td")
                  .first()
                  .innerText(),
                "-",
              );
              await live
                .getByText("CSV_HEADER_INVALID", { exact: true })
                .waitFor();
              await live.screenshot({
                path: path.join(output, "file-error.png"),
                fullPage: true,
              });
            } else {
              await live.locator("details summary").first().click();
              assert.match(
                await live.locator("details pre").first().innerText(),
                /商品/,
              );
              await live.screenshot({
                path: path.join(output, "errors.png"),
                fullPage: true,
              });
              await live.getByRole("button", { name: "下一页" }).click();
              await live.waitForURL(/page=2/);
              await live.getByText("共 21 项 · 第 2 / 2 页").waitFor();
              assert.equal(await live.locator("tbody tr").count(), 1);
              assert.equal(
                await live.locator("tbody tr td").first().innerText(),
                "23",
              );
              await live.reload();
              await live.getByText("共 21 项 · 第 2 / 2 页").waitFor();
              await live.getByRole("button", { name: "第一页" }).click();
              await live.getByText("共 21 项 · 第 1 / 2 页").waitFor();
            }
          }
          await live.getByRole("link", { name: "← 返回任务详情" }).click();
          await live.locator(`.badge.${terminal}`).waitFor();
        },
      );
    }
    await check("错误页加载、失败、重试、空页、缺失任务", async () => {
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      const pattern = `**/api/v1/jobs/${mixedId}/errors*`;
      await live.route(pattern, async (route) => {
        await gate;
        await route.fulfill({
          status: 500,
          json: {
            error: {
              code: "INTERNAL_ERROR",
              message: "验收：错误查询失败",
              details: [],
            },
          },
        });
      });
      await live.goto(`${origin}/jobs/${mixedId}/errors`);
      await live
        .getByRole("status")
        .filter({ hasText: "正在加载错误明细" })
        .waitFor();
      release();
      await live
        .getByRole("alert")
        .filter({ hasText: "验收：错误查询失败" })
        .waitFor();
      await live.unroute(pattern);
      await live.getByRole("button", { name: "重试", exact: true }).click();
      await live.locator("tbody tr").first().waitFor();
      await live.goto(`${origin}/jobs/${mixedId}/errors?page=999`);
      await live.getByRole("heading", { name: "当前页没有错误" }).waitFor();
      await live.getByRole("button", { name: "第一页" }).click();
      await live.locator("tbody tr").first().waitFor();
      await live.goto(`${origin}/jobs/missing/errors`);
      await live.getByRole("alert").filter({ hasText: "任务不存在" }).waitFor();
    });
    await check("列表筛选、分页、部分成功状态及移动端导航回归", async () => {
      await live.goto(`${origin}/?page_size=1`);
      await live.locator("tbody tr").first().waitFor();
      await live.getByRole("button", { name: "下一页" }).click();
      await live.waitForURL(/page=2/);
      await live.getByLabel("任务状态").selectOption("PARTIAL_SUCCESS");
      await live.locator("tbody .badge.PARTIAL_SUCCESS").waitFor();
      assert.equal(new URL(live.url()).searchParams.get("page"), "1");
      await live.getByRole("link", { name: "详情", exact: true }).click();
      await live.locator(".badge.PARTIAL_SUCCESS").waitFor();
      await live.setViewportSize({ width: 390, height: 844 });
      await live.getByRole("link", { name: "查看错误明细" }).click();
      await live.locator("tbody tr").first().waitFor();
      assert.equal(
        await live.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        true,
      );
      await live.screenshot({
        path: path.join(output, "mobile-errors.png"),
        fullPage: true,
      });
      await live.getByRole("link", { name: "任务列表", exact: true }).click();
      await live.getByRole("link", { name: "创建任务", exact: true }).click();
      await live.getByRole("heading", { name: "创建任务" }).waitFor();
    });
    assert.deepEqual(pageErrors, []);
    await fs.writeFile(
      path.join(output, "browser-scenarios.json"),
      JSON.stringify(scenarios, null, 2),
    );
    await fs.writeFile(
      path.join(output, "browser-results.json"),
      JSON.stringify({ passed: true, cases }, null, 2),
    );
  } finally {
    await browser.close();
  }
})().catch(async (error) => {
  await fs.writeFile(
    path.join(output, "browser-results.json"),
    JSON.stringify({ passed: false, cases, error: error.stack }, null, 2),
  );
  console.error(error);
  process.exitCode = 1;
});
