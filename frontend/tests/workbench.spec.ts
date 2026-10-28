import { test, expect } from "@playwright/test";

test("login, submit, cancel, retry, resources and administration", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(process.env.ADMIN_USERNAME!);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "我的评测任务" }),
  ).toBeVisible();
  const title = "browser-" + Date.now();
  await page.getByLabel("任务名称").fill(title);
  await page.getByLabel("模型 / checkpoint 标识").fill("browser-model");
  await page
    .getByLabel("资源池", { exact: true })
    .selectOption({ label: "mock · virtual" });
  await page.getByLabel("评测回合").fill("3");
  await page.getByRole("button", { name: "提交评测", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: title })).toBeVisible();
  await dialog.getByRole("button", { name: "取消评测", exact: true }).click();
  await expect(dialog.locator(".badge")).toHaveText("已取消");
  await dialog.getByRole("button", { name: "重新评测", exact: true }).click();
  await expect(dialog.locator(".badge")).toHaveText("排队中");
  await expect(dialog.getByLabel("执行尝试").locator("option")).toHaveCount(2);
  await dialog.getByRole("button", { name: "取消评测", exact: true }).click();
  await expect(dialog.locator(".badge")).toHaveText("已取消");
  await dialog.getByRole("button", { name: "关闭详情" }).click();
  await page.getByRole("button", { name: "计算资源", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "计算资源", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "管理设置", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "注册评测机器" }),
  ).toBeVisible();
  await page.getByLabel("机器名称").fill("browser-" + Date.now());
  await page
    .getByRole("button", { name: "生成 Worker 凭据", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /的 Worker 配置/ }),
  ).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载配置文件" }).click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toMatch(/^worker-browser-/);
  await page.getByRole("button", { name: "全部任务", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "全部评测任务", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  const completed = page
    .locator("tr")
    .filter({ has: page.locator(".badge.succeeded") })
    .first();
  await completed.getByRole("button", { name: "查看详情" }).click();
  await expect(
    page.getByRole("dialog").getByText("成功率", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("dialog").locator(".logs")).toContainText(
    "episode",
  );
  await page.getByRole("button", { name: "关闭详情" }).click();
  await page.screenshot({ path: "test-results/workbench.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "我的任务", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
