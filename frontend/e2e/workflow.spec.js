import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
const credentials = process.env.SMARTTICKET_E2E_DOCKER
  ? {
      admin_password: fs
        .readFileSync(path.resolve("../.docker-secrets/admin_password"), "utf8")
        .trim(),
      demo_password: fs
        .readFileSync(path.resolve("../.docker-secrets/demo_password"), "utf8")
        .trim(),
    }
  : JSON.parse(
      fs.readFileSync(path.resolve("../.mysql-local/credentials.json"), "utf8"),
    );
const output = path.join(
  os.homedir(),
  ".codex",
  "visualizations",
  "smartticket-qa",
);
fs.mkdirSync(output, { recursive: true });
const useLLM = process.env.SMARTTICKET_E2E_LLM === "1";
async function login(page, user) {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(user);
  await page
    .getByLabel("密码", { exact: true })
    .fill(
      user === "admin" ? credentials.admin_password : credentials.demo_password,
    );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
}
test("customer to admin: ticket, routing, evidence, human review, reply, closure", async ({
  page,
}) => {
  const errors = [];
  let llmRequests = 0;
  page.on("request", (request) => {
    if (
      request.url().endsWith("/assistant/draft") &&
      request.method() === "POST" &&
      request.postDataJSON()?.mode === "llm"
    )
      llmRequests++;
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (["error", "warning", "warn"].includes(message.type()))
      errors.push(message.text());
  });
  await login(page, "demo_customer_1");
  const title = `浏览器验收耳机无声 ${Date.now()}`;
  await page.getByRole("button", { name: "新建工单" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator("input").nth(0).fill(title);
  await dialog
    .locator("textarea")
    .fill("蓝牙耳机一侧没有声音，已经换过设备仍然无声，请协助排查。");
  await dialog.locator("input").nth(1).fill("ST202611060101");
  await dialog.getByRole("button", { name: "提交工单" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await login(page, "admin");
  await page.getByRole("button", { name: title, exact: true }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await page.screenshot({
    path: path.join(output, "workbench-desktop.png"),
    fullPage: false,
  });
  await page.getByText("工单操作与 SLA", { exact: true }).click();
  await page
    .getByLabel("操作原因", { exact: true })
    .fill("浏览器验收人工确认质量诉求并按技能组分配");
  await page
    .locator(".el-select")
    .filter({
      has: page.getByRole("combobox", { name: "人工分类", exact: true }),
    })
    .click();
  await page.getByRole("option", { name: "商品质量", exact: true }).click();
  await page.getByRole("button", { name: "确认分类", exact: true }).click();
  await expect(
    page.getByText("REVIEWED_MANUALLY", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "派单 / 重分配", exact: true })
    .click();
  await expect(page.locator(".detail-heading")).toContainText("处理中");
  await page.getByRole("tab", { name: "业务记录", exact: true }).click();
  await page.getByRole("button", { name: "查询授权业务记录" }).click();
  await expect(page.getByText("ST202611060101", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "消息", exact: true }).click();
  await page
    .getByRole("button", {
      name: useLLM ? "生成百炼草稿" : "生成证据摘录",
      exact: true,
    })
    .click();
  if (useLLM)
    await expect(
      page.getByRole("button", { name: "生成百炼草稿", exact: true }),
    ).toBeDisabled();
  await expect(page.getByRole("heading", { name: "草稿原文" })).toBeVisible({
    timeout: 90000,
  });
  expect(llmRequests).toBe(useLLM ? 1 : 0);
  await page
    .getByLabel("人工最终回复", { exact: true })
    .fill(
      "您好，请先确认左右声道、连接状态和音量，异常持续时可转人工检测。具体售后条件需核实。",
    );
  await page
    .getByLabel("审核原因", { exact: true })
    .fill("自动化验收模拟客服核对知识并调整表述，未承诺退款赔偿");
  await page.getByRole("button", { name: "修改采纳", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "发送已审核回复" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "发送已审核回复" }).click();
  await expect(
    page.getByText("已发送，重复点击不会产生第二条消息"),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(output, "workbench-review.png"),
    fullPage: false,
  });
  await page
    .locator(".el-select")
    .filter({
      has: page.getByRole("combobox", { name: "目标状态", exact: true }),
    })
    .click();
  await page.getByRole("option", { name: "已解决", exact: true }).click();
  await page.getByRole("button", { name: "更新状态", exact: true }).click();
  await expect(page.locator(".detail-heading")).toContainText("已解决");
  await page
    .locator(".el-select")
    .filter({
      has: page.getByRole("combobox", { name: "目标状态", exact: true }),
    })
    .click();
  await page.getByRole("option", { name: "已关闭", exact: true }).click();
  await page.getByRole("button", { name: "更新状态", exact: true }).click();
  await expect(page.locator(".detail-heading")).toContainText("已关闭");
  await page.getByRole("button", { name: "知识库", exact: true }).click();
  await page.getByLabel("知识检索问题").fill("耳机一侧没有声音怎么办");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await expect(page.locator(".search-result").first()).toBeVisible({
    timeout: 30000,
  });
  await page.screenshot({
    path: path.join(output, "knowledge-desktop.png"),
    fullPage: false,
  });
  await page.getByRole("button", { name: "SLA 管理", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "当前有效政策" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "立即扫描", exact: true }).click();
  await page.getByRole("button", { name: "数据统计", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /工单状态分布/ }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(output, "metrics-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "系统配置", exact: true }).click();
  await expect(page.getByRole("button", { name: "创建用户" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "工单工作台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(output, "workbench-mobile.png"),
    fullPage: false,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
