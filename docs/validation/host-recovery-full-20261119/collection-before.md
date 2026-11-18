# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: cs-tabs.spec.ts >> collection management publishes and hands off to the business wizard
- Location: tests/cs-tabs.spec.ts:34:1

# Error details

```
Test timeout of 45000ms exceeded.
```

```
Error: locator.fill: Test timeout of 45000ms exceeded.
Call log:
  - waiting for getByRole('region', { name: '集合运行向导' }).getByLabel('推理服务地址', { exact: true })

```

# Page snapshot

```yaml
- generic [ref=e3]:
  - complementary [ref=e4]:
    - generic [ref=e5]:
      - generic [ref=e6]: M
      - strong [ref=e7]: Mini Eval
    - generic [ref=e8]: 评测工作区
    - navigation [ref=e9]:
      - button "我的任务" [ref=e10] [cursor=pointer]
      - button "计算资源" [ref=e11] [cursor=pointer]
      - button "业务评测向导" [ref=e12] [cursor=pointer]
      - button "评测运行与结果" [ref=e13] [cursor=pointer]
      - button "全部任务" [ref=e14] [cursor=pointer]
      - button "管理设置" [ref=e15] [cursor=pointer]
      - button "用户指南" [ref=e16] [cursor=pointer]
    - generic [ref=e17]:
      - strong [ref=e18]: tester
      - generic [ref=e19]: 管理员
      - button "退出登录" [ref=e20] [cursor=pointer]
  - main [ref=e21]:
    - generic [ref=e22]:
      - generic [ref=e23]: 工作台 / 业务评测向导
      - generic [ref=e24]: ●每 2 秒更新
    - group [ref=e25]:
      - generic "操作说明 · 业务评测向导：提交与试跑" [ref=e26] [cursor=pointer]
    - generic [ref=e28]:
      - paragraph [ref=e29]: CLIENT / SERVER
      - heading "业务评测向导" [level=1] [ref=e30]
      - paragraph [ref=e31]: 选择任务、客户端和推理服务，检查后提交业务评测。
    - region "集合运行向导" [ref=e34]:
      - heading "发起集合运行" [level=2] [ref=e35]
      - list "运行步骤" [ref=e36]:
        - listitem [ref=e37]: 1. 选择集合
        - listitem [ref=e38]: 2. 客户端
        - listitem [ref=e39]: 3. 服务端
        - listitem [ref=e40]: 4. 资源配置
        - listitem [ref=e41]: 5. 预检确认
      - paragraph [ref=e42]: 选择业务配置与并发偏好，平台负责分配机器和传递参数；实际通信和执行可通过试跑验证。
      - alert [ref=e43]: "Error: 请先读取平台执行时限上限"
      - group [ref=e45]:
        - generic [ref=e46]:
          - generic [ref=e47]:
            - generic [ref=e48]: 客户端模板
            - combobox "客户端模板" [ref=e49]:
              - option "选择客户端及版本" [disabled]
              - option "RoboTwin / 1" [selected]
          - generic [ref=e50]:
            - generic [ref=e51]: 请求超时秒
            - spinbutton "请求超时秒" [ref=e52]: "10"
          - generic [ref=e53]:
            - checkbox "不限制每项总执行时长" [ref=e54]
            - text: 不限制每项总执行时长
          - generic [ref=e55]:
            - generic [ref=e56]: 每项总超时秒
            - spinbutton "每项总超时秒" [ref=e57]: "300"
        - paragraph [ref=e58]: 客户端总执行时限：每项任务全部回合共用，不是每回合单独计时。选择不限仍需心跳续租，且支持取消；需要 Worker 协议 16。正在读取平台上限。
        - alert [ref=e59]: "平台执行时限读取失败：Error: 平台执行时限响应无效"
        - button "刷新执行时限" [ref=e60] [cursor=pointer]
        - button "复制修改客户端模板" [ref=e61] [cursor=pointer]
        - group "客户端业务参数" [ref=e62]:
          - paragraph [ref=e64]: 此模板未声明类型化业务参数。任务参数仍可在集合清单中设置。
        - generic [ref=e65]:
          - generic [ref=e66]: 启动变量覆盖 JSON
          - textbox "启动变量覆盖 JSON" [ref=e67]: "{}"
        - paragraph [ref=e68]: 仅可覆盖模板允许的变量：无。请勿填写密码或令牌。
        - generic [ref=e69]:
          - button "上一步" [ref=e70] [cursor=pointer]
          - button "下一步" [active] [ref=e71] [cursor=pointer]
```

