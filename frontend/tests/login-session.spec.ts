import {test,expect,Page} from '@playwright/test';
const start = new Date('2026-11-17T12:00:00Z');
function token(exp:number,sub='u') {return 'header.'+Buffer.from(JSON.stringify({sub,exp,session_started_at:start.getTime()/1000})).toString('base64url')+'.signature';}
async function setup(page:Page, failRenew=false) {
  await page.clock.install({time:start});
  const initial=token(start.getTime()/1000+1800);
  await page.addInitScript(value=>sessionStorage.setItem('eval-token',value),initial);
  let renewals=0, username='admin';
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');
    if(path==='auth/renew') {
      renewals++;
      if(failRenew){await route.fulfill({status:503,json:{detail:'unavailable'}});return;}
      const now=await page.evaluate(()=>Date.now()/1000);
      await route.fulfill({json:{access_token:token(now+1800)}});return;
    }
    if(path==='auth/login') {
      username=route.request().postDataJSON().username;
      const now=await page.evaluate(()=>Date.now()/1000);
      await route.fulfill({json:{access_token:token(now+1800,username)}});return;
    }
    let data:any=[];
    if(path==='auth/me')data={id:username==='admin'?'u':'other',username,role:'admin'};
    if(path==='admin/ssh-onboarding')data={available:true,operations:[]};
    if(path==='jobs')data=[];
    await route.fulfill({json:data});
  });
  await page.goto('/');
  await expect(page.getByRole('button',{name:'管理设置',exact:true})).toBeVisible();
  return {renewals:()=>renewals,initial};
}
async function draft(page:Page) {
  await page.getByRole('button',{name:'管理设置',exact:true}).click();
  await page.getByRole('tab',{name:'账号管理',exact:true}).click();
  await page.getByRole('tab',{name:'创建账号',exact:true}).click();
  await page.getByLabel('用户名',{exact:true}).fill('unsaved-draft');
}
async function relogin(page:Page,name='admin') {
  await page.locator('.login').getByLabel('用户名',{exact:true}).fill(name);
  await page.locator('.login').getByLabel('密码',{exact:true}).fill('password');
  await page.locator('.login').getByRole('button',{name:'登录',exact:true}).click();
}
test('interaction renews, polling does not, idle locks and same user restores draft',async({page})=>{
  const state=await setup(page);await draft(page);
  await expect.poll(state.renewals).toBe(1);
  await page.clock.fastForward(20*60000);
  expect(state.renewals()).toBe(1);
  await page.getByLabel('用户名',{exact:true}).press('End');
  await expect.poll(state.renewals).toBe(2);
  await expect.poll(()=>page.evaluate(()=>JSON.parse(atob(sessionStorage.getItem('eval-token')!.split('.')[1])).exp)).toBeGreaterThan(start.getTime()/1000+40*60);
  await page.clock.fastForward(20*60000);
  await expect(page.getByLabel('用户名',{exact:true})).toHaveValue('unsaved-draft');
  await page.clock.fastForward(11*60000);
  await expect(page.getByRole('heading',{name:'登录已过期'})).toBeVisible();
  await relogin(page,'other');
  await expect(page.getByRole('alert')).toContainText('请使用原账号登录');
  await relogin(page);
  await expect(page.getByRole('heading',{name:'登录已过期'})).not.toBeVisible();
  await expect(page.getByLabel('用户名',{exact:true})).toHaveValue('unsaved-draft');
});
test('network failure retains draft and expires; explicit logout discards workspace',async({page})=>{
  await setup(page,true);await draft(page);
  await page.clock.fastForward(28*60000);
  await expect(page.getByRole('status')).toContainText('登录即将过期');
  await page.clock.fastForward(3*60000);
  await expect(page.getByRole('heading',{name:'登录已过期'})).toBeVisible();
  await page.getByRole('button',{name:'退出并清除当前草稿'}).click();
  await expect(page.getByRole('heading',{name:'评测工作台'})).toBeVisible();
});
test('synthetic events and background polling do not renew',async({page})=>{
  const state=await setup(page);
  await page.evaluate(()=>{window.dispatchEvent(new KeyboardEvent('keydown'));window.dispatchEvent(new Event('pointerdown'));});
  await page.clock.fastForward(31*60000);
  expect(state.renewals()).toBe(0);
  await expect(page.getByRole('heading',{name:'登录已过期'})).toBeVisible();
});
test('server 401 locks workspace and closes open guide',async({page})=>{
  await setup(page);await draft(page);
  await page.getByRole('button',{name:'用户指南',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.route('**/api/v1/forced-expiry',route=>route.fulfill({status:401,json:{detail:'invalid token'}}));
  await page.evaluate(async()=>{const {api}=await import('/src/api.ts');try{await api('forced-expiry')}catch{}});
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('heading',{name:'登录已过期'})).toBeVisible();
  await relogin(page);
  await expect(page.getByRole('heading',{name:'登录已过期'})).not.toBeVisible();
  await expect(page.getByLabel('用户名',{exact:true})).toHaveValue('unsaved-draft');
});
test('late renewal cannot resurrect explicit logout',async({page})=>{
  await setup(page);let release!:()=>void;const gate=new Promise<void>(r=>release=r);
  await page.route('**/api/v1/auth/renew',async route=>{await gate;await route.fulfill({json:{access_token:token(start.getTime()/1000+3600)}})});
  await page.getByRole('button',{name:'退出登录',exact:true}).click();
  release();await page.waitForTimeout(100);
  expect(await page.evaluate(()=>sessionStorage.getItem('eval-token'))).toBeNull();
  await expect(page.getByRole('heading',{name:'评测工作台'})).toBeVisible();
});
test('old 401 does not clear newer token',async({page})=>{
  await setup(page);let release!:()=>void;const gate=new Promise<void>(r=>release=r);
  await page.route('**/api/v1/old-request',async route=>{await gate;await route.fulfill({status:401,json:{detail:'invalid token'}})});
  await page.evaluate(()=>{void import('/src/api.ts').then(({api})=>api('old-request').catch(()=>{}));});
  await page.waitForTimeout(100);
  const next=token(start.getTime()/1000+3600);
  await page.evaluate(value=>sessionStorage.setItem('eval-token',value),next);
  release();await page.waitForTimeout(100);
  expect(await page.evaluate(()=>sessionStorage.getItem('eval-token'))).toBe(next);
  await expect(page.getByRole('button',{name:'管理设置',exact:true})).toBeVisible();
});
test('hidden page does not renew and expired session cannot resume by interaction',async({page})=>{
 const state=await setup(page);
 await page.evaluate(()=>Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'}));
 await page.getByRole('button',{name:'管理设置',exact:true}).click();
 expect(state.renewals()).toBe(0);
 await page.clock.fastForward(31*60000);
 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));});
 await expect(page.getByRole('heading',{name:'登录已过期'})).toBeVisible();
 await page.locator('.login').getByLabel('密码',{exact:true}).fill('typing-after-expiry');
 expect(state.renewals()).toBe(0);
});
test('absolute expiry supplied by server locks even after active renewal',async({page})=>{
 await setup(page);
 const capped=token(start.getTime()/1000+120);
 await page.route('**/api/v1/auth/renew',route=>route.fulfill({json:{access_token:capped}}));
 await draft(page);
 await expect.poll(()=>page.evaluate(()=>sessionStorage.getItem('eval-token'))).toBe(capped);
 await page.clock.fastForward(121000);
 await expect(page.getByRole('heading',{name:'登录已过期'})).toBeVisible();
});
test('renewal 401 retains draft and old session requires login',async({page})=>{
 await setup(page);await draft(page);
 await page.route('**/api/v1/auth/renew',route=>route.fulfill({status:401,json:{detail:'invalid token'}}));
 await page.clock.fastForward(61000);
 await page.getByLabel('用户名',{exact:true}).press('End');
 await expect(page.getByRole('heading',{name:'登录已过期'})).toBeVisible();
 await relogin(page);
 await expect(page.getByRole('heading',{name:'登录已过期'})).not.toBeVisible();
 await expect(page.getByLabel('用户名',{exact:true})).toHaveValue('unsaved-draft');
});
