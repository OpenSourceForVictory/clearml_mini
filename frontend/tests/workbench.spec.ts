import { test, expect } from "@playwright/test";

test('GPU environment check reports real rendering results', async ({ page }) => {
  test.skip(!process.env.RENDER_POOL_NAME, 'Requires an enabled GPU render deployment');
  test.setTimeout(120000);
  await page.goto('/');
  await page.getByLabel('用户名', { exact: true }).fill(process.env.ADMIN_USERNAME!);
  await page.getByLabel('密码', { exact: true }).fill(process.env.ADMIN_PASSWORD!);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.getByLabel('评测类型', { exact: true }).selectOption('robotwin-render');
  await expect(page.getByLabel('模型 / checkpoint 标识')).toHaveCount(0);
  await page.getByLabel('任务名称').fill('R8000 浏览器渲染自检');
  await page.getByLabel('资源池', { exact: true }).selectOption({ label: process.env.RENDER_POOL_NAME! });
  await page.getByLabel('评测回合').fill('3');
  await page.getByRole('button', { name: '提交评测', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.locator('.badge')).toHaveText('已完成', { timeout: 90000 });
  await expect(dialog.getByText('环境检查通过率', { exact: true })).toBeVisible();
  await expect(dialog.locator('.logs')).toContainText('sapien-render-and-physics');
  await expect(dialog.getByLabel('执行尝试').locator('option')).toHaveCount(1);
  await page.screenshot({ path: 'test-results/r8000-render.png', fullPage: true });
});

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
    .selectOption({ label: "mock · 模拟池 · GPU 独占" });
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
    .filter({ hasNot: page.locator(".model", {hasText: "sapien-render"}) })
    .first();
  await completed.getByRole("button", { name: "查看详情" }).click();
  await expect(
    page.getByRole("dialog").getByText("成功率", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("dialog").locator(".logs").last()).toContainText(
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

test('configure heterogeneous shared pool and submit GPU requirements', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('用户名', {exact: true}).fill(process.env.ADMIN_USERNAME!);
  await page.getByLabel('密码', {exact: true}).fill(process.env.ADMIN_PASSWORD!);
  await page.getByRole('button', {name: '登录', exact: true}).click();
  await page.getByRole('button', {name: '管理设置', exact: true}).click();
  await page.getByRole('tab', {name: '资源池管理', exact: true}).click();
  const name = 'mixed-browser-' + Date.now();
  const form = page.locator('form').filter({has: page.getByRole('button', {name: '创建资源池', exact: true})});
  await form.getByLabel('名称', {exact: true}).fill(name);
  await form.getByLabel('设备类型').selectOption('*');
  await form.getByLabel('允许 GPU 共享').check();
  await form.getByLabel('每卡任务上限').fill('2');
  await form.getByRole('button', {name: '创建资源池', exact: true}).click();
  const policy = page.locator('form').filter({has: page.getByRole('heading', {name, exact: true})});
  await expect(policy.getByLabel('每卡任务上限')).toHaveValue('2');
  await policy.getByLabel('共享准入利用率阈值 %').fill('80');
  await policy.getByRole('button', {name: '保存 '+name+' 策略'}).click();
  await page.getByRole('button', {name: '我的任务', exact: true}).click();
  await page.getByLabel('任务名称').fill(name);
  await page.getByLabel('模型 / checkpoint 标识').fill('model');
  await page.getByLabel('资源池', {exact: true}).selectOption({label: name+' · 异构 GPU 池 · 可共享'});
  await page.getByLabel('GPU 使用模式').selectOption('shared');
  await page.getByLabel('所需显存 GiB（共享至少 1）').fill('8');
  await page.getByLabel('指定 GPU 型号（可选，精确匹配）').fill('NVIDIA GeForce RTX 4090');
  await page.getByRole('button', {name: '提交评测', exact: true}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('共享 · 8 GiB · NVIDIA GeForce RTX 4090', {exact: true})).toBeVisible();
  await dialog.getByRole('button', {name: '取消评测', exact: true}).click();
  await expect(dialog.locator('.badge')).toHaveText('已取消');
  await page.screenshot({path: 'test-results/shared-gpu.png', fullPage: true});
});
