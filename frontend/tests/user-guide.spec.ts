import {test,expect,Page} from '@playwright/test';
async function setup(page:Page,role='admin'){
 const writes:string[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async r=>{
  const path=new URL(r.request().url()).pathname;
  if(r.request().method()!=='GET')writes.push(path);
  await r.fulfill({json:path.endsWith('/auth/me')?{username:'guide-test',role}:[]});
 });
 await page.goto('/');return writes;
}
test('guide search, SDK download and role filtering',async({page})=>{
 const writes=await setup(page,'member');await page.getByRole('button',{name:'用户指南',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'用户指南'});await expect(dialog).toBeVisible();
 await expect(dialog.getByRole('button',{name:'账号管理',exact:true})).toHaveCount(0);
 await expect(dialog.getByRole('button',{name:'API 通道隔离与过载排查',exact:true})).toHaveCount(0);
 await dialog.getByLabel('搜索指南').fill('SDK');
 await dialog.getByRole('button',{name:'业务接入：选择模式与下载 SDK',exact:true}).first().click();
 await expect(dialog.getByRole('heading',{name:'业务接入：选择模式与下载 SDK'})).toBeVisible();
 await expect(dialog.getByText(/task_type=command/)).toBeVisible();
 await expect(dialog.getByRole('link',{name:'下载业务接入完整指南（含模板操作和协议样例）'})).toBeVisible();
 await dialog.getByRole('button',{name:'SDK 对接：输入、回合与结果',exact:true}).click();
 await expect(dialog.locator('li').filter({hasText:'继承 EvaluationClient'})).toBeVisible();
 await expect(dialog.locator('pre')).toContainText('from runner.sdk import EvaluationClient');
 await dialog.getByRole('button',{name:'直接评测协议：非 Python 程序也可接入',exact:true}).click();
 await expect(dialog.locator('li').filter({hasText:'每行是 MINI_EVAL_EVENT'})).toBeVisible();
 await expect(dialog.locator('pre')).toContainText('MINI_EVAL_EVENT');
 await expect(dialog.locator('pre')).toContainText('episode_count');
 const download=page.waitForEvent('download');await dialog.getByRole('link',{name:'下载 SDK 与四种客户端接入样例（ZIP）'}).click();
 expect((await download).suggestedFilename()).toBe('mini-eval-sdk.zip');
 await dialog.getByLabel('搜索指南').fill('no-such-guide-0123');await expect(dialog.getByRole('status')).toContainText('未找到');
 await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();await expect(page.getByRole('button',{name:'用户指南',exact:true})).toBeFocused();expect(writes).toEqual([]);
});
test('context help preserves template draft and does not submit',async({page})=>{
 const writes=await setup(page);await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();
 await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.getByLabel('模板名称',{exact:true}).fill('retained-draft');
 const opener=page.getByRole('button',{name:'业务如何接入：SDK 与样例'});await opener.click();
 await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'关闭用户指南'}).click();
 await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('retained-draft');await expect(opener).toBeFocused();expect(writes).toEqual([]);
});
test('admin help and narrow screen have no horizontal overflow',async({page})=>{
 await setup(page);await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'用户指南',exact:true}).click();
 const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'账号管理',exact:true}).click();await expect(dialog.getByRole('heading',{name:'账号管理'})).toBeVisible();
 expect(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
});

test('administrator can find API isolation guidance without changing configuration',async({page})=>{
 const writes=await setup(page);await page.getByRole('button',{name:'用户指南',exact:true}).click();
 const dialog=page.getByRole('dialog');await dialog.getByLabel('搜索指南').fill('通道隔离');
 await dialog.getByRole('button',{name:'API 通道隔离与过载排查',exact:true}).click();
 await expect(dialog.getByRole('heading',{name:'API 通道隔离与过载排查'})).toBeVisible();
 await expect(dialog.locator('li').filter({hasText:'仅 Worker 数据角色运行调度器'})).toBeVisible();
 await expect(dialog.locator('li').filter({hasText:'扩大连接池不能保证提升处理能力'})).toBeVisible();
 expect(writes).toEqual([]);
});
