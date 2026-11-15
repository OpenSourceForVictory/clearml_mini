import React from 'react';
import {Field} from './form-fields';
import {BusinessValues,ValuesDraft,ParameterSchema,parseValues} from './business-values';
import {CopyTemplate} from './template-copy';
export type ServerTemplate=CopyTemplate&{parameter_schema?:ParameterSchema;enabled:boolean;protocol:string;execution_mode?:string};
export type ServiceDraft={cpus:string;ram_gb:string;key:string;template_id:string;pool_id:string;mode:'shared'|'exclusive';max_instances:number;parameters:ValuesDraft;environment:string;gpu_mode:'shared'|'exclusive';requested_vram_gb:number};
export const newService=(index=0):ServiceDraft=>({cpus:'',ram_gb:'',key:index?'service'+(index+1):'default',template_id:'',pool_id:'',mode:'shared',max_instances:1,parameters:{},environment:'{}',gpu_mode:'exclusive',requested_vram_gb:0});
export function servicePayload(rows:ServiceDraft[],templates:ServerTemplate[],fallbackPool:string){return rows.map(row=>{
 const t=templates.find(t=>t.id===row.template_id);if(!t)throw Error('请选择可用的服务端模板');
 const environment=JSON.parse(row.environment);if(!environment||Array.isArray(environment)||typeof environment!=='object'||Object.values(environment).some(v=>typeof v!=='string'))throw Error('服务端运行变量必须是字符串键值对象');
 return {key:row.key,template_id:row.template_id,pool_id:row.pool_id||fallbackPool,mode:row.mode,max_instances:row.max_instances,parameters:parseValues(t.parameter_schema,row.parameters),environment,resources:{...(row.cpus?{cpus:Number(row.cpus)}:{}),...(row.ram_gb?{ram_gb:Number(row.ram_gb)}:{}),gpu_mode:row.gpu_mode,requested_vram_gb:row.requested_vram_gb}};
})}
export function ManagedServices({value,onChange,templates,pools,onCopy}:{value:ServiceDraft[];onChange:(rows:ServiceDraft[])=>void;templates:ServerTemplate[];pools:{id:string;name:string}[];onCopy:(t:ServerTemplate)=>void}){
 function update(i:number,patch:Partial<ServiceDraft>){onChange(value.map((s,n)=>n===i?{...s,...patch}:s))}
 return <><p>平台自动选机器、分配端口并等待健康，再将服务地址传给客户端。访问独占与 GPU 独占分别配置。</p>{value.map((row,i)=>{const template=templates.find(t=>t.id===row.template_id);return <fieldset key={i}><legend>服务定义 {i+1}</legend>
 <div className="submit-grid"><Field label={`服务 ${i+1} 逻辑名称`}><input required value={row.key} pattern="[A-Za-z][A-Za-z0-9_-]{0,63}" onChange={e=>update(i,{key:e.target.value})}/></Field>
 <Field label={`服务 ${i+1} 模板`}><select required value={row.template_id} onChange={e=>update(i,{template_id:e.target.value,parameters:{}})}><option value="">选择服务端模板</option>{templates.filter(t=>t.enabled&&t.status!=='draft').map(t=><option key={t.id} value={t.id}>{t.name} / {t.version} · {t.execution_mode==='host'?'宿主机':'Docker'}</option>)}</select></Field>
 <Field label={`服务 ${i+1} 访问模式`}><select value={row.mode} onChange={e=>update(i,{mode:e.target.value as ServiceDraft['mode']})}><option value="shared">共享：多个客户端复用</option><option value="exclusive">独占：每实例同时一个客户端</option></select></Field>
 <Field label={`服务 ${i+1} 实例上限`}><input type="number" required min={1} max={20} value={row.max_instances} onChange={e=>update(i,{max_instances:Number(e.target.value)})}/></Field></div>
 {template&&<><button type="button" onClick={()=>onCopy(template)}>复制修改服务模板 {i+1}</button><BusinessValues label={`服务 ${i+1} 业务参数`} schema={template.parameter_schema} value={row.parameters} onChange={parameters=>update(i,{parameters})}/></>}
 <details><summary>服务资源与运行变量</summary><Field label={`服务 ${i+1} 资源池`}><select value={row.pool_id} onChange={e=>update(i,{pool_id:e.target.value})}><option value="">跟随客户端资源池</option>{pools.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
 <Field label={`服务 ${i+1} CPU 核数`}><input type="number" min={1} step={1} value={row.cpus} placeholder="资源池默认值" onChange={e=>update(i,{cpus:e.target.value})}/></Field>
 <Field label={`服务 ${i+1} 内存 GiB`}><input type="number" min={1} step={1} value={row.ram_gb} placeholder="资源池默认值" onChange={e=>update(i,{ram_gb:e.target.value})}/></Field>
 <Field label={`服务 ${i+1} GPU 模式`}><select value={row.gpu_mode} onChange={e=>update(i,{gpu_mode:e.target.value as ServiceDraft['gpu_mode']})}><option value="exclusive">独占</option><option value="shared">共享</option></select></Field>
 <Field label={`服务 ${i+1} 显存 GiB`}><input type="number" required min={0} value={row.requested_vram_gb} onChange={e=>update(i,{requested_vram_gb:Number(e.target.value)})}/></Field>
 <Field label={`服务 ${i+1} 运行变量 JSON`}><textarea value={row.environment} onChange={e=>update(i,{environment:e.target.value})}/></Field></details>
 <button type="button" disabled={value.length===1} onClick={()=>onChange(value.filter((_,n)=>n!==i))}>移除此服务定义</button></fieldset>})}
 <button type="button" disabled={value.length>=20} onClick={()=>onChange([...value,newService(value.length)])}>添加服务定义</button><p className="hint">默认任务可使用任一兼容服务；可在任务清单的 service_keys 中指定逻辑名称。</p></>;
}
