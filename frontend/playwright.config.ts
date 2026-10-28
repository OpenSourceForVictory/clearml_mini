import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 45000,
  use: {
    baseURL: process.env.EVAL_API_URL || "http://127.0.0.1:18080",
    headless: true,
    viewport: { width: 1440, height: 1000 },
  },
  reporter: "list",
});
