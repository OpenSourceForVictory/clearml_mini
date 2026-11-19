import {test,expect} from '@playwright/test';
test('server machine shows deploy, status, logs and revisioned stop',async({page})=>{
 let operation:any;await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{let data:any=[];const p=new URL(route.request().url()).pathname.replace('/api/v1/','');
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'server',roles:['server'],enabled:true,status:'online'}];
 if(p==='admin/services')data=[{id:'s',name:'inference',worker_id:'n',state:'HEALTHY',online:true,revision:4,desired_running:true,cleanup_pending:true,endpoint:'http://192.168.8.151:19093',template:{name:'demo',version:'1'}}];
 if(p==='admin/services/s/logs')data={content:'ready for requests',history:[]};
 if(p==='admin/services/s/control'){operation=route.request().postDataJSON();data={}};
 await route.fulfill({json:data})});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'服务端机器'}).click();
 await expect(page.getByRole('heading',{name:'推理服务实例'})).toBeVisible();await expect(page.getByText('健康',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'日志',exact:true}).click();await expect(page.getByText('ready for requests')).toBeVisible();
 await page.getByRole('button',{name:'停止',exact:true}).click();await expect.poll(()=>operation).toEqual({action:'stop',expected_revision:4});
 await expect(page.getByRole('button',{name:'启动新执行'})).toBeDisabled();
});

test('service deployment sends typed business values separately from environment',async({page})=>{
 let submitted:any;await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:'admin',role:'admin'};
  if(p==='admin/nodes')data=[{id:'n',node_name:'server',roles:['server'],enabled:true,status:'online'}];
  if(p==='workers')data=[{id:'slot',worker_id:'n',pool_id:'p',node_name:'server',gpu_index:0,gpu_model:'test'}];
  if(p==='admin/server-templates')data=[{id:'t',name:'typed',version:'1',enabled:true,execution_mode:'docker',parameter_schema:{version:1,fields:{scene:{type:'string',required:true}}}}];
  if(p==='admin/services'&&route.request().method()==='POST'){submitted=route.request().postDataJSON();data={id:'s'}}
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'服务端机器'}).click();
 await page.getByRole('button',{name:'部署服务实例',exact:true}).click();
 await page.getByLabel('实例名称',{exact:true}).fill('typed instance');await page.getByLabel('服务端模板版本').selectOption('t');
 await page.getByLabel('目标机器 / GPU').selectOption('slot');await page.getByLabel('服务机器 IPv4 地址').fill('192.168.8.151');
 await page.getByLabel('业务参数 JSON',{exact:true}).fill('{"scene":"room"}');
 await page.getByLabel('不限制内存上限',{exact:true}).check();await page.getByLabel('服务最低内存 GiB').fill('8');
 await page.getByRole('button',{name:'提交部署',exact:true}).click();
 await expect.poll(()=>submitted).toMatchObject({template_id:'t',slot_id:'slot',parameters:{scene:'room'},parameter_overrides:{},environment:{},resources:{ram_gb:8,memory_unlimited:true}});
});

test('readiness diagnostics are read-only and collapse on a narrow screen',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{let data:any=[];const p=new URL(route.request().url()).pathname.replace('/api/v1/','');
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'server',roles:['server'],enabled:true,status:'online'}];
 if(p==='admin/services')data=[{id:'s',name:'checked',worker_id:'n',state:'UNHEALTHY',revision:1,endpoint:'http://192.168.8.151:19093',probe:{kind:'websocket',mode:'message',phase:'message',duration_ms:12,successes:0,failures:3,success_threshold:2,failure_threshold:3,error_code:'configuration_invalid',startup_remaining_seconds:42},template:{name:'checked',version:'1',readiness:{kind:'websocket',mode:'message',path:'/health',headers_from_env:{Authorization:'HEALTH_TOKEN'},expect:{type:'json_equals',pointer:'/ready',value:true}}}}];
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'服务端机器'}).click();
 const detail=page.locator('details').filter({has:page.locator('summary',{hasText:'就绪检查详情'})});
 await detail.locator('summary').click();
 await expect(detail.getByText('首次就绪剩余约 42 秒')).toBeVisible();
 await expect(detail.getByRole('status')).toContainText('管理员');
 await detail.getByRole('button',{name:'查看检查配置'}).click();
 const snapshot=detail.getByRole('group',{name:'服务检查配置快照'});
 await expect(snapshot.getByLabel('就绪检查方式')).toBeDisabled();
 await detail.getByRole('button',{name:'收起检查配置'}).click();await expect(snapshot).toHaveCount(0);
 await detail.locator('summary').click();await expect(detail.getByRole('status')).not.toBeVisible();
});

test('same-name service logs select the execution history without mixing scoped drawers',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 const queries:string[]=[];
 await page.route('**/api/v1/**',async route=>{
  const u=new URL(route.request().url()),p=u.pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:'admin',role:'admin'};
  if(p==='admin/nodes')data=[{id:'n',node_name:'server',roles:['server'],enabled:true,status:'online'}];
  if(p==='admin/services')data=['a','b'].map(id=>({id,name:'same name',worker_id:'n',state:'STOPPED',online:true,revision:1,desired_running:false,cleanup_pending:false,endpoint:'http://node/'+id,template:{name:'demo',version:'1'}}));
  if(/^admin\/services\/./.test(p)&&p.endsWith('/logs')){
   const id=p.split('/')[2],job=u.searchParams.get('job_id')||id+'-current';
   data={job_id:job,content:job+' summary',history:[{job_id:id+'-old',status:'FAILED',created_at:'previous'}]};
  }
  if(p.startsWith('jobs/')&&!p.includes('/attempts/'))data={attempts:[{id:'run-'+p.split('/')[1],attempt:1}]};
  if(p.endsWith('/log-catalog')){const job=p.split('/')[1];data={items:[{id:job,execution_id:job,scope:'service',episode_id:0,generation:'g',status:'closed',path:'/runs/'+job,next_sequence:1,updated_at:'today'}]};}
  if(p.endsWith('/log-reads')){queries.push(p.split('/')[1]);data={read_id:p.split('/')[1]};}
  if(p.startsWith('log-reads/'))data={status:'ready',generation:'g',page:{records:[{time:'now',stream:'stdout',text:p.split('/')[1]+' scoped'}],has_more:false,closed:true}};
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'服务端机器'}).click();
 const rows=page.getByRole('row').filter({hasText:'same name'});
 await rows.nth(0).getByRole('button',{name:'日志',exact:true}).click();
 await page.getByRole('button',{name:'服务实例日志',exact:true}).click();
 let drawer=page.getByRole('dialog',{name:'服务实例日志'});await expect(drawer).toContainText('a-current scoped');await drawer.getByRole('button',{name:'关闭独立日志'}).click();
 await page.getByLabel('执行历史').selectOption('a-old');await page.getByRole('button',{name:'服务实例日志',exact:true}).click();
 drawer=page.getByRole('dialog',{name:'服务实例日志'});await expect(drawer).toContainText('a-old scoped');await expect(drawer).not.toContainText('a-current scoped');await drawer.getByRole('button',{name:'关闭独立日志'}).click();
 await rows.nth(1).getByRole('button',{name:'日志',exact:true}).click();await page.getByRole('button',{name:'服务实例日志',exact:true}).click();
 drawer=page.getByRole('dialog',{name:'服务实例日志'});await expect(drawer).toContainText('b-current scoped');expect(queries).toEqual(['a-current','a-old','b-current']);
});
