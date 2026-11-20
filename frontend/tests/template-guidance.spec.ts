import {test,expect,Page} from '@playwright/test';
const template={id:'existing',name:'demo',version:'1',enabled:true,image:'demo:1',execution_mode:'docker',integration_mode:'sdk-command',command:['python3','/app/episode.py'],protocols:['http'],episode_reporting:true,endpoint_exclusive:true,platform_bindings:[{source:'task.task_name',target:'argv',name:'--task'}],parameter_schema:{version:1,fields:{scene:{type:'string',default:'room',overridable:true,binding:{target:'env',name:'SCENE'}}}},runtime:{environment:{LOG_LEVEL:'info'},overridable_env:[],assets:{dataset:'/datasets'},mounts:{shared:{source:'directories:shared',target:'/shared',read_only:true,required:true}}}};
async function setup(page:Page,fail=false,clientTemplate:any=template){
 const writes:any[]=[];await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(path==='auth/me')data={username:'admin',role:'admin'};
 if(path==='client-templates')data=[clientTemplate];
 if(path==='admin/server-templates')data=[{...template,id:'server',name:'server',execution_mode:'host',image:'',integration_mode:'direct',runtime:null,host_runtime:{environment_name:'demo',assets:['dataset'],environment:{},overridable_env:[]},port:8000,protocol:'http',health_type:'http',health_path:'/healthz',startup_timeout:120}];
 if(route.request().method()!=='GET'){writes.push({path,body:route.request().postDataJSON()});if(fail){await route.fulfill({status:400,json:{detail:'invalid example'}});return}data={id:'new',...route.request().postDataJSON()}}
 await route.fulfill({json:data})});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();return writes;
}
test('guidance, tabs and rejected copy preserve draft; failed registration keeps input',async({page})=>{
 const writes=await setup(page,true);const tab=page.getByRole('tab',{name:'注册新模板',exact:true});await tab.focus();await page.keyboard.press('Enter');
 await page.getByLabel('模板名称',{exact:true}).fill('draft');await page.getByLabel('版本',{exact:true}).fill('2');await page.getByLabel('Docker 镜像').fill('demo:1');
 await page.getByText('查看当前模式填写示例',{exact:true}).click();await expect(page.getByText('以下是配置写法示例',{exact:false})).toBeVisible();expect(writes).toHaveLength(0);
 await page.getByRole('tab',{name:'已注册模板',exact:true}).click();page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'复制新版本',exact:true}).click();
 await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('draft');
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('tab',{name:'客户端模板',exact:true}).click();await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('draft');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect(page.getByRole('alert')).toBeVisible();await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('draft');expect(writes).toHaveLength(1);
});
test('structured read-only view and copy retain mappings, assets and protocol restrictions',async({page})=>{
 const writes=await setup(page);await page.getByRole('button',{name:'查看配置',exact:true}).click();const view=page.getByRole('region',{name:'模板配置详情'});
 await expect(view.getByLabel('字段名称',{exact:true})).toBeVisible();await expect(view.locator('input[value="/datasets"]')).toBeVisible();expect(writes).toHaveLength(0);
 await page.screenshot({path:'test-results/template-config-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'以此配置创建新版本'}).click();await page.getByLabel('版本',{exact:true}).fill('2');await page.getByRole('button',{name:'注册此版本'}).click();
 await expect.poll(()=>writes.length).toBe(1);expect(writes[0].body).toMatchObject({platform_bindings:template.platform_bindings,parameter_schema:template.parameter_schema,endpoint_exclusive:true,protocols:['http'],runtime:{assets:template.runtime.assets,mounts:template.runtime.mounts}});
 await expect(page.getByRole('tab',{name:'已注册模板',exact:true})).toHaveAttribute('aria-selected','true');
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();await expect(page.getByRole('region',{name:'模板配置详情'}).getByLabel('健康检查路径')).toBeVisible();
});
test('choose platform and business parameters; reject duplicate targets before submit',async({page})=>{
 const writes=await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.getByLabel('模板名称',{exact:true}).fill('mapped');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('Docker 镜像').fill('demo:1');
 await page.getByLabel('启用类型化业务参数').check();await page.getByRole('button',{name:'添加业务字段'}).click();await page.getByLabel('字段名称',{exact:true}).fill('scene');
 await page.getByLabel('选择要传递的参数').selectOption('task.task_name');await page.getByRole('button',{name:'添加参数映射'}).click();
 await page.getByLabel('选择要传递的参数').selectOption({label:'scene'});await page.getByRole('button',{name:'添加参数映射'}).click();
 const rows=page.getByRole('group',{name:/^参数映射 /});await rows.nth(1).getByLabel('程序接收的参数名').fill('--task');await page.getByRole('button',{name:'注册此版本'}).click();await expect(page.getByRole('alert')).toContainText('映射目标重复');expect(writes).toHaveLength(0);
 await rows.nth(1).getByLabel('程序接收的参数名').fill('--scene');await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.platform_bindings).toEqual([{source:'task.task_name',target:'argv',name:'--task',encoding:'literal'}]);expect(writes[0].body.parameter_schema.fields.scene.binding).toMatchObject({target:'argv',name:'--scene'});
});
test('existing business binding remains selectable and opens its mapping without duplication',async({page})=>{
 const writes=await setup(page);await page.getByRole('button',{name:'复制新版本',exact:true}).click();await page.getByLabel('版本',{exact:true}).fill('2');
 const picker=page.getByLabel('选择要传递的参数');await picker.selectOption({label:'scene（已映射）'});
 await page.getByRole('button',{name:'查看已有映射'}).click();
 const mapped=page.getByRole('group',{name:/参数映射 .*业务参数：scene/});await expect(mapped).toBeFocused();
 await mapped.getByLabel('程序接收的参数名').fill('SCENE_NAME');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.parameter_schema.fields.scene.binding).toMatchObject({target:'env',name:'SCENE_NAME'});
 expect(writes[0].body.platform_bindings).toHaveLength(1);
});
test('business mapping picker explains why no business fields are available',async({page})=>{
 await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();
 await expect(page.getByText('要映射业务参数，请先在上方“业务参数”中启用类型化业务参数并添加字段。')).toBeVisible();
 await page.getByLabel('启用类型化业务参数').check();
 await expect(page.getByText('请先在上方添加业务字段并填写字段名称。')).toBeVisible();
 await page.getByRole('button',{name:'添加业务字段'}).click();await page.getByLabel('字段名称',{exact:true}).fill('scene');
 await expect(page.getByLabel('选择要传递的参数').locator('option',{hasText:'scene'})).toHaveCount(1);
});
test('directory selection and host/server explanations work at narrow width',async({page})=>{
 await setup(page);await page.setViewportSize({width:390,height:844});await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.getByRole('button',{name:'添加目录挂载'}).click();
 await page.getByLabel('挂载 1 来源类型').selectOption('assets');await page.getByLabel('挂载 1 逻辑名称').fill('dataset');await expect(page.getByLabel('只读挂载 1')).toBeDisabled();
 await page.screenshot({path:'test-results/template-register-mobile.png',fullPage:true});
 await page.getByLabel('执行方式',{exact:true}).selectOption('host');await expect(page.getByText('宿主机：先在机器管理中配置',{exact:false})).toBeVisible();await expect(page.getByRole('button',{name:'添加资产引用'})).toBeVisible();
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await expect(page.getByText('服务端持续运行并接受客户端请求',{exact:false})).toBeVisible();
 await expect(page.getByLabel('协议接入方式')).toBeHidden();
});

