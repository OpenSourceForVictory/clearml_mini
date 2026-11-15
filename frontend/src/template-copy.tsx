import React,{useState} from 'react';
import {api} from './api';
import {Field} from './form-fields';
import {ParameterEditor,parameterDraft,parameterSchema} from './parameter-editor';
export type CopyTemplate=Record<string,any>&{id:string;name:string;version:string;revision?:number;status?:string};
const metadata=['id','enabled','archived','owner_id','status','revision','available_to_members','created_at'];
export function TemplateCopy({kind,source,onClose,onPublished}:{kind:'client'|'server';source:CopyTemplate;onClose:()=>void;onPublished:(t:CopyTemplate)=>void}){
 const [name,setName]=useState(source.name),[version,setVersion]=useState(source.status==='draft'?source.version:''),[draft,setDraft]=useState<CopyTemplate|null>(source.status==='draft'?source:null);
 const [parameters,setParameters]=useState(()=>parameterDraft(source.parameter_schema));
 const initial=Object.fromEntries(Object.entries(source).filter(([k])=>!metadata.includes(k)&&!['parameter_schema','name','version'].includes(k)));
 const [runtime,setRuntime]=useState(JSON.stringify(initial,null,2)),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function save(publish:boolean){setBusy(true);setError('');try{
  const definition={...JSON.parse(runtime),name,version,parameter_schema:parameterSchema(parameters)};
  let current=draft;if(!current){current=await api<CopyTemplate>(`runtime-templates/${kind}/${source.id}/clone`,'POST',{name,version});setDraft(current)}
  current=await api<CopyTemplate>(`runtime-templates/${kind}/${current.id}`,'PUT',{expected_revision:current.revision,definition});setDraft(current);
  if(publish){const published=await api<CopyTemplate>(`runtime-templates/${kind}/${current.id}/actions`,'POST',{expected_revision:current.revision,action:'publish'});onPublished(published)}
 }catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="panel" aria-label="复制编辑模板"><h3>复制并修改{kind==='client'?'客户端':'服务端'}模板</h3><p>新版本为独立私有草稿，发布成功后替换当前选择。取消会保留原选择；已保存的草稿可在模板管理中继续编辑。</p>{error&&<p role="alert">{error}</p>}
 <fieldset disabled={busy}><Field label="副本名称"><input value={name} onChange={e=>setName(e.target.value)} maxLength={100}/></Field><Field label="新版本"><input value={version} onChange={e=>setVersion(e.target.value)} maxLength={100}/></Field>
 <ParameterEditor value={parameters} onChange={setParameters}/><details><summary>平台映射与运行配置（高级）</summary><Field label="完整运行配置 JSON"><textarea rows={14} value={runtime} onChange={e=>setRuntime(e.target.value)}/></Field></details>
 <button type="button" disabled={!name||!version} onClick={()=>void save(false)}>保存草稿</button><button type="button" disabled={!name||!version} onClick={()=>void save(true)}>保存并发布此版本</button><button type="button" onClick={onClose}>取消编辑</button>{draft&&<p role="status">草稿 {draft.name} / {draft.version} · 修订 {draft.revision}</p>}</fieldset></section>;
}
