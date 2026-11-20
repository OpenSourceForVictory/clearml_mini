import {test,expect,type Page} from '@playwright/test';

async function setup(page:Page, response:(id:number)=>Promise<any>|any){
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 let reads=0;
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'u',role:'user'};
  if(path.endsWith('/monitor'))data={id:'a',title:'日志恢复',max_concurrent:1,total:1,finished:0,total_episodes:3,summary:{states:{RUNNING:1},completed_episodes:0},cases:[{job_id:'j',case_id:'case',status:'RUNNING',run_id:'r',attempt:1,episodes:3,seed:0,progress:{completed:0},config:{}}]};
  if(path.endsWith('/episodes'))data={planned:3,progress:{completed:0,errors:0},items:[]};
  if(path.endsWith('/log-catalog'))data={items:[{id:'log',execution_id:'exec',scope:'business',episode_id:0,generation:'g',status:'open',path:'/runs/exec/logs/business',next_sequence:1,updated_at:'today'}]};
  if(path.endsWith('/log-reads'))data={read_id:String(++reads)};
  if(path.startsWith('log-reads/'))data=await response(Number(path.split('/')[1]));
  await route.fulfill({json:data}).catch(()=>{});
 });
 await page.goto('/#/cs/batches/a');
 await page.getByRole('button',{name:'查看回合',exact:true}).click();
 await page.getByRole('button',{name:'任务公共日志',exact:true}).click();
 return {drawer:page.getByRole('dialog',{name:'任务公共日志'}),reads:()=>reads};
}
const ready=(text:string,closed=false)=>({status:'ready',generation:'g',page:{records:[{sequence:0,time:'now',stream:'stdout',text,gap:false}],has_more:false,closed,source:'node'}});

for(const [state,message] of Object.entries({expired:'节点读取超时',busy:'日志正在轮转或清理',cleaned:'节点日志已清理',missing:'日志尚未生成或已不存在',failed:'节点日志读取失败',superseded:'日志代次已变化'})){
 test(`scoped log ${state} is explicit and refresh recovers`,async({page})=>{
  let recovered=false;
  const {drawer}=await setup(page,()=>recovered?ready('recovered output',true):{status:state});
  await expect(drawer.getByRole('alert')).toContainText(message);
  await expect(drawer).toContainText('此业务进程的多个回合共用这份日志');
  recovered=true;await drawer.getByRole('button',{name:'刷新尾部'}).click();
  await expect(drawer.locator('pre')).toContainText('recovered output');
  await expect(drawer.getByRole('alert')).toHaveCount(0);
 });
}

test('late read from a closed drawer cannot replace the reopened log page',async({page})=>{
 let release!:()=>void;const held=new Promise<void>(resolve=>release=resolve);let waiting=false;
 const {drawer}=await setup(page,async id=>{if(id===1){waiting=true;await held;return ready('old delayed output');}return ready('current output',true);});
 await expect.poll(()=>waiting).toBe(true);
 await drawer.getByRole('button',{name:'关闭独立日志'}).click();
 await page.getByRole('button',{name:'任务公共日志',exact:true}).click();
 await expect(drawer.locator('pre')).toContainText('current output');
 release();
 await expect(drawer.locator('pre')).not.toContainText('old delayed output');
 await expect(drawer.locator('pre')).toContainText('current output');
});

test('pause stops live log polling and resume reads new output',async({page})=>{
 await page.clock.install();
 const {drawer,reads}=await setup(page,id=>ready('page-'+id));
 await expect(drawer.locator('pre')).toContainText('page-1');
 await drawer.getByRole('button',{name:'暂停跟随'}).click();
 const paused=reads();await page.clock.fastForward(10000);expect(reads()).toBe(paused);
 await drawer.getByRole('button',{name:'自动跟随'}).click();
 await page.clock.fastForward(3100);
 await expect.poll(reads).toBeGreaterThan(paused);
 await expect(drawer.locator('pre')).toContainText('page-'+reads());
});

test('cancel download discards a delayed part and retains current page',async({page})=>{
 let release!:()=>void;const held=new Promise<void>(resolve=>release=resolve);let waiting=false,downloads=0;
 page.on('download',()=>downloads++);
 const {drawer}=await setup(page,async id=>{if(id===2){waiting=true;await held;return ready('download body',true);}return ready('visible page',true);});
 await expect(drawer.locator('pre')).toContainText('visible page');
 await drawer.getByRole('button',{name:'下载保留日志（最多 8 MiB）'}).click();
 await expect.poll(()=>waiting).toBe(true);
 await drawer.getByRole('button',{name:'取消下载'}).click();release();
 await expect(drawer).toContainText('本分片未生成');expect(downloads).toBe(0);
 await expect(drawer.locator('pre')).toContainText('visible page');
});