test('complete examples load a draft without registering automatically',async({page})=>{
 const writes=await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.locator('summary:visible').filter({hasText:'从完整样例开始'}).click();
 await page.getByLabel('运行样例',{exact:true}).filter({visible:true}).selectOption('client-docker-sdk-command');await page.getByRole('button',{name:'载入样例草稿'}).click();
 await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('echo-client-docker-sdk-command');await expect(page.getByLabel('固定参数 1',{exact:true})).toHaveValue('/app/episode.py');expect(writes).toHaveLength(0);
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);expect(writes[0].body.parameter_schema.fields.scene.default).toBe('room');
});

test('environment rows preserve mode drafts, empty defaults, override-only entries and reject duplicates',async({page})=>{
 const writes=await setup(page);await page.getByRole('button',{name:'复制新版本',exact:true}).click();await page.getByLabel('版本',{exact:true}).fill('2');
 await expect(page.getByLabel('变量名称 1',{exact:true})).toHaveValue('LOG_LEVEL');await page.getByLabel('变量值 1',{exact:true}).fill('');await page.getByLabel('允许提交时覆盖 1',{exact:true}).check();
 await page.getByRole('button',{name:'添加环境变量',exact:true}).click();await page.getByLabel('变量名称 2',{exact:true}).fill('LOG_LEVEL');await page.getByRole('button',{name:'注册此版本'}).click();await expect(page.getByRole('alert')).toContainText('环境变量名称重复');expect(writes).toHaveLength(0);
 await page.getByLabel('变量名称 2',{exact:true}).fill('OPTIONAL_VALUE');await page.getByLabel('提供默认值 2',{exact:true}).uncheck();await page.getByLabel('允许提交时覆盖 2',{exact:true}).check();
 await page.getByLabel('执行方式',{exact:true}).selectOption('host');await expect(page.getByLabel('变量名称 1',{exact:true})).toHaveCount(0);await page.getByRole('button',{name:'添加环境变量',exact:true}).click();await page.getByLabel('变量名称 1',{exact:true}).fill('HOST_ONLY');
 await page.getByLabel('执行方式',{exact:true}).selectOption('docker');await expect(page.getByLabel('变量名称 1',{exact:true})).toHaveValue('LOG_LEVEL');await expect(page.getByLabel('变量值 1',{exact:true})).toHaveValue('');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);expect(writes[0].body.runtime.environment).toEqual({LOG_LEVEL:''});expect(writes[0].body.runtime.overridable_env).toEqual(['LOG_LEVEL','OPTIONAL_VALUE']);
});

