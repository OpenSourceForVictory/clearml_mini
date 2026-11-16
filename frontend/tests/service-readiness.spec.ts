import {test,expect} from '@playwright/test';
test('WebSocket readiness registration view and copy preserve configuration',async({page})=>{
 let sent:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(path==='auth/me')data={username:'admin',role:'admin'};
 if(path==='admin/server-templates'){
 if(route.request().method()==='POST'){sent=route.request().postDataJSON();data={id:'server',...sent};}
 else if(sent)data=[{id:'server',enabled:true,...sent}];
 }
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('button',{name:'注册模板',exact:true}).click();
 await page.getByLabel('模板名称',{exact:true}).fill('ws-service');await page.getByLabel('版本',{exact:true}).fill('v1');await page.getByLabel('Docker 镜像').fill('service:v1');
 await page.getByRole('checkbox',{name:/自定义就绪检查/}).check();await page.getByRole('button',{name:'WS 健康消息样例',exact:true}).click();
 await page.getByLabel('健康端口',{exact:true}).selectOption('separate');await page.getByLabel('程序健康端口（容器内部）').fill('9001');await page.getByLabel('首次就绪等待（秒）').fill('600');
 await page.getByRole('button',{name:'注册此版本',exact:true}).click();await expect.poll(()=>sent?.readiness?.kind).toBe('websocket');
 expect(sent.readiness).toMatchObject({port:9001,startup_timeout:600,mode:'message',expect:{type:'json_equals',pointer:'/ready',value:true}});
 await page.getByRole('button',{name:'查看配置',exact:true}).click();const detail=page.getByRole('region',{name:'模板配置详情'});
 await expect(detail.getByLabel('就绪检查方式')).toHaveValue('websocket');await expect(detail.getByLabel('首次就绪等待（秒）')).toHaveValue('600');await expect(detail.getByLabel('就绪检查方式')).toBeDisabled();
 await detail.getByRole('button',{name:'以此配置创建新版本'}).click();await expect(page.getByLabel('首次就绪等待（秒）')).toHaveValue('600');await page.getByLabel('就绪检查方式').selectOption('tcp');await expect(page.getByLabel('探测消息',{exact:true})).toHaveCount(0);
});
