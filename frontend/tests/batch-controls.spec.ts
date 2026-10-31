import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page){
  let offline=false,run='r1',lose=false,delay=false,revision=0,paused=false,loseControl=false;const controls:any[]=[];const requests:any[]=[];
  const cases=['FAILED','TIMED_OUT','SUCCEEDED','PENDING'].map((status,i)=>({job_id:'j'+i,case_id:'case'+i,task_name:'task'+i,status,run_id:i===0?run:'r'+i,attempt:1,episodes:2,seed:0,episode_reporting:true,progress:{completed:1,successes:0},node_name:null,gpu_index:null,reason_codes:status==='PENDING'?['gpu_busy']:[],waiting:status==='PENDING'?{reasons:[{code:'gpu_busy',message:'GPU 已占用'}],nodes:[]}:null,config:{pool_name:'mixed',client_name:'client',client_version:'1',endpoint:'http://server',gpu_mode:'exclusive',requested_vram_gb:0}}));
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
    if(path==='auth/me')data={id:'user',username:'user',role:'user'};
    if(path==='suites/manage')data={items:[],total:0};
    if(path==='pools')data=[{id:'p',name:'pool'}];
    if(path==='batches')data=[{id:'a',title:'批次 A',status:'PENDING',succeeded:1,total:4,finished:3,total_episodes:8,cases:[]}];
    if(path.endsWith('/monitor')){
      if(path.includes('/private/')){await route.fulfill({status:404,json:{detail:'batch not found'}});return;}
      if(offline){await route.abort();return;}
      if(delay&&path.includes('/a/'))await new Promise(r=>setTimeout(r,900));
      data={scheduling_paused:paused,control_revision:revision,id:'a',title:path.includes('/b/')?'批次 B':'批次 A',max_concurrent:2,total:4,finished:3,total_episodes:8,cases:cases.map((c,i)=>({...c,run_id:i===0?run:c.run_id})),summary:{states:{FAILED:1,TIMED_OUT:1,SUCCEEDED:1,PENDING:1},completed_episodes:3,successes:0,goal_failures:3,episode_errors:0,execution_failures:2,unreported_tasks:0,success_rate:0}};
    }
    if(path.endsWith('/control')){
      const body=route.request().postDataJSON();controls.push(body);
      await new Promise(r=>setTimeout(r,150));
      if(body.expected_revision!==revision){await route.fulfill({status:409,json:{detail:'batch control changed; refresh before retrying'}});return;}
      revision++;paused=body.action!=='resume';
      if(loseControl){loseControl=false;await route.abort();return;}
      data={scheduling_paused:paused,control_revision:revision,counts:{withdrawn:1,cancelled:1,cancel_requested:1,already_cancelling:0,terminal_unchanged:2}};
    }
    if(path.endsWith('/retry-failed')){requests.push(route.request().postDataJSON());if(lose){lose=false;await route.abort();return;}data={retried:0,skipped:2,items:[{job_id:'j0',outcome:'skipped',message:'尝试已变化，请刷新后重新选择',attempt:2},{job_id:'j1',outcome:'skipped',message:'旧执行尚未确认清理',attempt:1}]};}
    if(path==='jobs/j0')data={attempts:[{id:'old',attempt:1,status:'FAILED'},{id:'new',attempt:2,status:'FAILED'}]};
    if(path.endsWith('/logs'))data={chunks:[{content:new URL(route.request().url()).searchParams.get('run_id')+' log'}],next_after:1};
    if(path.endsWith('/episodes'))data={planned:2,progress:{completed:1,successes:0,errors:0},items:[]};
    if(path.endsWith('/artifacts'))data=[{id:'file',name:path.includes('/old/')?'old.json':'new.json',size_bytes:2}];
    await route.fulfill({json:data});
  });
  await page.goto('/#/cs/batches/a');
  await expect(page.getByRole('heading',{name:'批次 A',exact:true})).toBeVisible();
  return {controls,advance:()=>{revision++;},loseControl:()=>{loseControl=true;},requests,setRun:()=>{run='r-new';},lose:()=>{lose=true;},offline:(v:boolean)=>{offline=v;},delay:()=>{delay=true;}};
}
test('pause resume and cancel confirmation use versions without resetting filters',async({page})=>{
 const ctl=await setup(page);await page.getByLabel('任务状态',{exact:true}).selectOption('FAILED');
 await page.getByRole('button',{name:'暂停后续调度',exact:true}).click();
 await expect(page.getByRole('button',{name:'取消整批',exact:true})).toBeDisabled();
 await expect(page.getByRole('status')).toContainText('已暂停后续调度');
 await page.getByRole('button',{name:'恢复调度',exact:true}).click();await expect(page.getByRole('status')).toContainText('已恢复调度');
 await page.getByRole('button',{name:'取消整批',exact:true}).click();expect(ctl.controls).toHaveLength(2);
 ctl.advance();await page.waitForResponse(r=>r.url().endsWith('/monitor'));
 await page.getByRole('button',{name:'确认取消整批',exact:true}).click();await expect(page.getByRole('alert')).toContainText('本次操作未执行');
 expect(ctl.controls[2]).toEqual({action:'cancel',expected_revision:2});
 await page.getByRole('button',{name:'关闭操作确认'}).click();
 await page.getByRole('button',{name:'取消整批',exact:true}).click();await page.getByRole('button',{name:'确认取消整批',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('请等待停止和清理确认');await expect(page.getByLabel('任务状态',{exact:true})).toHaveValue('FAILED');
});
test('lost control response retries original version instead of applying a new action',async({page})=>{
 const ctl=await setup(page);ctl.loseControl();await page.getByRole('button',{name:'暂停后续调度',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('原控制版本');await page.waitForResponse(r=>r.url().endsWith('/monitor'));
 await page.getByRole('button',{name:'重试原控制请求'}).click();await expect(page.getByRole('alert')).toContainText('本次操作未执行');
 expect(ctl.controls).toHaveLength(2);expect(ctl.controls[0]).toEqual(ctl.controls[1]);
 await page.getByRole('button',{name:'关闭操作确认'}).click();await expect(page.getByRole('button',{name:'恢复调度',exact:true})).toBeEnabled();
});