test('launch preview follows fixed args, mapping names and targets without persisting placeholders',async({page})=>{
 const writes=await setup(page);await page.getByRole('button',{name:'复制新版本',exact:true}).click();await page.getByLabel('版本',{exact:true}).fill('2');
 const preview=page.getByRole('region',{name:'实时启动预览'});await expect(preview).toContainText('--task ⟪task.task_name⟫');await expect(preview).toContainText('SCENE ⟪business.scene⟫');
 await page.getByLabel('固定参数 1',{exact:true}).fill('/app/path with spaces.py');await expect(preview).toContainText('"/app/path with spaces.py"');
 const rows=page.getByRole('group',{name:/^参数映射 /});await rows.first().getByLabel('程序接收的参数名').fill('--simulation');await expect(preview).toContainText('--simulation ⟪task.task_name⟫');await expect(preview).not.toContainText('--task');
 await rows.first().getByLabel('传递到',{exact:true}).selectOption('env');await rows.first().getByLabel('程序接收的参数名').fill('SIM_TASK');await expect(page.getByLabel('动态环境变量预览',{exact:true})).toContainText('SIM_TASK ⟪task.task_name⟫');await expect(page.getByLabel('启动命令预览',{exact:true})).not.toContainText('SIM_TASK');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);expect(writes[0].body.command).toEqual(['python3','/app/path with spaces.py']);expect(writes[0].body.platform_bindings[0]).toMatchObject({target:'env',name:'SIM_TASK'});
});

test('conditional flag and repeated array previews reflect business encodings',async({page})=>{
 await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.getByLabel('启用类型化业务参数').check();await page.getByRole('button',{name:'添加业务字段'}).click();await page.getByLabel('字段名称',{exact:true}).fill('verbose');await page.getByLabel('参数类型',{exact:true}).selectOption('boolean');await page.getByLabel('传递方式',{exact:true}).selectOption('argv');await page.getByLabel('绑定名称',{exact:true}).fill('--verbose');await page.getByLabel('传递编码',{exact:true}).selectOption('flag');
 const preview=page.getByRole('region',{name:'实时启动预览'});await expect(preview).toContainText('--verbose：仅当 ⟪business.verbose⟫ 为 true');
 await page.getByLabel('参数类型',{exact:true}).selectOption('array');await page.getByLabel('字段名称',{exact:true}).fill('scenes');await page.getByLabel('传递方式',{exact:true}).selectOption('argv');await page.getByLabel('绑定名称',{exact:true}).fill('--scene');await expect(preview).toContainText('business.scenes · JSON 编码');await page.getByLabel('传递编码',{exact:true}).selectOption('repeat');await expect(preview).toContainText('--scene ⟪business.scenes 的元素⟫：逐项重复');
});

