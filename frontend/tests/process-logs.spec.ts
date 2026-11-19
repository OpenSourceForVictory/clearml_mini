import {test,expect} from '@playwright/test';
test('scoped log drawer filters pages, shows gaps, downloads and stops on close',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 let reads=0;const queries:any[]=[];
 const entry={id:'log',execution_id:'exec',scope:'episode',episode_id:2,generation:'g',status:'closed',path:'/runs/exec/logs/episodes/000002',next_sequence:3,truncated:true,dropped_bytes:12,updated_at:'2026-11-19'};
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'u',role:'user'};
  if(path.endsWith('/monitor'))data={id:'a',title:'日志批次',max_concurrent:1,total:1,finished:1,total_episodes:2,summary:{states:{SUCCEEDED:1},completed_episodes:2,successes:2},cases:[{job_id:'j',case_id:'case',status:'SUCCEEDED',run_id:'r',attempt:1,episodes:2,seed:1,progress:{completed:2},config:{}}]};
  if(path.endsWith('/episodes'))data={planned:2,progress:{completed:2,errors:0},items:[]};
  if(path.endsWith('/log-catalog'))data={items:[entry]};
  if(path.endsWith('/log-reads')){queries.push(route.request().postDataJSON());data={read_id:'read'+queries.length};}
  if(path.startsWith('log-reads/')){
   reads++;const q=queries[Number(path.replace('log-reads/read',''))-1];
   data={status:'ready',generation:'g',page:{records:[{sequence:1,time:'time',stream:q.stream==='stderr'?'stderr':'stdout',text:q.direction==='backward'?'earlier':'<script>plain text</script>',gap:false}],previous_cursor:'before',next_cursor:'after',has_more:false,gap:true,closed:true,source:'node'}};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/a');
 await page.getByRole('button',{name:'查看回合',exact:true}).click();
 await page.getByRole('button',{name:'回合 2 日志',exact:true}).click();
 const drawer=page.getByRole('dialog',{name:'回合 2 日志'});
 await expect(drawer.locator('pre')).toContainText('<script>plain text</script>');
 await expect(drawer).toContainText('日志有缺口');
 await page.getByLabel('输出来源',{exact:true}).selectOption('stderr');
 await expect(drawer.locator('pre')).toContainText('[stderr]');
 await page.getByRole('button',{name:'加载更早日志'}).click();
 await expect(drawer.locator('pre')).toContainText('earlier');
 expect(queries.at(-1).cursor).toBe('before');
 const download=page.waitForEvent('download');
 await page.getByRole('button',{name:'下载保留日志（最多 8 MiB）'}).click();
 expect((await download).suggestedFilename()).toBe('exec-episode-2.jsonl');
 await page.setViewportSize({width:390,height:800});
 expect((await drawer.boundingBox())!.width).toBeLessThanOrEqual(390);
 await page.keyboard.press('Escape');
 await expect(drawer).toHaveCount(0);
 const before=reads;await page.waitForTimeout(3300);expect(reads).toBe(before);
 await expect(page.getByRole('button',{name:'回合 2 日志',exact:true})).toBeFocused();
});

