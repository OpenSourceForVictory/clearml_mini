import {test,expect} from '@playwright/test';

test('duration formatting, monotonic live clock, stale freeze, recovery and terminal',async({page})=>{
  await page.route(/https?:\/\/[^/]+\/$/,route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'}));
  await page.goto('/');
  await page.evaluate(async()=>{
    const React=(await import('/node_modules/.vite/deps/react.js')).default;
    const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
    const mod=await import('/src/execution-timing.tsx');
    const win=window as any;
    win.formatTime=mod.durationText;
    const el=document.createElement('div');document.body.replaceChildren(el);
    const root=createRoot(el);
    win.draw=(seconds:number|null,state='running',mode='per_episode')=>root.render(React.createElement(mod.AttemptTiming,{value:{as_of:'2000-01-01T00:00:00Z',mode,execution:{state,seconds},waiting:{state:'finished',seconds:0}}}));
    win.dispose=()=>root.unmount();
    win.draw(10);
  });
  const block=page.getByRole('region',{name:'执行时间'});
  await expect(block).toContainText('已运行 10秒');
  expect(await page.evaluate(()=>(window as any).formatTime(90061))).toBe('1天 1小时 1分 1秒');
  expect(await page.evaluate(()=>(window as any).formatTime(null))).toBe('未记录');
  expect(await page.evaluate(()=>(window as any).formatTime(0))).toBe('0毫秒');
  await expect(block).toContainText('更新中断',{timeout:9000});
  const frozen=await block.textContent();await page.waitForTimeout(1200);expect(await block.textContent()).toBe(frozen);
  await page.evaluate(()=>(window as any).draw(20));await expect(block).toContainText('已运行 20秒');await expect(block).not.toContainText('更新中断');
  await page.evaluate(()=>(window as any).draw(0,'finished'));await expect(block).toContainText('执行耗时：0毫秒');
  await page.waitForTimeout(1100);await expect(block).not.toContainText('已运行');
  await page.evaluate(()=>(window as any).dispose());await expect(block).toHaveCount(0);
});

test('episode time column follows process mode; whole process retains protocol results',async({page})=>{
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  let independent=true;
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/episodes'))return route.fulfill({json:{timing:{as_of:'2026-01-01T00:00:00Z',mode:independent?'per_episode':'whole_task',execution:{state:'finished',seconds:3},waiting:{state:'finished',seconds:0}},planned:2,run_status:'FAILED',exit_code:1,result_source:'protocol',progress:{completed:1,errors:1},items:[
      {episode_id:1,seed:7,result:{status:'COMPLETED',success:true},timing:{state:'finished',seconds:.123}},
      {episode_id:2,seed:8,result:{status:'ERROR',error:'invalid result'},timing:{state:'interrupted',seconds:null}},
    ]}});
    return route.fulfill({json:{items:[]}});
  });
  await page.route(/https?:\/\/[^/]+\/$/,route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'}));
  await page.goto('/');
  await page.evaluate(async()=>{
    const React=(await import('/node_modules/.vite/deps/react.js')).default;const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
    const {EpisodeList}=await import('/src/cs.tsx');const el=document.createElement('div');document.body.replaceChildren(el);
    createRoot(el).render(React.createElement(EpisodeList,{jobId:'j',runId:'r'}));
  });
  await expect(page.getByRole('columnheader',{name:'回合耗时'})).toBeVisible();
  await expect(page.getByRole('cell',{name:'123毫秒'})).toBeVisible();await expect(page.getByText('已中断，完整耗时未记录')).toBeVisible();
  independent=false;await expect(page.getByRole('columnheader',{name:'回合耗时'})).toHaveCount(0);
  await expect(page.getByRole('table',{name:'逐回合状态'})).toBeVisible();
});

test('many timing rows share one ticker and release it on unmount',async({page})=>{
 await page.route(/https?:\/\/[^/]+\/$/,route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'}));
 await page.goto('/');
 await page.evaluate(async()=>{
  const React=(await import('/node_modules/.vite/deps/react.js')).default;
  const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
  const {DurationValue}=await import('/src/execution-timing.tsx');const win=window as any;
  win.timingIntervals=0;win.timingCleared=0;
  const original=window.setInterval,clear=window.clearInterval;let id:number;
  window.setInterval=((fn:()=>void,ms:number)=>{const next=original(fn,ms);if(ms===250){win.timingIntervals++;id=next;}return next;}) as any;
  window.clearInterval=((value:number)=>{if(value===id)win.timingCleared++;clear(value);}) as any;
  const el=document.createElement('div');document.body.append(el);const root=createRoot(el);win.dispose=()=>root.unmount();
  root.render(React.createElement('div',{},Array.from({length:200},(_,i)=>React.createElement(DurationValue,{key:i,value:{state:'running',seconds:0}}))));
 });
 await expect.poll(()=>page.evaluate(()=>(window as any).timingIntervals)).toBe(1);
 await page.evaluate(()=>(window as any).dispose());
 await expect.poll(()=>page.evaluate(()=>(window as any).timingCleared)).toBe(1);
});
