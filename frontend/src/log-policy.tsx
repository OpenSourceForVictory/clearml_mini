import React, {useEffect, useRef, useState} from 'react';
import {api} from './api';
import {usePoll} from './poll';
type Policy={segment_bytes:number;scope_bytes:number;execution_bytes:number;node_bytes:null};
type Value={revision:number;policy:Policy};
type View=Value&{slots:{id:string;device_index:number;accelerator_type:string;supported:boolean;report?:{status:string;error?:string;applied_revision:number}}[]};
const fields=[['segment_bytes','日志分片'],['scope_bytes','单份日志'],['execution_bytes','单次执行']] as const;
export function LogPolicySettings({id}:{id:string}){
 const path=`admin/nodes/${id}/log-policy`,{data,error}=usePoll<View>(path);
 const [draft,setDraft]=useState<Value|null>(null),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{if(data?.policy&&!draft)setDraft({revision:data.revision,policy:{...data.policy}});},[data,draft]);
 const stale=!!draft&&!!data&&data.revision>draft.revision;
 const p=draft?.policy;
 const valid=!!p&&fields.every(([k])=>Number.isSafeInteger(p[k]))&&p.segment_bytes>=65536&&p.segment_bytes<=p.scope_bytes&&p.scope_bytes<=p.execution_bytes&&p.execution_bytes<=1024**4;
 async function save(){if(!draft||busy)return;setBusy(true);setMessage('');try{
  const value=await api<Value>(path,'PUT',{expected_revision:draft.revision,policy:draft.policy});
  if(alive.current){setDraft(value);setMessage('配置已保存，等待各卡 Worker 回报生效版本；已有执行保持原配置。');}
 }catch(e){if(alive.current)setMessage(String(e));}finally{if(alive.current)setBusy(false);}}
 return <fieldset aria-label="独立日志预算"><legend>独立日志预算</legend>
 <p>整机日志总量暂不限制。达到单份或单次执行预算时轮转日志；调整仅用于后续领取的执行，不追溯清理历史日志。</p>
 {error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
 {draft&&<>{fields.map(([key,label])=><label key={key}>{label}（MiB）<input aria-label={`${label}（MiB）`} type="number" min={0.0625} step="any" disabled={busy} value={draft.policy[key]/1024**2} onChange={e=>setDraft({...draft,policy:{...draft.policy,[key]:Number(e.target.value)*1024**2}})}/></label>)}
 {!valid&&<p role="alert">需满足：分片至少 64 KiB，分片 ≤ 单份日志 ≤ 单次执行，最大 1 TiB。</p>}
 {stale&&<p role="alert">配置版本已变化，草稿已保留，请重新加载后确认。</p>}
 <button disabled={busy||stale||!valid} onClick={()=>void save()}>保存日志预算</button>
 <button disabled={busy||!data} onClick={()=>data&&setDraft({revision:data.revision,policy:{...data.policy}})}>重新加载日志预算</button></>}
 <p>平台期望版本：{data?.revision??'—'}</p>
 <ul>{data?.slots?.map(s=><li key={s.id}>{s.accelerator_type.toUpperCase()} {s.device_index}：{!s.supported?'需要升级 Worker':s.report?.status==='failed'?`应用失败：${s.report.error||'未知错误'}`:s.report?.applied_revision===data.revision?'已生效':'等待同步'}；生效版本 {s.report?.applied_revision??'尚未回报'}</li>)}</ul>
 </fieldset>;
}
