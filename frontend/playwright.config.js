import { defineConfig } from "@playwright/test";
import path from "node:path";
import os from "node:os";
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 120000,
  outputDir: path.join(
    os.homedir(),
    ".codex",
    "visualizations",
    "smartticket-qa",
    "results",
  ),
  reporter: [["list"]],
  use: {
    baseURL: process.env.SMARTTICKET_E2E_URL || "http://127.0.0.1:5173",
    browserName: "chromium",
    channel: process.env.SMARTTICKET_E2E_CHANNEL || undefined,
    headless: true,
    viewport: { width: 1487, height: 1058 },
    trace: "off",
  },
});
