import {test,expect} from '@playwright/test';
for(const width of [1440,390]) test(`read-only template uses registration layout without overflow ${width}`,async({page})=>{
 await page.setViewportSize({width,height:900});let writes=0;
 const template={id:'t',name:'long-template',version:'v9',enabled:true,execution_mode:'docker',integration_mode:'direct',image:'registry/'+ 'a'.repeat(700)+':v1',command:['python3','/app/'+ 'b'.repeat(700)+'.py'],protocols:['http'],runtime:{environment:{LONG:'c'.repeat(900)},assets:{data:'/data/'+ 'd'.repeat(500)},mounts:{}}};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async r=>{const p=new URL(r.request().url()).pathname; if(r.request().method()!=='GET')writes++;await r.fulfill({json:p.endsWith('/auth/me')?{username:'admin',role:'admin'}:p.endsWith('/client-templates')?[template]:[]})});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();
 const view=page.getByRole('region',{name:'模板配置详情'});await expect(view.getByLabel('版本',{exact:true})).toHaveValue('v9');await expect(view.getByLabel('Docker 镜像')).toBeDisabled();await expect(view.getByLabel('固定参数 1')).toHaveValue(template.command[1]);await expect(view.getByRole('button',{name:'注册此版本'})).toHaveCount(0);
 await view.getByText('原始配置 JSON',{exact:true}).click();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(writes).toBe(0);
 await page.screenshot({path:`test-results/config-readonly-${width}.png`,fullPage:true});
 await view.getByRole('button',{name:'关闭配置'}).click();await expect(view).toHaveCount(0);
});
test('my tasks only lists records and keeps filtering',async({page})=>{
 const paths:string[]=[];await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async r=>{paths.push(r.request().url());await r.fulfill({json:r.request().url().endsWith('/auth/me')?{username:'user',role:'user'}:[]})});await page.goto('/');
 await expect(page.getByRole('heading',{name:'任务记录',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'提交评测',exact:true})).toHaveCount(0);await expect(page.getByLabel('任务名称',{exact:true})).toHaveCount(0);
 await page.getByLabel('任务状态',{exact:true}).selectOption('FAILED');await expect.poll(()=>paths.some(p=>p.includes('status=FAILED'))).toBe(true);
});
test('machine configuration mounts on demand and preserves unsaved values between tabs',async({page})=>{
 const requests:string[]=[];await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async r=>{const p=new URL(r.request().url()).pathname;requests.push(p);let data:any=[];
 if(p.endsWith('/auth/me'))data={username:'admin',role:'admin'};
 if(p.endsWith('/admin/nodes'))data=[{id:'n',node_name:'node',roles:['client','server'],enabled:true,status:'online',management_revision:0,max_concurrent:4,cpu_capacity:32,ram_gb_capacity:64,gpu_count:2,held_executions:0}];
 if(p.endsWith('/storage'))data={revision:0,config:null,slots:[],all_applied:false};
 await r.fulfill({json:data});});await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();
 await page.getByLabel('CPU 核数',{exact:true}).fill('24');expect(requests.some(x=>x.endsWith('/storage'))).toBe(false);
 await page.getByRole('tab',{name:'工作目录与资产',exact:true}).click();await expect.poll(()=>requests.some(x=>x.endsWith('/storage'))).toBe(true);
 await expect(page.getByLabel('CPU 核数',{exact:true})).toBeHidden();await page.getByRole('tab',{name:'基本配置',exact:true}).click();await expect(page.getByLabel('CPU 核数',{exact:true})).toHaveValue('24');
 await page.getByRole('tab',{name:'基本配置',exact:true}).focus();await page.keyboard.press('ArrowRight');await expect(page.getByRole('tab',{name:'节点 Agent',exact:true})).toBeFocused();
});
test('wizard and results have distinct entries and batch shows saved parameters',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 const c={job_id:'j',case_id:'case-1',task_name:'demo',status:'PENDING',run_id:'r',attempt:1,episodes:2,seed:3,progress:{completed:0,successes:0},reason_codes:[],waiting:null,config:{pool_name:'pool',client_name:'client',client_version:'1'}};
 await page.route('**/api/v1/**',async r=>{const p=new URL(r.request().url()).pathname;let data:any=[];
 if(p.endsWith('/auth/me'))data={username:'user',role:'user'};
 if(p.endsWith('/batches'))data=[{id:'b',title:'业务验证批次',status:'PENDING',total:1,succeeded:0,finished:0,total_episodes:2,cases:[c]}];
 if(p.endsWith('/batches/b/monitor'))data={id:'b',title:'业务验证批次',max_concurrent:2,total:1,finished:0,total_episodes:2,cases:[c],orchestrated:true,summary:{states:{PENDING:1},completed_episodes:0,successes:0,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:null}};
 if(p.endsWith('/orchestrations/b'))data={cases:[{case_id:'case-1',endpoint:'http://service:9000',waiting_service:false}],orchestration:{service_mode:'managed',stop_after_batch:true,definitions:[{key:'model',mode:'shared',parameters:{model:'test-model'},resources:{cpus:2},template:{name:'server',version:'v1'}}],services:[{id:'s',name:'model-server',state:'HEALTHY',endpoint:'http://service:9000',revision:1}]}};
 if(p.endsWith('/jobs/j'))data={episodes:2,client_spec:{task_name:'demo',seed:3,parameters:{scene:'kitchen'},resources:{cpus:1},template:{name:'client',version:'1'}},attempts:[]};
 await r.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'业务评测向导',exact:true}).click();await expect(page.getByRole('region',{name:'集合运行向导'})).toBeVisible();await expect(page.getByRole('heading',{name:'批次进度'})).toBeHidden();await expect(page.getByRole('tab',{name:'客户端注册'})).toBeHidden();
 await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();await expect(page.getByRole('region',{name:'集合运行向导'})).toBeHidden();await page.getByRole('button',{name:'查看批次',exact:true}).click();
 await expect(page.getByRole('heading',{name:'业务验证批次',exact:true})).toBeVisible();await page.getByRole('button',{name:'运行参数',exact:true}).click();await expect(page.getByRole('region',{name:'本次运行参数'})).toContainText('kitchen');await page.getByRole('button',{name:'服务端与关联',exact:true}).click();await expect(page.getByRole('heading',{name:'model-server · 已就绪'})).toBeVisible();await page.getByText('model · 共享访问 · 服务端运行参数',{exact:true}).click();await expect(page.getByText(/"model": "test-model"/)).toBeVisible();
 await page.reload();await expect(page.getByRole('heading',{name:'评测运行与结果',exact:true})).toBeVisible();await page.getByRole('button',{name:'返回批次列表'}).click();await expect(page.getByRole('button',{name:'查看批次',exact:true})).toBeVisible();await expect(page.getByRole('region',{name:'集合运行向导'})).toBeHidden();
});
