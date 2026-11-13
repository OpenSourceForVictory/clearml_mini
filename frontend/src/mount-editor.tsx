import React from 'react';
import {Field} from './form-fields';
export type MountRow={name:string;source:string;target:string;read_only:boolean;required:boolean};
export type DirectoryRow={name:string;path:string;writable:boolean};
export function mountDraft(value:Record<string,any>={}):MountRow[]{return Object.entries(value).map(([name,v])=>({name,read_only:true,required:true,...v}));}
export function directoryDraft(value:Record<string,any>={}):DirectoryRow[]{return Object.entries(value).map(([name,v])=>({name,writable:false,...v}));}
function values<T extends {name:string}>(rows:T[]):Record<string,Omit<T,'name'>>{
 const result:Record<string,Omit<T,'name'>>={};
 for(const {name,...v} of rows){if(!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name))throw new Error('逻辑名称须以字母开头，只含字母、数字和下划线');if(Object.hasOwn(result,name))throw new Error('逻辑名称重复：'+name);result[name]=v;}
 return result;
}
export const mountValues=(rows:MountRow[])=>values(rows);
export const directoryValues=(rows:DirectoryRow[])=>values(rows);
export function MountEditor({rows,onChange}:{rows:MountRow[];onChange:(v:MountRow[])=>void}){
 const set=(i:number,v:Partial<MountRow>)=>onChange(rows.map((r,n)=>n===i?{...r,...v}:r));
 return <fieldset><legend>通用目录挂载</legend><p>引用平台工作区、输出区、只读缓存/资产，或管理员批准的目录。不同机器可以映射不同路径；需要 Worker v9。</p>
 {rows.map((r,i)=><fieldset key={i}><legend>挂载 {i+1}</legend><div className="row">
 <Field label={`挂载 ${i+1} 名称`}><input value={r.name} onChange={e=>set(i,{name:e.target.value})} required/></Field>
 <Field label={`挂载 ${i+1} 来源`}><input value={r.source} placeholder="directories:dataset" onChange={e=>set(i,{source:e.target.value})} required/></Field>
 <Field label={`挂载 ${i+1} 容器路径`}><input value={r.target} placeholder="/data/dataset" onChange={e=>set(i,{target:e.target.value})} required/></Field></div>
 <label><input type="checkbox" checked={r.read_only} onChange={e=>set(i,{read_only:e.target.checked})}/>只读挂载 {i+1}</label>{' '}
 <label><input type="checkbox" checked={r.required} onChange={e=>set(i,{required:e.target.checked})}/>必需映射 {i+1}</label>{' '}
 <button type="button" onClick={()=>onChange(rows.filter((_,n)=>n!==i))}>删除挂载 {i+1}</button></fieldset>)}
 <button type="button" onClick={()=>onChange([...rows,{name:'',source:'directories:',target:'',read_only:true,required:true}])}>添加目录挂载</button>
 <p className="muted">来源：work、output、cache、assets:逻辑名、directories:逻辑名。可写共享目录必须经节点管理员批准；取消“必需”仅允许节点没有此映射，已配置但不可用仍会阻止启动。</p>
 {!!rows.length&&<details><summary>挂载声明预览</summary><ul>{rows.map((r,i)=><li key={i}>{r.source} → {r.target} · {r.read_only?'只读':'可写'} · {r.required?'必需':'可选'}</li>)}</ul><p>宿主机路径在分配节点后解析；预览不代表实际挂载已就绪。</p></details>}
 </fieldset>;
}
export function DirectoryEditor({rows,onChange}:{rows:DirectoryRow[];onChange:(v:DirectoryRow[])=>void}){
 const set=(i:number,v:Partial<DirectoryRow>)=>onChange(rows.map((r,n)=>n===i?{...r,...v}:r));
 return <fieldset><legend>批准的额外目录</legend><p>目录须在这台机器预先存在。默认仅允许只读；批准写入后，不同任务可能同时修改共享内容。</p>
 {rows.map((r,i)=><div key={i} className="row"><Field label={`目录 ${i+1} 逻辑名`}><input value={r.name} onChange={e=>set(i,{name:e.target.value})}/></Field><Field label={`目录 ${i+1} 宿主机路径`}><input value={r.path} onChange={e=>set(i,{path:e.target.value})}/></Field><label><input type="checkbox" checked={r.writable} onChange={e=>set(i,{writable:e.target.checked})}/>批准目录 {i+1} 写入</label><button type="button" onClick={()=>onChange(rows.filter((_,n)=>n!==i))}>删除目录 {i+1}</button></div>)}
 <button type="button" onClick={()=>onChange([...rows,{name:'',path:'',writable:false}])}>添加批准目录</button></fieldset>;
}
