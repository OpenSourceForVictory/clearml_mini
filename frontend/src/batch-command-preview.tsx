import React,{useState} from 'react';
type Values=Record<string,any>;
const literal=(value:any):string=>typeof value==='string'?value:JSON.stringify(value);
// Match Runner: expand template tokens, then business bindings, then platform bindings.
export function commandPreview(template:Values,parameters:Values={},task:Values={},episode=1){
 const perEpisode=template.execution_mode==='host'&&template.host_runtime?.episode_launch==='per_episode';
 const each=perEpisode||template.integration_mode==='sdk-command';
 const runtime=(template.execution_mode==='host'?template.host_runtime:template.runtime)||{};
 const seed=typeof task.seed==='number'?(task.seed+episode-1)%2147483648:undefined;
 const values:Values={case_id:task.case_id,task_name:task.task_name,episodes:perEpisode?1:task.episodes,seed:perEpisode?seed:task.seed,
  episode_id:each?episode:undefined,episode_seed:each?seed:undefined};
 const source=(name:string):any=>name.startsWith('task.')?values[name.slice(5)]:name==='platform.job_id'?task.job_id:undefined;
 const expand=(s:string)=>s.replace(/\$\{([^}]+)\}/g,(original,key)=>values[key]===undefined?original:String(values[key]));
 const argv=Array.isArray(template.command)?template.command.map(expand):null;
 const environment:Record<string,string>=Object.fromEntries(Object.entries({...runtime.environment,...task.environment}).map(([k,v])=>[k,expand(String(v))]));
 const pending=new Set<string>();
 function bind(b:Values,value:any,unknown=false){
  if(b.target==='input'||!b.target)return;
  if(unknown){pending.add(b.source);value='⟨启动时解析：'+b.source+'⟩';}
  if(value===undefined)return;
  if(b.target==='env')environment[b.name]=b.encoding==='json'&&!unknown?JSON.stringify(value):literal(value);
  else if(argv){
   if(b.encoding==='flag'){if(value)argv.push(b.name);}
   else if(b.encoding==='repeat'&&Array.isArray(value))for(const item of value)argv.push(b.name,literal(item));
   else argv.push(b.name,b.encoding==='json'&&!unknown?JSON.stringify(value):literal(value));
  }
 }
 for(const [name,field] of Object.entries(template.parameter_schema?.fields||{}))bind((field as Values).binding||{target:'input'},parameters[name]);
 for(const b of template.platform_bindings||[])bind(b,source(b.source),source(b.source)===undefined);
 return {argv,environment,pending:[...pending],each};
}
export function BatchCommandPreview({template,parameters,task}:{template:Values;parameters?:Values;task?:Values}){
 const [choice,setChoice]=useState(1);
 const total=Math.max(1,Number(task?.episodes)||1),episode=Math.min(choice,total);
 const preview=commandPreview(template,parameters,task,episode);
 return <section aria-label="参数映射后的启动配置"><h5>启动命令（参数映射后）</h5>
 {preview.each&&task?.episodes!=null&&<label>预览回合<input type="number" min={1} max={total} value={episode} onChange={e=>setChoice(Math.max(1,Math.min(total,Math.trunc(Number(e.target.value))||1)))}/></label>}
 <pre aria-label="参数映射后的启动命令">{preview.argv===null?'未记录':JSON.stringify(preview.argv,null,2)}</pre>
 <p>按本次配置展开的业务命令；每个数组元素是一个参数。节点路径、服务地址和监听端口等启动时才能确定的值保留占位。</p>
 {preview.each&&task?.episodes!=null&&<p>{template.execution_mode==='host'&&template.host_runtime?.episode_launch==='per_episode'?'每回合启动时 task.episodes 为 1，task.seed 随所选回合递增。':'SDK 命令每回合启动，task.episodes 与 task.seed 保留任务总回合数和基础种子；episode_id / episode_seed 随回合变化。'}</p>}
 <details><summary>合并后的显式环境变量（含参数映射）</summary><pre>{JSON.stringify(preview.environment,null,2)}</pre></details>
 </section>;
}
