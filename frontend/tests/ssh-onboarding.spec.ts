import {test,expect} from '@playwright/test';
for (const nativeUUID of [true,false]) test('SSH wizard identity, installation and recovery; native UUID='+nativeUUID,async({page})=>{
 if(!nativeUUID)await page.addInitScript(()=>Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true}));
 let operation:any=null;let check:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/ssh-onboarding')data={available:true,operations:operation?[operation]:[]};
 if(p==='admin/ssh-onboarding/check'){check=route.request().postDataJSON();operation={id:'op',state:'NEEDS_HOST_KEY',stage:'HOST_KEY',form:{node_name:'new-node',host:'host',mode:check.mode,npu_sharing:check.npu_sharing,gpu_sharing:check.gpu_sharing},host_key:{fingerprint:'SHA256:'+'A'.repeat(43),algorithm:'ssh-ed25519'}};data=operation;}
 if(p==='admin/ssh-onboarding/op/trust'){expect(route.request().postDataJSON().fingerprint).toBe('SHA256:'+'A'.repeat(43));operation={...operation,state:'READY',stage:'ENVIRONMENT',plan:{runner_image_help:{image:'mini-eval-runner:dev',present:false,runtime_verified:false,command:'docker build -f runner/Dockerfile -t mini-eval-runner:dev .',context:'在完整节点安装包解压目录执行'},runner_image_ready:false,cgroup_mode:'v1-or-hybrid',business_user:'root',business_uid:0,business_gid:0,workspace_paths:[{index:0,workspace_root:'/srv/eval/new-node/npu0',assets_root:'/srv/eval/new-node/assets',cache_root:'/srv/eval/new-node/cache'}],accelerator_type:'npu',python_version:'3.11',home:'/home/test',free_disk_gb:100,gpus:[{index:0,name:'RTX'}],selected_gpus:[0],existing:false,services:[],issues:[]}};data=operation;}
 if(p==='admin/ssh-onboarding/op/confirm'){operation={...operation,state:'VERIFYING',stage:'RECONNECT',reconnect:{slots:[{id:'s',gpu_index:0,ready:true}],updater_ready:false}};data=operation;}
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'注册新机器',exact:true}).click();
 await page.getByLabel('SSH 机器名称').fill('new-node');await page.getByLabel('SSH 主机地址').fill('192.168.8.157');await expect(page.getByLabel('SSH 用户名')).toHaveValue('root');await expect(page.getByLabel('GPU 设备共享批准')).toHaveValue('disabled');expect(await page.getByLabel('GPU 设备共享批准').evaluate((element)=>Boolean(element.compareDocumentPosition(document.querySelector('input[type="password"]'))&Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);await page.getByRole('checkbox',{name:'宿主机'}).check();await page.getByLabel('加速设备类型').selectOption('npu');await expect(page.getByLabel('NPU 设备共享批准')).toHaveValue('disabled');await page.getByLabel('NPU 设备共享批准').selectOption('enabled');await page.getByLabel('SSH 密码',{exact:true}).fill('private-password');await page.getByLabel('节点访问平台的 API 地址').fill('http://192.168.8.151:18000');
 await page.getByLabel('工作目录根路径（可选）').fill('/srv/eval');await page.getByRole('button',{name:'检查连接与环境',exact:true}).click();await expect.poll(()=>check?.password).toBe('private-password');expect(check.request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);expect(check.username).toBe('root');expect(check.business_user).toBe('root');expect(check.workspace_root).toBe('/srv/eval');expect(check.mode).toBe('both');expect(check.accelerator_type).toBe('npu');expect(check.npu_sharing).toBe('enabled');expect(check.gpu_sharing).toBe('preserve');await expect(page.getByLabel('SSH 密码',{exact:true})).toHaveValue('');
 await expect(page.getByRole('button',{name:'确认计划并自动纳管'})).toHaveCount(0);
 await page.getByRole('button',{name:'确认此主机指纹并检查环境'}).click();await expect(page.getByText('检查通过，等待安装',{exact:true})).toBeVisible();
 await expect(page.getByText('执行环境：宿主机、容器。')).toBeVisible();
 await expect(page.getByText('NPU 共享批准：本次开启。资源池共享开关需另行设置。')).toBeVisible();await expect(page.getByText('cgroup 层级：v1-or-hybrid；以实际资源限制验证结果为准。')).toBeVisible();await expect(page.getByText('平台 runner 镜像尚未准备，不阻止纳管',{exact:false})).toBeVisible();await expect(page.getByText('管理身份：root · 宿主机业务：root · UID/GID 0/0')).toBeVisible();await expect(page.getByText('NPU 0：工作 /srv/eval/new-node/npu0；资产 /srv/eval/new-node/assets；缓存 /srv/eval/new-node/cache')).toBeVisible();await expect(page.getByText('docker build -f runner/Dockerfile -t mini-eval-runner:dev .',{exact:true})).toBeVisible();await page.getByRole('button',{name:'确认计划并自动纳管'}).click();await expect(page.getByText('升级执行器：等待回连',{exact:true})).toBeVisible();
 await page.reload();await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'注册新机器',exact:true}).click();await expect(page.getByText('等待组件回连',{exact:true})).toBeVisible();
 operation={...operation,state:'SUCCEEDED',stage:'COMPLETE'};await expect(page.getByText('纳管完成。可在资源池管理中分配 GPU。',{exact:true})).toBeVisible({timeout:7000});
});

