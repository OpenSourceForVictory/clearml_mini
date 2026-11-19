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
