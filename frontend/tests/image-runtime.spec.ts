import {test,expect} from '@playwright/test';

test('member registers an image, keeps drafts across tabs and submits allowed variables',async({page})=>{
  const templates:any[]=[];let batch:any;
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let body:any=[];
    if(path==='auth/me')body={id:'user',username:'user',role:'user'};
    if(path==='client-templates'){
      if(route.request().method()==='POST'){body={id:'image-1',enabled:true,...route.request().postDataJSON()};templates.push(body);}else body=templates;
    }
    if(path==='suites/manage')body={items:[],total:0};
    if(path==='pools')body=[{id:'pool',name:'gpu-pool'}];
    if(path==='batches/preflight')body={checked_at:new Date().toISOString(),can_submit:true,status:'ready',cases:route.request().postDataJSON().cases,nodes:[],reasons:[]};
    if(path==='batches'&&route.request().method()==='POST'){batch=route.request().postDataJSON();body={id:'b1',...batch};}
    await route.fulfill({json:body});
  });
  await page.goto('/');await page.getByRole('button',{name:'CS 评测批次',exact:true}).click();
  await page.getByRole('tab',{name:'客户端注册',exact:true}).click();
  await page.getByLabel('镜像客户端名称',{exact:true}).fill('My simulator');
  await page.getByLabel('客户端镜像地址',{exact:true}).fill('my-client:1');
  await page.getByRole('tab',{name:'评测集合运行',exact:true}).click();
  await page.getByRole('tab',{name:'客户端注册',exact:true}).click();
  await expect(page.getByLabel('镜像客户端名称',{exact:true})).toHaveValue('My simulator');
  await page.getByLabel('启动命令 JSON 数组',{exact:true}).fill('["python3","/app/evaluate.py"]');
  const runtime=await page.getByLabel('运行配置 JSON',{exact:true}).inputValue();
  await page.getByLabel('运行配置 JSON',{exact:true}).fill(runtime);
  await page.getByRole('button',{name:'注册镜像客户端',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('已注册');
  expect(templates[0].runtime.assets.robotwin).toBe('/data/robotwin');
  await expect(page.getByRole('button',{name:'管理设置',exact:true})).toHaveCount(0);
  await page.getByRole('tab',{name:'评测集合运行',exact:true}).click();
  await page.getByLabel('批次名称',{exact:true}).fill('run');
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByLabel('客户端模板',{exact:true}).selectOption('image-1');
  await page.getByLabel('推理服务地址',{exact:true}).fill('http://inference:9000');
  await page.getByLabel('启动变量覆盖 JSON',{exact:true}).fill('{"LOG_LEVEL":"debug"}');
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByLabel('客户端资源池',{exact:true}).selectOption('pool');
  await page.getByRole('button',{name:'检查并确认',exact:true}).click();
  await page.getByRole('button',{name:'提交评测批次',exact:true}).click();
  await expect.poll(()=>batch?.client.environment).toEqual({LOG_LEVEL:'debug'});
});

test('node runtime preserves drafts, reports missing assets and prevents stale saves',async({page})=>{
  let data:any={revision:1,config:{images:['my:1'],assets:{robotwin:'/srv/assets'}},slots:[{slot_id:'s',gpu_index:0,ready:true,report:{applied_revision:1,effective:{images:['my:1'],assets:{robotwin:'/srv/assets'}},images:{'my:1':{ready:true}},assets:{robotwin:{ready:false,error:'asset directory missing'}}}}]};
  let submitted:any;
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let body:any=[];
    if(path==='auth/me')body={id:'admin',username:'admin',role:'admin'};
    if(path==='admin/nodes')body=[{id:'node',node_name:'host',enabled:true,max_concurrent:1,cpu_capacity:4,ram_gb_capacity:8}];
    if(path==='admin/health')body={runner_image:'runner:1'};
    if(path==='admin/nodes/node/storage')body={revision:0,config:null,slots:[]};
    if(path==='admin/nodes/node/runtime'){
      if(route.request().method()==='PUT'){
        submitted=route.request().postDataJSON();
        if(submitted.expected_revision!==data.revision){await route.fulfill({status:409,json:{detail:'runtime configuration changed; reload before saving'}});return;}
        data={...data,revision:data.revision+1,config:submitted.config};
      }body=data;
    }await route.fulfill({json:body});
  });
  await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();
  await page.getByText('host 镜像与逻辑资产',{exact:true}).click();
  await expect(page.getByText(/asset directory missing/)).toBeVisible();
  await page.getByLabel('host 逻辑资产宿主映射',{exact:true}).fill('{"robotwin":"/new/assets"}');
  data={...data,revision:2};
  await expect(page.getByText('期望版本 2 · 平台配置',{exact:true})).toBeVisible({timeout:10000});
  await expect(page.getByLabel('host 逻辑资产宿主映射',{exact:true})).toHaveValue('{"robotwin":"/new/assets"}');
  await page.getByRole('button',{name:'保存镜像与资产配置',exact:true}).click();
  await expect(page.getByRole('alert').filter({hasText:'runtime configuration changed'})).toBeVisible();
  expect(submitted.expected_revision).toBe(1);
  await page.getByRole('button',{name:'重新载入运行配置（放弃草稿）',exact:true}).click();
  await page.getByRole('button',{name:'恢复本机运行配置',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('版本 3 已保存');
  expect(submitted).toEqual({expected_revision:2,config:null});
});
