import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

test("AI review identity, results and history stay explicit without submitting human confirmation", async ({
  page,
}) => {
  const output = path.join(
    os.homedir(),
    ".codex",
    "visualizations",
    "smartticket-qa",
  );
  fs.mkdirSync(output, { recursive: true });
  const { admin_password: password } = JSON.parse(
    fs.readFileSync(path.resolve("../.mysql-local/credentials.json"), "utf8"),
  );
  const errors = [],
    mutations = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type()))
      errors.push(message.text());
  });
  page.on("request", (request) => {
    if (
      ["POST", "PUT", "PATCH", "DELETE"].includes(request.method()) &&
      request.url().includes("/api/") &&
      !request.url().endsWith("/auth/login")
    )
      mutations.push(request.url());
  });
  await page.goto("/");
  await expect(page).toHaveTitle(/SmartTicket/);
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "数据与评审", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "数据与评审", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "复核方式", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "AI复核", exact: true }).first(),
  ).toBeVisible();
  await page.locator(".review-title").first().click();
  await page.getByText("复核历史", { exact: true }).click();
  await expect(
    page.locator("article").filter({ hasText: "system-ai-review" }).first(),
  ).toContainText("AI复核");
  await expect(page.getByText(/AI复核会单独标识/)).toBeVisible();
  await page.screenshot({
    path: path.join(output, "reviews-ai-desktop.png"),
    fullPage: false,
  });
  expect(await page.locator("vite-error-overlay").count()).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(output, "reviews-ai-mobile.png"),
    fullPage: false,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  expect(mutations).toEqual([]);
  expect(errors).toEqual([]);
  fs.writeFileSync(
    path.join(output, "reviews-ai-browser.json"),
    JSON.stringify(
      {
        passed: true,
        url: page.url(),
        browser: "Chrome/Playwright",
        fallback: "Browser plugin not available",
        viewports: ["1487x1058", "390x844"],
        ai_identity_visible: true,
        history_visible: true,
        human_confirmation_submitted: false,
        paid_requests: 0,
        console_errors: errors,
      },
      null,
      2,
    ),
  );
});
