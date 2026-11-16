import {test,expect} from '@playwright/test';

test.skip(!process.env.SUITE_LIVE_TEST_API,'requires an explicitly configured disposable API');
const base=process.env.SUITE_LIVE_TEST_API||'';
test('real API persists client/server Docker/host template contracts',async({page,request})=>{
  const login=await request.post(base+'/api/v1/auth/login',{data:{username:'admin',password:'suite-test-password'}});
  expect(login.ok()).toBeTruthy();const token=(await login.json()).access_token;
  const headers={Authorization:'Bearer '+token},prefix='template-live-'+Date.now();
  await page.addInitScript(t=>sessionStorage.setItem('eval-token',t),token);
  await page.goto(base);await page.getByRole('button',{name:'管理设置',exact:true}).click();
  await page.getByRole('tab',{name:'运行模板管理',exact:true}).click();
  for(const kind of ['客户端','服务端']){
    await page.getByRole('tab',{name:kind+'模板',exact:true}).click();
    for(const mode of ['docker','host']){
      const name=prefix+'-'+kind+'-'+mode;
      await page.getByRole('button',{name:'注册模板',exact:true}).click();
      await page.getByLabel('模板名称',{exact:true}).fill(name);await page.getByLabel('版本',{exact:true}).fill('1');
      await page.getByLabel('执行方式',{exact:true}).selectOption(mode);
      if(mode==='docker')await page.getByLabel('Docker 镜像').fill('contract-test:1');
      else await page.getByLabel('节点环境名称').fill('simulation');
      if(kind==='客户端')await page.getByLabel('协议接入方式').selectOption('sdk-command');
      await page.getByLabel('启用类型化业务参数').check();await page.getByRole('button',{name:'添加业务字段'}).click();
      await page.getByLabel('字段名称',{exact:true}).fill('scene');
      await page.getByLabel('设置默认值',{exact:true}).check();await page.getByLabel('默认值',{exact:true}).fill('lab');
      const route=kind==='客户端'?'client-templates':'server-templates';
      const reply=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/admin/'+route));
      await page.getByRole('button',{name:'注册此版本'}).click();const response=await reply;
      expect(response.status(),await response.text()).toBe(201);
      const saved=await response.json();expect(saved).toMatchObject({name,execution_mode:mode,parameter_schema:{fields:{scene:{default:'lab'}}}});
      await expect(page.getByRole('button',{name:'注册此版本'})).toHaveCount(0);
      const listed=await request.get(base+'/api/v1/'+(kind==='客户端'?'':'admin/')+route,{headers});
      expect((await listed.json()).find((v:any)=>v.id===saved.id)).toMatchObject({name,execution_mode:mode});
    }
  }
});
