import {test,expect} from '@playwright/test';
for(const kind of ['客户端模板','服务端模板'])test('host directory save and copy '+kind,async({page})=>{
 const saved:any[]=[];await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async r=>{const path=new URL(r.request().url()).pathname;let data:any=[];
 if(path.endsWith('/auth/me'))data={username:'admin',role:'admin'};
 if(path.endsWith('/client-templates')||path.endsWith('/admin/server-templates'))data=saved.map(t=>({id:'new',enabled:true,...t}));
 if(r.request().method()==='POST'){const body=r.request().postDataJSON();saved.push(body);data={id:'new',...body};}
 await r.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();await page.getByRole('tab',{name:kind,exact:true}).click();await page.getByRole('tab',{name:'注册新模板',exact:true}).click();
 await page.getByLabel('执行方式',{exact:true}).selectOption('host');await page.getByLabel('模板名称',{exact:true}).fill('cwd-demo');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('节点环境名称').fill('simulation');await page.getByLabel('业务执行目录').fill('eval');
 await page.getByLabel('宿主机环境来源').selectOption('login');if(kind==='客户端模板')await page.getByLabel('客户端回合启动').selectOption('per_episode');
 await page.getByLabel('执行方式',{exact:true}).selectOption('docker');await page.getByLabel('执行方式',{exact:true}).selectOption('host');await expect(page.getByLabel('业务执行目录')).toHaveValue('eval');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>saved.length).toBe(1);expect(saved[0].host_runtime.working_directory).toBe('eval');expect(saved[0].host_runtime.environment_source).toBe('login');expect(saved[0].host_runtime.episode_launch).toBe(kind==='客户端模板'?'per_episode':'whole_task');
 await page.getByRole('button',{name:'查看配置',exact:true}).click();const details=page.getByRole('region',{name:'模板配置详情'});await expect(details.getByLabel('业务执行目录')).toHaveValue('eval');await expect(details.getByLabel('业务执行目录')).toBeDisabled();await expect(details.getByLabel('宿主机环境来源')).toHaveValue('login');await details.getByRole('button',{name:'以此配置创建新版本'}).click();await expect(page.getByLabel('业务执行目录')).toHaveValue('eval');await expect(page.getByLabel('宿主机环境来源')).toHaveValue('login');
});