test('template resource contract saves defaults, minimums and locked accelerator settings',async({page})=>{
 const writes=await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.getByLabel('模板名称',{exact:true}).fill('resource-template');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('Docker 镜像').fill('demo:1');
 await page.getByLabel('设置模板资源要求',{exact:true}).check();await page.getByLabel('默认 CPU 核数',{exact:true}).fill('4');await page.getByLabel('默认内存 GiB',{exact:true}).fill('8');await page.getByLabel('加速设备类型',{exact:true}).selectOption('npu');await page.getByLabel('加速设备使用方式',{exact:true}).selectOption('shared');await page.getByLabel('默认设备内存 GiB',{exact:true}).fill('12');
 await page.getByText('最低要求与覆盖权限',{exact:true}).click();await page.getByLabel('最低内存 GiB',{exact:true}).fill('4');await page.getByLabel('最低设备内存 GiB',{exact:true}).fill('8');await page.getByLabel('允许修改CPU 核数',{exact:true}).uncheck();
 await page.getByLabel('执行方式',{exact:true}).selectOption('host');await expect(page.getByLabel('默认设备内存 GiB',{exact:true})).toHaveValue('12');await page.getByLabel('执行方式',{exact:true}).selectOption('docker');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);expect(writes[0].body.resource_requirements).toEqual({version:1,cpu:{minimum:1,default:4},memory:{minimum:4,default:8},accelerator:{type:'npu',mode:'shared',memory:{minimum:8,default:12}},allow_overrides:['ram_gb','requested_vram_gb']});
});
test('generic command controls and Docker directory are submitted',async({page})=>{
 const writes=await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();
 await expect(page.getByLabel('命令解析方式',{exact:true})).toHaveValue('exec');
 await page.getByText('通用命令示例：Python、Bash、可执行文件',{exact:true}).click();
 await expect(page.getByText('每一行数组是一种启动方式',{exact:false})).toBeVisible();
 await page.getByLabel('模板名称',{exact:true}).fill('native');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('Docker 镜像').fill('native:1');
 await page.getByLabel('容器业务执行目录',{exact:true}).fill('/app');await page.getByLabel('容器平台 Python',{exact:true}).fill('/opt/platform/bin/python');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.runtime).toMatchObject({command_mode:'exec',working_directory:'/app',interpreter:'/opt/platform/bin/python'});
});

for(const width of [1440,390])test(`template detail has independent view and restores trigger ${width}`,async({page})=>{
 await setup(page);await page.setViewportSize({width,height:844});
 const trigger=page.getByRole('button',{name:'查看配置',exact:true});await trigger.click();
 await expect(page.getByRole('tab',{name:'已注册模板',exact:true})).toBeHidden();
 await expect(page.getByRole('button',{name:'返回模板列表'})).toBeInViewport();
 await expect(page.getByRole('region',{name:'模板配置详情'})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.getByRole('button',{name:'返回模板列表'}).click();await expect(trigger).toBeFocused();
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();
 await expect(page.getByRole('button',{name:'返回模板列表'})).toBeInViewport();
 await expect(page.getByRole('region',{name:'模板配置详情'})).toContainText('server / 1');
});

for(const target of ['argv','env'])test(`hyphenated business names and ${target} mappings preserve names`,async({page})=>{
 const writes=await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();
 await page.getByLabel('模板名称',{exact:true}).fill('hyphen');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('Docker 镜像').fill('demo:1');
 await page.getByLabel('启用类型化业务参数').check();await page.getByRole('button',{name:'添加业务字段'}).click();
 await page.getByLabel('字段名称',{exact:true}).fill('task-config');
 await page.getByLabel('选择要传递的参数').selectOption({label:'task-config'});await page.getByRole('button',{name:'添加参数映射'}).click();
 const field=page.getByRole('group',{name:/^业务字段 1/});await field.getByLabel('传递方式').selectOption(target);
 await field.getByLabel('绑定名称').fill(target==='argv'?'--task-config':'task-config');
 await page.getByRole('button',{name:'添加环境变量',exact:true}).click();
 await page.getByLabel('变量名称 1',{exact:true}).fill('control-mode');await page.getByLabel('变量值 1',{exact:true}).fill('eef');
 await expect(page.getByText('变量名支持中划线',{exact:false})).toBeVisible();
 if(target==='env'){await field.getByLabel('绑定名称').fill('ASCEND_private-key');await page.getByRole('button',{name:'注册此版本'}).click();await expect(page.getByRole('alert')).toContainText('平台保留');expect(writes).toHaveLength(0);await field.getByLabel('绑定名称').fill('task-config');}
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.parameter_schema.fields['task-config'].binding).toMatchObject({target,name:target==='argv'?'--task-config':'task-config'});
 expect(writes[0].body.runtime.environment).toEqual({'control-mode':'eef'});
});


