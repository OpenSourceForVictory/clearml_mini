import React,{useSyncExternalStore} from 'react';

export type Duration={state:string;seconds:number|null;started_at?:string|null;finished_at?:string|null;source?:string;quality?:string;clock_adjusted?:boolean};
export type Timing={as_of:string;mode?:string;execution?:Duration;waiting?:Duration;elapsed?:Duration;running_started_at?:string|null};
const anchors=new WeakMap<object,number>();
const listeners=new Set<()=>void>();
let timer:ReturnType<typeof setInterval>|undefined;
let tick=0;
function subscribe(listener:()=>void){
  listeners.add(listener);
  if(!timer)timer=setInterval(()=>{tick++;listeners.forEach(fn=>fn());},250);
  return ()=>{listeners.delete(listener);if(!listeners.size){clearInterval(timer);timer=undefined;}};
}
const snapshot=()=>tick;
const idle=()=>()=>{};
const zero=()=>0;
export function durationText(seconds:number|null|undefined){
  if(seconds==null||!Number.isFinite(seconds)||seconds<0)return '未记录';
  if(seconds<1)return `${Math.round(seconds*1000)}毫秒`;
  let n=Math.floor(seconds);const days=Math.floor(n/86400);n%=86400;
  const hours=Math.floor(n/3600);n%=3600;const minutes=Math.floor(n/60);n%=60;
  return `${days?days+'天 ':''}${hours?hours+'小时 ':''}${minutes?minutes+'分 ':''}${n}秒`;
}
export function DurationValue({value,anchor,failed=false}:{value?:Duration;anchor?:object;failed?:boolean}){
  const running=value?.state==='running'&&value.seconds!=null;
  useSyncExternalStore(running?subscribe:idle,running?snapshot:zero,zero);
  if(!value)return <span>未记录</span>;
  const key=anchor||value;
  if(!anchors.has(key))anchors.set(key,performance.now());
  const age=Math.max(0,(performance.now()-anchors.get(key)!)/1000);
  const stale=running&&age>=6;
  const seconds=value.seconds==null?null:value.seconds+(running?Math.min(age,6):0);
  const label=value.state==='not_started'?'未启动':value.state==='interrupted'?'已中断，完整耗时未记录':
    value.state==='running'&&seconds==null?'运行中，耗时待上报':durationText(seconds);
  return <span title={`${value.source||''}${value.clock_adjusted?' · 节点时钟有调整':''}`}>
    {running?(value.quality==='estimated'?'已运行约 ':'已运行 '):''}{label}
    {value.quality==='fallback'&&(value.source==='legacy_reported'?'（业务上报）':'（历史回退）')}{value.clock_adjusted&&'（节点时钟已调整）'}{stale&&'（更新中断）'}{failed&&!stale&&running&&'（更新重试中）'}
  </span>;
}
export function AttemptTiming({value,failed=false}:{value?:Timing;failed?:boolean}){
  if(!value)return null;
  return <section aria-label="执行时间"><p>执行耗时：<DurationValue value={value.execution} anchor={value} failed={failed}/> · 等待时间：<DurationValue value={value.waiting} anchor={value} failed={failed}/></p>
    <small>执行耗时包含准备、启动及结果处理，不包含排队或结束后的资源清理。</small>
    {value.execution?.state==='finished'&&!value.running_started_at&&<p>未记录进入运行状态的心跳；该尝试可能在启动阶段结束。</p>}
    <details><summary>查看起止时间</summary><p>领取：{date(value.execution?.started_at)} · 首次运行心跳：{date(value.running_started_at)} · 结束：{date(value.execution?.finished_at)}</p></details>
  </section>;
}
function date(value?:string|null){return value?new Date(value).toLocaleString():'未记录';}
