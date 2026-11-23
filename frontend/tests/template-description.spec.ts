import {test,expect} from '@playwright/test';

test('wizard shows selected client and each server description, safely and on narrow screens',async({page})=>{
 const description='用途：评测\n<script>window.bad=true</script>\n'+'longword'.repeat(40);
 const client={id:'client',name:'Client',version:'1',enabled:true,protocols:['http'],description};
 const legacy={...client,id:'legacy',name:'Legacy',description:undefined};
 const suite={id:'suite',name:'Suite',version:'1',template_id:'client',template_ids:['client','legacy'],cases:[{case_id:'one',task_name:'demo',episodes:1,seed:1}]};
 const servers=[{id:'s1',name:'Server 1',version:'1',enabled:true,description:'模型服务说明',protocol:'http'},{id:'s2',name:'Server 2',version:'1',enabled:true,description:'另一个服务',protocol:'http'}];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={username:'admin',role:'admin'};
  if(path==='client-templates')data=[client,legacy];
  if(path==='runtime-templates/server'||path==='admin/server-templates')data=servers;
  if(path==='suites')data=[suite];if(path==='pools')data=[{id:'pool',name:'Pool'}];
  if(path==='execution-limits')data={client_execution_max_seconds:3600,client_execution_default_seconds:300};
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'业务评测向导',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByLabel('选择已保存集合').selectOption('suite');await w.getByLabel('批次名称',{exact:true}).fill('说明测试');await w.getByRole('button',{name:'下一步',exact:true}).click();
 const info=w.getByRole('region',{name:'客户端模板说明'});
 await expect(w.getByLabel('客户端模板',{exact:true})).toHaveValue('client');await expect(info).toContainText(description);
 expect(await info.locator('script').count()).toBe(0);
 await page.setViewportSize({width:390,height:844});expect(await info.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
 await w.getByLabel('客户端模板',{exact:true}).selectOption('legacy');await expect(info).toContainText('暂无模板说明');await expect(info).not.toContainText('用途：评测');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByLabel('服务来源').selectOption('managed');
 await w.getByLabel('服务 1 模板',{exact:true}).selectOption('s1');await expect(w.getByRole('region',{name:'服务 1 模板说明'})).toContainText('模型服务说明');
 await w.getByRole('button',{name:'添加服务定义',exact:true}).click();await w.getByLabel('服务 2 模板',{exact:true}).selectOption('s2');await expect(w.getByRole('region',{name:'服务 2 模板说明'})).toContainText('另一个服务');await expect(w.getByRole('region',{name:'服务 1 模板说明'})).toContainText('模型服务说明');
 await w.getByLabel('服务 1 模板',{exact:true}).selectOption('s2');await expect(w.getByRole('region',{name:'服务 1 模板说明'})).toContainText('另一个服务');
 await w.getByLabel('服务 1 模板',{exact:true}).selectOption('');await expect(w.getByRole('region',{name:'服务 1 模板说明'})).toHaveCount(0);
});

test('member draft editor keeps description separate from runtime JSON',async({page})=>{
 let saved:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const body=route.request().postDataJSON();saved=body;await route.fulfill({json:{id:'draft',name:'Draft',version:'1',revision:1,status:'draft',...body?.definition}})});
 await page.route(/https?:\/\/[^/]+\/$/,route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'}));await page.goto('/');
 await page.evaluate(async()=>{const React=(await import('/node_modules/.vite/deps/react.js')).default;const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;const {TemplateCopy}=await import('/src/template-copy.tsx');const el=document.createElement('div');document.body.replaceChildren(el);createRoot(el).render(React.createElement(TemplateCopy,{kind:'client',source:{id:'draft',status:'draft',revision:0,name:'Draft',version:'1',description:'原说明',command:['python3','business.py']},onClose:()=>{},onPublished:()=>{}}));});
 await expect(page.getByLabel('模板说明',{exact:true})).toHaveValue('原说明');await page.getByLabel('模板说明',{exact:true}).fill('新说明\n${task_name}');await page.getByRole('button',{name:'保存草稿',exact:true}).click();await expect.poll(()=>saved?.definition?.description).toBe('新说明\n${task_name}');expect(saved.definition.command).toEqual(['python3','business.py']);
});