for(const launch of ['per_episode','whole_task'])test(`host episode error policy copy and launch switch ${launch}`,async({page})=>{
 const source={...template,execution_mode:'host',image:'',runtime:null,host_runtime:{environment_name:'demo',episode_launch:'per_episode',episode_error_policy:'continue',environment:{},assets:[],overridable_env:[]}};
 const writes=await setup(page,false,source);
 await page.getByRole('button',{name:'复制新版本',exact:true}).click();await page.getByLabel('版本',{exact:true}).fill('2');
 await expect(page.getByLabel('回合出错后的处理',{exact:true})).toHaveValue('continue');
 await page.getByLabel('客户端回合启动',{exact:true}).selectOption(launch);
 if(launch==='whole_task')await expect(page.getByLabel('回合出错后的处理',{exact:true})).toHaveCount(0);
 else{await page.getByLabel('回合出错后的处理',{exact:true}).selectOption('stop');await page.getByLabel('回合出错后的处理',{exact:true}).selectOption('continue');}
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.host_runtime).toMatchObject({episode_launch:launch,episode_error_policy:launch==='per_episode'?'continue':'stop'});
});

test('artifact output is a platform parameter and saves as output-dir mapping',async({page})=>{
 const writes=await setup(page);
 await page.getByRole('tab',{name:'注册新模板',exact:true}).click();
 await page.getByLabel('模板名称',{exact:true}).fill('artifact-output');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('Docker 镜像').fill('demo:1');
 await page.getByLabel('选择要传递的参数').selectOption({label:'产物目录'});
 await page.getByRole('button',{name:'添加参数映射',exact:true}).click();
 await expect(page.getByLabel('程序接收的参数名',{exact:true})).toHaveValue('--output-dir');
 await expect(page.getByText(/业务需要输出参数时/)).toBeVisible();
 await page.getByRole('button',{name:'注册此版本',exact:true}).click();
 await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.platform_bindings).toEqual([{source:'platform.paths.output',target:'argv',name:'--output-dir',encoding:'literal'}]);
});


test('custom browse directory is retained by copy and submitted without changing runtime output',async({page})=>{
 const writes=await setup(page,false,{...template,result_directory:'${mounts:shared}/${business.scene}'});
 await page.getByRole('button',{name:'复制新版本',exact:true}).click();
 await expect(page.getByLabel('自定义结果浏览目录',{exact:true})).toHaveValue('${mounts:shared}/${business.scene}');
 await page.getByLabel('版本',{exact:true}).fill('custom-copy');
 await page.getByLabel('自定义结果浏览目录',{exact:true}).fill('${output_dir}/custom');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.result_directory).toBe('${output_dir}/custom');
 expect(writes[0].body.runtime.mounts).toEqual(template.runtime.mounts);
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('tab',{name:'注册新模板',exact:true}).click();
 await expect(page.getByLabel('自定义结果浏览目录',{exact:true})).toHaveCount(0);
});
