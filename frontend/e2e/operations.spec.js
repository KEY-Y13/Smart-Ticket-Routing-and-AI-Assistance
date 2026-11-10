import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const output = path.join(
  os.homedir(),
  ".codex",
  "visualizations",
  "smartticket-qa",
);
fs.mkdirSync(output, { recursive: true });
const password = process.env.SMARTTICKET_E2E_DOCKER
  ? fs
      .readFileSync(path.resolve("../.docker-secrets/admin_password"), "utf8")
      .trim()
  : JSON.parse(
      fs.readFileSync(path.resolve("../.mysql-local/credentials.json"), "utf8"),
    ).admin_password;

test("admin inspects real operational health, refreshes and switches pages without stale charts", async ({
  page,
}) => {
  const errors = [],
    requests = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (["error", "warning"].includes(m.type())) errors.push(m.text());
  });
  page.on("request", (r) => {
    if (r.url().endsWith("/assistant/draft")) requests.push(r.url());
  });
  await page.goto("/");
  await expect(page).toHaveTitle(/SmartTicket/);
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "数据统计", exact: true }).click();
  const card = page.getByRole("region", { name: "运行状态", exact: true });
  await expect(card).toBeVisible();
  for (const name of ["业务库", "后台任务", "检索服务", "知识索引"])
    await expect(card.getByText(name, { exact: true })).toBeVisible();
  const refresh = page.waitForResponse((r) =>
    r.url().endsWith("/admin/operations/health"),
  );
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  const data = await (await refresh).json();
  expect(data.components).toHaveLength(4);
  await expect(card).toContainText(
    `当前API进程共处理 ${data.http.requests} 次请求`,
  );
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.screenshot({
    path: path.join(output, "operations-desktop.png"),
    fullPage: false,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await page.route("**/admin/operations/health", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.continue();
  });
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "工单工作台", exact: true }).click();
    await page.getByRole("button", { name: "数据统计", exact: true }).click();
    await page.getByRole("button", { name: "工单工作台", exact: true }).click();
  }
  await page.getByRole("button", { name: "数据统计", exact: true }).click();
  await expect(card).toBeVisible();
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(output, "operations-mobile.png"),
    fullPage: false,
  });
  expect(await page.locator("vite-error-overlay").count()).toBe(0);
  expect(requests).toHaveLength(0);
  expect(errors).toEqual([]);
  fs.writeFileSync(
    path.join(
      output,
      process.env.SMARTTICKET_E2E_DOCKER
        ? "operations-docker-browser.json"
        : "operations-browser.json",
    ),
    JSON.stringify(
      {
        passed: true,
        url: page.url(),
        browser: "Chrome/Playwright",
        fallback: "Browser plugin not available",
        viewports: ["1487x1058", "390x844"],
        checks: {
          identity: true,
          meaningful_content: true,
          no_framework_overlay: true,
          console_clean: true,
          screenshots: true,
          refresh_real_data: true,
          delayed_navigation_safe: true,
          mobile_no_overflow: true,
          no_llm_requests: true,
        },
      },
      null,
      2,
    ),
  );
});
