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
 await dialog.getByLabel('搜索指南').fill('SDK');
 await dialog.getByRole('button',{name:'业务接入：选择模式与下载 SDK',exact:true}).first().click();
 await expect(dialog.getByRole('heading',{name:'业务接入：选择模式与下载 SDK'})).toBeVisible();
 const download=page.waitForEvent('download');await dialog.getByRole('link',{name:'下载 SDK 与三种接入样例（ZIP）'}).click();
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
