import React from 'react';
import {Field} from './form-fields';
export type EnvironmentRow={name:string;value:string;hasDefault:boolean;overridable:boolean};
export function environmentDraft(settings:any):EnvironmentRow[]{
 const env=settings?.environment||{},overrides:string[]=settings?.overridable_env||[];
 return [...new Set([...Object.keys(env),...overrides])].map(name=>({name,value:env[name]??'',hasDefault:Object.hasOwn(env,name),overridable:overrides.includes(name)}));
}
export function environmentValues(rows:EnvironmentRow[]){
 const entries:[string,string][]=[],overridable_env:string[]=[],seen=new Set<string>();
 for(const r of rows){
  if(!/^[A-Za-z_][A-Za-z0-9_-]{0,127}$/.test(r.name)||/^(MINI_EVAL_|NVIDIA_|ASCEND_)/.test(r.name)||['CUDA_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','HIP_VISIBLE_DEVICES','PYTHONPATH','PYTHONHOME'].includes(r.name))throw new Error('环境变量名称非法或为平台保留变量：'+r.name);
  if(seen.has(r.name))throw new Error('环境变量名称重复：'+r.name);seen.add(r.name);
  if(!r.hasDefault&&!r.overridable)throw new Error(r.name+'：请设置默认值或允许提交时填写');
  if(r.value.length>4096||r.value.includes('\0'))throw new Error(r.name+'：值不能含空字符或超过 4096 字符');
  if(r.hasDefault)entries.push([r.name,r.value]);if(r.overridable)overridable_env.push(r.name);
 }
 if(entries.length>64||overridable_env.length>64||entries.reduce((n,[k,v])=>n+k.length+v.length,0)>16384)throw new Error('环境变量超出平台数量或大小上限');
 return {environment:Object.fromEntries(entries),overridable_env};
}
export function EnvironmentEditor({rows,onChange}:{rows:EnvironmentRow[];onChange:(v:EnvironmentRow[])=>void}){
 const update=(i:number,patch:Partial<EnvironmentRow>)=>onChange(rows.map((r,n)=>n===i?{...r,...patch}:r));
 return <fieldset><legend>环境变量</legend><p>例如名称 LOG_LEVEL，值 info，无需填写 JSON 或引号。允许覆盖后，用户可在提交任务时设置该变量。平台身份和设备变量由平台管理。变量名支持中划线，例如 task-config，可用 Python os.environ["task-config"] 读取；Shell 无法直接 export 或用普通变量展开读取这种名称，且部分 Shell 会丢弃它。Shell 程序建议使用下划线。</p>
 {!rows.length&&<p className="muted">尚未设置固定环境变量。动态参数请在启动命令中选择“环境变量”映射。</p>}
 {rows.map((r,i)=><fieldset key={i}><legend>环境变量 {i+1}</legend><div className="row"><Field label={`变量名称 ${i+1}`}><input value={r.name} required placeholder="LOG_LEVEL" onChange={e=>update(i,{name:e.target.value})}/></Field><Field label={`变量值 ${i+1}`}><input value={r.value} disabled={!r.hasDefault} maxLength={4096} placeholder="info（可为空字符串）" onChange={e=>update(i,{value:e.target.value})}/></Field></div><div className="row"><label><input type="checkbox" checked={r.hasDefault} onChange={e=>update(i,{hasDefault:e.target.checked})}/>提供默认值 {i+1}</label><label><input type="checkbox" checked={r.overridable} onChange={e=>update(i,{overridable:e.target.checked})}/>允许提交时覆盖 {i+1}</label><button type="button" onClick={()=>onChange(rows.filter((_,n)=>n!==i))}>删除环境变量 {i+1}</button></div></fieldset>)}
 <button type="button" disabled={rows.length>=64} onClick={()=>onChange([...rows,{name:'',value:'',hasDefault:true,overridable:false}])}>添加环境变量</button>
 </fieldset>;
}