test('executor disconnect disables checks and reconnect preserves form',async({page})=>{
 let ready=true;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');
  if(p==='admin/ssh-onboarding'){
   await route.fulfill({status:ready?200:503,json:ready?{available:true,operations:[]}:{detail:'SSH onboarding executor is unavailable'}});return;
  }
  await route.fulfill({json:p==='auth/me'?{username:'admin',role:'admin'}:[]});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'注册新机器',exact:true}).click();
 await expect(page.getByRole('button',{name:'检查连接与环境',exact:true})).toBeEnabled();
 await page.getByLabel('SSH 机器名称').fill('keep-my-node');
 ready=false;await page.getByRole('button',{name:'重新检测纳管服务'}).click();
 await expect(page.getByRole('button',{name:'检查连接与环境',exact:true})).toBeDisabled();
 await expect(page.getByText('暂时无法连接纳管服务，平台正在自动重新检测；已填写信息保留。')).toBeVisible();
 ready=true;
 await expect(page.getByRole('button',{name:'检查连接与环境',exact:true})).toBeEnabled({timeout:7000});
 await expect(page.getByLabel('SSH 机器名称')).toHaveValue('keep-my-node');
 await expect(page.getByText('暂时无法连接纳管服务，平台正在自动重新检测；已填写信息保留。')).toHaveCount(0);
});

test('existing custom layout shows preserved path and rejects incomplete layout',async({page})=>{
 let issues:string[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');
  const operation={id:'legacy',state:issues.length?'CHECK_FAILED':'READY',stage:'ENVIRONMENT',form:{node_name:'legacy',host:'node',npu_sharing:'preserve'},plan:{accelerator_type:'npu',configs:[{gpu_index:0,npu_sharing:true}],installation:{layout:{app_dir:'/opt/mini-eval-node/app',state_dir:'/var/lib/mini-eval-node/state/updater'}},python_version:'3.12',home:'/root',free_disk_gb:10,gpus:[{index:0,name:'Ascend'}],selected_gpus:[0],existing:true,business_user:'HwHiAiUser',services:[{scope:'system',name:'mini-eval-worker-npu0.service'}],issues}};
  await route.fulfill({json:p==='auth/me'?{username:'admin',role:'admin'}:p==='admin/ssh-onboarding'?{available:true,operations:[operation]}:[]});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'注册新机器',exact:true}).click();
 await expect(page.getByText('已识别原安装：/opt/mini-eval-node/app',{exact:false})).toBeVisible();await expect(page.getByText('NPU 共享批准：保留原设置。原配置：NPU 0 允许。资源池共享开关需另行设置。')).toBeVisible();
 await expect(page.getByText('管理身份：root · 宿主机业务：HwHiAiUser · 沿用原批准账号')).toBeVisible();
 issues=['existing_layout_incomplete'];await page.getByRole('button',{name:'重新检测纳管服务'}).click();
 await expect(page.getByText('旧安装布局遗漏或重复设备配置，不能覆盖安装')).toBeVisible();
 await expect(page.getByRole('button',{name:'确认计划并自动纳管'})).toHaveCount(0);
});

test('timed out installation resumes verification without SSH credentials',async({page})=>{
 let verified=false;
 let operation:any={id:'resume',state:'FAILED',stage:'RECONNECT',error:'component_reconnect_timeout',form:{node_name:'resume-node',host:'host'},verification_available:true,verification_timeout_seconds:300,reconnect:{slots:[{id:'slot',gpu_index:0,ready:true,probe_ready:false}],node_agent_ready:true,updater_ready:false}};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:'admin',role:'admin'};
  if(p==='admin/ssh-onboarding')data={available:true,operations:[operation]};
  if(p==='admin/ssh-onboarding/resume/verify'){
   expect(route.request().headers()['authorization']).toBe('Bearer test');
   expect(route.request().postData()).toBeNull();
   verified=true;operation={...operation,state:'SUCCEEDED',stage:'COMPLETE',error:null};data=operation;
  }
  if(p==='admin/ssh-onboarding/check'||p.endsWith('/confirm'))throw Error('must not reinstall');
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();
 await page.getByRole('button',{name:'注册新机器',exact:true}).click();
 await expect(page.getByText('探测依赖未就绪',{exact:false})).toBeVisible();
 await expect(page.getByText('每次回连验收最多等待 300 秒。',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'继续回连验收',exact:true}).click();
 await expect(page.getByText('纳管完成。可在资源池管理中分配 GPU。',{exact:true})).toBeVisible();
 expect(verified).toBeTruthy();
});
