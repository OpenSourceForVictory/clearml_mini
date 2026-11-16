import React from 'react';
import {Field} from './form-fields';
import {MountRow} from './mount-editor';
export type AssetRow={name:string;target:string};
export function assetValues(rows:AssetRow[]){const result:Record<string,string>={};for(const r of rows){if(!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(r.name))throw new Error('资产名称须以字母开头，只含字母、数字和下划线');if(Object.hasOwn(result,r.name))throw new Error('资产名称重复：'+r.name);result[r.name]=r.target}return result;}
export function TemplateDirectories({mode,assets,onAssets,mounts,onMounts}:{mode:string;assets:AssetRow[];onAssets:(v:AssetRow[])=>void;mounts:MountRow[];onMounts:(v:MountRow[])=>void}){
 const set=(i:number,v:Partial<MountRow>)=>onMounts(mounts.map((r,n)=>n===i?{...r,...v}:r));
 return <fieldset><legend>目录挂载</legend><p>节点管理设置真实磁盘路径，模板只引用逻辑名称。工作、输出和缓存目录由平台提供；资产或额外目录须在目标节点预先登记。注册不会下载数据。</p>
 {assets.map((a,i)=><fieldset key={i}><legend>资产 {i+1}{mode==='docker'?' · 兼容资产声明':''}</legend><Field label={`资产 ${i+1} 逻辑名`}><input value={a.name} onChange={e=>onAssets(assets.map((v,n)=>n===i?{...v,name:e.target.value}:v))}/></Field>{mode==='docker'&&<Field label={`资产 ${i+1} 容器路径`}><input value={a.target} onChange={e=>onAssets(assets.map((v,n)=>n===i?{...v,target:e.target.value}:v))}/></Field>}<p>资产只读且必需；已有命令的 assets 占位符保持有效。</p><button type="button" onClick={()=>onAssets(assets.filter((_,n)=>n!==i))}>删除资产 {i+1}</button></fieldset>)}
 {mode==='host'?<><button type="button" onClick={()=>onAssets([...assets,{name:'',target:''}])}>添加资产引用</button><p>宿主机使用节点独立工作区，资产实际路径由节点解析，无需容器路径。</p></>:<>
 {mounts.map((r,i)=>{const type=r.source.includes(':')?r.source.split(':')[0]:r.source;return <fieldset key={i}><legend>挂载 {i+1}</legend><div className="row"><Field label={`挂载 ${i+1} 名称`}><input value={r.name} onChange={e=>set(i,{name:e.target.value})} required placeholder="例如 dataset"/></Field><Field label={`挂载 ${i+1} 来源类型`}><select value={type} onChange={e=>set(i,{source:['assets','directories'].includes(e.target.value)?e.target.value+':':e.target.value,...(['assets','cache'].includes(e.target.value)?{read_only:true}:{})})}><option value="directories">节点批准的额外目录</option><option value="assets">节点资产</option><option value="work">本次执行工作目录</option><option value="output">本次执行输出目录</option><option value="cache">节点缓存（只读）</option></select></Field>
 {['assets','directories'].includes(type)&&<Field label={`挂载 ${i+1} 逻辑名称`}><input value={r.source.slice(r.source.indexOf(':')+1)} onChange={e=>set(i,{source:type+':'+e.target.value})} placeholder="机器配置中的名称，例如 dataset" required/></Field>}
 <Field label={`挂载 ${i+1} 容器路径`}><input value={r.target} onChange={e=>set(i,{target:e.target.value})} required placeholder="/datasets"/></Field></div>
 <label><input type="checkbox" checked={r.read_only} disabled={['assets','cache'].includes(type)} onChange={e=>set(i,{read_only:e.target.checked})}/>只读挂载 {i+1}</label>{' '}<label><input type="checkbox" checked={r.required} onChange={e=>set(i,{required:e.target.checked})}/>必需映射 {i+1}</label><button type="button" onClick={()=>onMounts(mounts.filter((_,n)=>n!==i))}>删除挂载 {i+1}</button>
 <details><summary>高级来源标识</summary><Field label={`挂载 ${i+1} 来源`}><input value={r.source} onChange={e=>set(i,{source:e.target.value})}/></Field></details></fieldset>})}
 <button type="button" onClick={()=>onMounts([...mounts,{name:'',source:'directories:',target:'',read_only:true,required:true}])}>添加目录挂载</button><p>资产和缓存仅可只读；其他共享目录的写入必须由节点管理员批准。“可选”仅允许未配置映射，已配置但不可用仍会阻止启动。</p>
 </>}
 <details><summary>挂载声明预览</summary><ul>{assets.map((r,i)=><li key={'a'+i}>assets:{r.name} → {mode==='host'?'节点实际路径':r.target} · 只读 · 必需</li>)}{mode==='docker'&&mounts.map((r,i)=><li key={i}>{r.source} → {r.target} · {r.read_only?'只读':'可写'} · {r.required?'必需':'可选'}</li>)}</ul><p>实际宿主机路径在分配节点后解析；预览不代表目录已就绪。</p></details></fieldset>;
}