test('large retained logs download in bounded explicit parts with a fixed end sequence',async({page})=>{
 const {readFile}=await import('node:fs/promises');
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 const requests:any[]=[];
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'u',role:'user'};
  if(path.endsWith('/monitor'))data={id:'a',title:'分片日志',max_concurrent:1,total:1,finished:1,total_episodes:1,summary:{states:{SUCCEEDED:1},completed_episodes:1,successes:1},cases:[{job_id:'j',case_id:'case',status:'SUCCEEDED',run_id:'r',attempt:1,episodes:1,seed:1,progress:{completed:1},config:{}}]};
  if(path.endsWith('/episodes'))data={planned:1,progress:{completed:1,errors:0},items:[]};
  if(path.endsWith('/log-catalog'))data={items:[{id:'log',execution_id:'exec',scope:'business',episode_id:0,generation:'g',status:'closed',path:'/logs/business',next_sequence:100,updated_at:'today'}]};
  if(path.endsWith('/log-reads')){requests.push(route.request().postDataJSON());data={read_id:String(requests.length-1)};}
  if(path.startsWith('log-reads/')){
   const q=requests[Number(path.split('/')[1])],n=q.direction==='forward'?Number(q.cursor||0):99;
   data={status:'ready',generation:'g',page:{records:[{sequence:n,time:'now',stream:'stdout',text:'line-'+n,gap:false}],next_cursor:String(n+1),has_more:n<99,closed:true,source:'node'}};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/a');await page.getByRole('button',{name:'查看回合',exact:true}).click();
 await page.getByRole('button',{name:'任务公共日志',exact:true}).click();
 const drawer=page.getByRole('dialog',{name:'任务公共日志'});await expect(drawer.locator('pre')).toContainText('line-99');
 const first=page.waitForEvent('download');await drawer.getByRole('button',{name:'下载保留日志（最多 8 MiB）'}).click();
 const one=await first;expect(one.suggestedFilename()).toBe('exec-business-0-part-001.jsonl');
 expect((await readFile((await one.path())!,'utf8')).trim().split('\n')).toHaveLength(96);
 await expect(drawer).toContainText('尚非完整范围');
 const next=page.waitForEvent('download');await drawer.getByRole('button',{name:'下载下一分片（第 2 份）'}).click();
 const two=await next;expect(two.suggestedFilename()).toBe('exec-business-0-part-002.jsonl');
 const lines=(await readFile((await two.path())!,'utf8')).trim().split('\n').map(s=>JSON.parse(s));
 expect(lines.map(r=>r.sequence)).toEqual([96,97,98,99]);
 expect(requests.filter(q=>q.direction==='forward').every(q=>q.until===100)).toBe(true);
 await expect(drawer.getByRole('button',{name:/下载下一分片/})).toHaveCount(0);
 await expect(drawer).toContainText('已下载本次读取范围内保留日志');
});

test('failed download does not claim success or erase the displayed page',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 const requests:any[]=[];let downloads=0;page.on('download',()=>downloads++);
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'u',role:'user'};
  if(path.endsWith('/monitor'))data={id:'a',title:'失败下载',max_concurrent:1,total:1,finished:1,total_episodes:1,summary:{states:{SUCCEEDED:1},completed_episodes:1,successes:1},cases:[{job_id:'j',case_id:'case',status:'SUCCEEDED',run_id:'r',attempt:1,episodes:1,seed:1,progress:{completed:1},config:{}}]};
  if(path.endsWith('/episodes'))data={planned:1,progress:{completed:1,errors:0},items:[]};
  if(path.endsWith('/log-catalog'))data={placement:{node_name:'npu-node',accelerator_type:'npu',gpu_index:0},items:[{id:'log',execution_id:'exec',scope:'business',episode_id:0,generation:'g',status:'closed',path:'/logs/business',next_sequence:2,updated_at:'today'}]};
  if(path.endsWith('/log-reads')){requests.push(route.request().postDataJSON());data={read_id:String(requests.length-1)};}
  if(path.startsWith('log-reads/')){
   const q=requests[Number(path.split('/')[1])];
   data=q.direction==='forward'?{status:'cleaned'}:{status:'ready',generation:'g',source:'platform_cache',page:{records:[{sequence:1,time:'now',stream:'stdout',text:'retained preview',gap:false}],has_more:false,closed:true}};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/a');await page.getByRole('button',{name:'查看回合',exact:true}).click();await page.getByRole('button',{name:'任务公共日志',exact:true}).click();
 const drawer=page.getByRole('dialog');await expect(drawer).toContainText('npu-node · NPU 0');await expect(drawer).toContainText('平台临时页缓存');
 await page.getByRole('button',{name:'下载保留日志（最多 8 MiB）'}).click();await expect(drawer).toContainText('本分片未生成');
 await expect(drawer.locator('pre')).toContainText('retained preview');expect(downloads).toBe(0);
});