# Test source

```ts
  1  | import {test, expect, type Page} from '@playwright/test';
  2  | 
  3  | async function openWorkbench(page:Page, role='admin') {
  4  |   const suites:any[]=[];
  5  |   const batches:any[]=[];
  6  |   const template={id:'client-1',name:'RoboTwin',version:'1',enabled:true,image:'client:1',protocols:['ws'],command:['python','-m','runner.sdk']};
  7  |   let submitted:any;
  8  |   await page.addInitScript(()=>sessionStorage.setItem('eval-token','ui-test'));
  9  |   await page.route('**/api/v1/**',async route=>{
  10 |     const path=new URL(route.request().url()).pathname.replace('/api/v1/','');
  11 |     let body:any=[];
  12 |     if(path==='auth/me') body={id:'user-1',username:'tester',role};
  13 |     else if(path==='client-templates') body=[template];
  14 |     else if(path==='suites/manage') body={items:[],total:0};
  15 |     else if(path==='suites/preview') {const doc=JSON.parse(route.request().postDataJSON().content);body={valid:true,errors:[],document:doc,task_count:doc.cases.length,total_episodes:3};}
  16 |     else if(path==='pools') body=[{id:'pool-1',name:'gpu-pool'}];
  17 |     else if(path==='suites') {
  18 |       if(route.request().method()==='POST') {body={id:'suite-1',revision:0,archived:false,...route.request().postDataJSON()};suites.push(body);}
  19 |       else body=suites;
  20 |     } else if(path==='orchestrations/preflight') {const b=route.request().postDataJSON();body={checked_at:new Date().toISOString(),can_submit:true,status:'ready',cases:suites.find(s=>s.id===b.suite_id)?.cases||b.cases,nodes:[],reasons:[]};
  21 |     } else if((path==='batches'||path==='orchestrations')) {
  22 |       if(route.request().method()==='POST') {
  23 |         submitted=route.request().postDataJSON();
  24 |         body={id:'batch-1',title:submitted.title,status:'PENDING',total:1,succeeded:0,finished:0,total_episodes:3,cases:[]};batches.push(body);
  25 |       } else body=batches;
  26 |     }
  27 |     await route.fulfill({json:body});
  28 |   });
  29 |   await page.goto('/');
  30 |   await page.getByRole('button',{name:'业务评测向导',exact:true}).click();
  31 |   return ()=>submitted;
  32 | }
  33 | 
  34 | test('collection management publishes and hands off to the business wizard',async({page})=>{
  35 |  const submitted=await openWorkbench(page);
  36 |  await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'评测集合管理',exact:true}).click();
  37 |  const registration=page.getByRole('tabpanel',{name:'评测集合管理',exact:true});
  38 |  await registration.getByRole('button',{name:'新建集合',exact:true}).click();await registration.getByLabel('集合名称',{exact:true}).fill('RoboTwin full');await registration.getByTestId('suite-client-client-1').check();
  39 |  await registration.getByRole('button',{name:'添加任务',exact:true}).click();await registration.getByLabel('任务名称 0',{exact:true}).fill('demo');await registration.getByRole('button',{name:'发布并运行',exact:true}).click();
  40 |  const runs=page.getByRole('region',{name:'集合运行向导'});await expect(runs).toBeVisible();await runs.getByLabel('批次名称',{exact:true}).fill('RoboTwin batch');await expect(runs.getByLabel('选择已保存集合')).toHaveValue('suite-1');
> 41 |  await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('客户端资源池',{exact:true}).selectOption('pool-1');await runs.getByLabel('最大并发任务数').fill('2');await runs.getByRole('button',{name:'检查并确认',exact:true}).click();await runs.getByRole('button',{name:'提交评测批次',exact:true}).click();
     |                                                                                                                                                                             ^ Error: locator.fill: Test timeout of 45000ms exceeded.
  42 |  expect(submitted()).toMatchObject({suite_id:'suite-1',cases:[],max_concurrent:2,client:{template_id:'client-1',endpoints:[{url:'ws://inference:8000',mode:'shared'}]}});
  43 |  await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();await expect(page.getByRole('row',{name:'批次 RoboTwin batch',exact:true})).toBeVisible();await expect(runs).toBeHidden();
  44 | });
  45 | test('member keeps own collection and source registration without admin controls',async({page})=>{
  46 |  await openWorkbench(page,'member');await page.getByRole('button',{name:'评测配置',exact:true}).click();await page.getByRole('tab',{name:'客户端接入',exact:true}).click();await page.getByRole('tab',{name:'代码构建',exact:true}).click();
  47 |  await expect(page.getByRole('heading',{name:'提交仿真客户端代码'})).toBeVisible();await expect(page.getByRole('button',{name:'管理设置',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'登记客户端模板',exact:true})).toHaveCount(0);
  48 |  await page.getByRole('tab',{name:'代码构建',exact:true}).focus();await page.keyboard.press('Home');await expect(page.getByRole('tab',{name:'镜像接入',exact:true})).toBeFocused();
  49 | });
  50 | 
  51 | test('results collection subtab preserves draft and hands off to wizard for members',async({page})=>{
  52 |  const submitted=await openWorkbench(page,'user');
  53 |  await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();await page.getByRole('tab',{name:'评测任务集合管理',exact:true}).click();
  54 |  const registration=page.getByRole('tabpanel',{name:'评测任务集合管理',exact:true});
  55 |  await registration.getByRole('button',{name:'新建集合',exact:true}).click();await registration.getByLabel('集合名称',{exact:true}).fill('RoboTwin full');await page.getByRole('tab',{name:'运行记录',exact:true}).click();await page.getByRole('tab',{name:'评测任务集合管理',exact:true}).click();await expect(registration.getByLabel('集合名称',{exact:true})).toHaveValue('RoboTwin full');await registration.getByTestId('suite-client-client-1').check();
  56 |  await registration.getByRole('button',{name:'添加任务',exact:true}).click();await registration.getByLabel('任务名称 0',{exact:true}).fill('demo');await registration.getByRole('button',{name:'发布并运行',exact:true}).click();
  57 |  const runs=page.getByRole('region',{name:'集合运行向导'});await expect(runs).toBeVisible();await runs.getByLabel('批次名称',{exact:true}).fill('RoboTwin batch');await expect(runs.getByLabel('选择已保存集合')).toHaveValue('suite-1');
  58 |  await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('客户端资源池',{exact:true}).selectOption('pool-1');await runs.getByLabel('最大并发任务数').fill('2');await runs.getByRole('button',{name:'检查并确认',exact:true}).click();await runs.getByRole('button',{name:'提交评测批次',exact:true}).click();
  59 |  expect(submitted()).toMatchObject({suite_id:'suite-1',cases:[],max_concurrent:2,client:{template_id:'client-1',endpoints:[{url:'ws://inference:8000',mode:'shared'}]}});
  60 |  await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();await expect(page.getByRole('row',{name:'批次 RoboTwin batch',exact:true})).toBeVisible();await expect(runs).toBeHidden();
  61 | });
```