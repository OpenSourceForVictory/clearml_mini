import React,{useRef,useState} from 'react';
import {Field} from './form-fields';
import type {ParameterDraft} from './parameter-editor';
export type PlatformBinding={source:string;target:string;name?:string;encoding?:string};
const common=[['platform.job_id','任务 ID','job-id'],['platform.execution_id','执行 ID','execution-id'],['platform.paths.work','工作目录','work-dir'],['platform.paths.output','产物目录','output-dir'],['platform.paths.cache','缓存目录','cache-dir']];
const client=[['task.task_name','任务名','task'],['task.case_id','集合任务 ID','case-id'],['task.episodes','回合数','episodes'],['task.seed','任务种子','seed'],['platform.service.endpoint','推理服务地址（需配置服务）','endpoint'],['platform.service.key','推理服务标识（需配置服务）','service-key']];
const server=[['platform.listen.address','监听地址','host'],['platform.listen.port','监听端口','port'],['platform.listen.health_port','健康端口','health_port']];
export function validateMappings(bindings:PlatformBinding[],parameters:Record<string,any>|null,command:string[],environment:Record<string,unknown>,overrides:string[]){
 const used=new Set<string>();
 const fields=Object.values(parameters?.fields||{}) as any[];
 for(const b of [...bindings,...fields.map(f=>f.binding).filter(Boolean)]){
  if(b.target==='input')continue;
  const name=b.name||'';
  if(b.target==='argv'&&!/^--[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name))throw new Error('命令参数名须使用 --name 形式');
  if(b.target==='env'&&(!/^[A-Za-z_][A-Za-z0-9_-]{0,127}$/.test(name)||/^(MINI_EVAL_|NVIDIA_|ASCEND_)/.test(name)||['CUDA_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','HIP_VISIBLE_DEVICES','PYTHONPATH','PYTHONHOME'].includes(name)))throw new Error('环境变量名称非法或为平台保留变量');
  const key=b.target+':'+name;
  if(used.has(key))throw new Error('参数映射目标重复：'+name);used.add(key);
  if(b.target==='argv'&&command.some(arg=>arg===name||arg.startsWith(name+'=')))throw new Error('基础命令已包含映射参数：'+name+'，请只保留一处');
  if(b.target==='env'&&(Object.hasOwn(environment,name)||overrides.includes(name)))throw new Error('映射与运行环境变量重复：'+name);
 }
}
export function CommandMappings({kind,bindings,onBindings,parameters,onParameters}:{kind:string;bindings:PlatformBinding[];onBindings:(v:PlatformBinding[])=>void;parameters:ParameterDraft;onParameters:(v:ParameterDraft)=>void}){
 const [selection,setSelection]=useState('');
 const rowRefs=useRef<Record<string,HTMLFieldSetElement|null>>({});
 const sources=[...common,...(kind==='server'?server:client)];
 const business=parameters.enabled?parameters.rows.filter(r=>r.name):[];
 const selectedBusiness=business.find(r=>selection==='business:'+r.id);
 const alreadyMapped=!!selectedBusiness?.definition.binding&&selectedBusiness.definition.binding.target!=='input';
 const rows=[...bindings.map((b,i)=>({id:'p'+i,label:sources.find(s=>s[0]===b.source)?.[1]||b.source,b,update:(patch:Partial<PlatformBinding>)=>onBindings(bindings.map((v,n)=>n===i?{...v,...patch}:v)),remove:()=>onBindings(bindings.filter((_,n)=>n!==i)),type:'string'})),
 ...business.filter(r=>r.definition.binding&&r.definition.binding.target!=='input').map(r=>({id:r.id,label:'业务参数：'+r.name,b:r.definition.binding as PlatformBinding,type:r.definition.type,update:(patch:Partial<PlatformBinding>)=>onParameters({...parameters,rows:parameters.rows.map(v=>v.id===r.id?{...v,definition:{...v.definition,binding:{...v.definition.binding,...patch}}}:v)}),remove:()=>onParameters({...parameters,rows:parameters.rows.map(v=>v.id===r.id?{...v,definition:{...v.definition,binding:{target:'input'}}}:v)})}))];
 function add(){if(!selection)return;
  if(selection.startsWith('business:')){const id=selection.slice(9);const row=business.find(r=>r.id===id);
   if(row?.definition.binding&&row.definition.binding.target!=='input'){
    rowRefs.current[id]?.scrollIntoView({block:'center'});rowRefs.current[id]?.focus();setSelection('');return;
   }
   onParameters({...parameters,rows:parameters.rows.map(r=>r.id===id?{...r,definition:{...r.definition,binding:{target:'argv',name:'--'+r.name.replaceAll('_','-'),encoding:['object','array'].includes(r.definition.type)?'json':'literal'}}}:r)})}
  else{const s=sources.find(s=>s[0]===selection);if(s)onBindings([...bindings,{source:s[0],target:'argv',name:'--'+s[2],encoding:'literal'}])}setSelection('');
 }
 return <div><h3>动态参数映射</h3><p>选择程序需要的参数，再填写程序接收的名称。平台在启动时追加参数或设置环境变量；不需要在基础命令中重复填写。业务字段先在上方定义；已映射字段也可在此选择并定位修改。</p>
 {!parameters.enabled?<p className="hint">要映射业务参数，请先在上方“业务参数”中启用类型化业务参数并添加字段。</p>:!business.length?<p className="hint">请先在上方添加业务字段并填写字段名称。</p>:null}
 <div className="row"><Field label="选择要传递的参数"><select value={selection} onChange={e=>setSelection(e.target.value)}><option value="">请选择参数来源</option><optgroup label="平台参数">{sources.filter(s=>!bindings.some(b=>b.source===s[0])).map(s=><option key={s[0]} value={s[0]}>{s[1]}</option>)}</optgroup><optgroup label="业务参数">{business.map(r=><option key={r.id} value={'business:'+r.id}>{r.name}{r.definition.binding&&r.definition.binding.target!=='input'?'（已映射）':''}</option>)}</optgroup></select></Field><button type="button" disabled={!selection} onClick={add}>{alreadyMapped?'查看已有映射':'添加参数映射'}</button></div>
 {rows.map((row,i)=><fieldset key={row.id} ref={element=>{rowRefs.current[row.id]=element}} tabIndex={-1}><legend>参数映射 {i+1} · {row.label}</legend><div className="row"><Field label="传递到"><select value={row.b.target} onChange={e=>row.update({target:e.target.value,name:e.target.value==='argv'?'--parameter':'PARAMETER',encoding:['object','array'].includes(row.type)?'json':'literal'})}><option value="argv">命令行参数</option><option value="env">环境变量</option></select></Field><Field label="程序接收的参数名"><input value={row.b.name||''} onChange={e=>row.update({name:e.target.value})} placeholder={row.b.target==='argv'?'--server-url':'SERVER_URL'}/></Field><button type="button" onClick={row.remove}>移除映射 {i+1}</button></div></fieldset>)}
 <details><summary>参数传递预览</summary>{rows.length?<ul>{rows.map(r=><li key={r.id}>{r.label} → {r.b.target==='env'?'环境变量':'命令行参数'} {r.b.name}（{r.b.encoding||'literal'}）</li>)}</ul>:<p>未添加映射，程序可从 MINI_EVAL_INPUT 输入文件读取参数。</p>}<p>实际值在提交和调度后确定；SDK 每回合的编号与种子还可读取 MINI_EVAL_EPISODE_ID 和 MINI_EVAL_EPISODE_SEED。</p></details></div>;
}
