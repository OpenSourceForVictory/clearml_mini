import React, {useEffect, useRef, useState} from 'react';
import {api} from './api';
import {usePoll} from './poll';

type Policy = {revision:number; enabled:boolean; days:number};
type Entry = {job_title?:string;attempt?:number;id:string; generation:string; slot_id:string; execution_id:string|null; root_path:string; entry_name:string;
  logical_bytes:number|null; allocated_bytes:number|null; scan_status:string; scanned_at:string; blocked_reason:string|null; last_cleanup_status:string|null};
type Inventory = {items:Entry[]; total:number; policy:Policy; scan_revision:number; slots:{id:string;online:boolean;supported:boolean}[]};
type Operation = {id:string; status:string; source:string; expires_at:string; items:{id:string; data_id:string; status:string; reason:string|null;
  snapshot:{entry_name:string;allocated_bytes:number|null}; receipt:null|{deleted_entries:number;free_before:number|null;free_after:number|null}}[]};
const bytes=(value:number|null)=>value===null?'未知':`${(value/1024/1024).toFixed(1)} MiB`;
const labels:Record<string,string>={preview:'待确认',queued:'已提交，等待 Worker',running:'执行中',succeeded:'已清理',skipped:'已跳过',failed:'失败 / 可能部分删除',completed_with_exceptions:'结束，含跳过或失败',complete:'完整扫描',blocked:'受保护',partial:'扫描未完成',missing:'数据已不存在'};

export function ExecutionDataSettings({id,name}:{id:string;name:string}) {
  const [open,setOpen]=useState(false);
  return <details className="panel" onToggle={e=>setOpen(e.currentTarget.open)}><summary>{name} 执行数据</summary>
    {open&&<DataPanel key={id} id={id}/>}</details>;
}

function DataPanel({id}:{id:string}) {
  const base=`admin/nodes/${id}`;
  const [offset,setOffset]=useState(0),[selected,setSelected]=useState<Record<string,string>>({});
  const {data,error:pollError}=usePoll<Inventory>(`${base}/execution-data?offset=${offset}&limit=50`);
  const [auditOffset,setAuditOffset]=useState(0);
  const {data:operations}=usePoll<Operation[]>(`${base}/execution-data/operations?offset=${auditOffset}&limit=20`);
  const [preview,setPreview]=useState<Operation|null>(null),[policyPreview,setPolicyPreview]=useState<Operation|null>(null);
  const [draft,setDraft]=useState<Policy|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  const alive=useRef(true),pending=useRef(false);
  const [clock,setClock]=useState(Date.now());
  useEffect(()=>{alive.current=true;const timer=setInterval(()=>setClock(Date.now()),1000);return()=>{alive.current=false;clearInterval(timer);};},[]);
  useEffect(()=>{if(data&&!draft)setDraft(data.policy);},[data,draft]);
  async function action(fn:()=>Promise<void>){if(pending.current)return;pending.current=true;setBusy(true);setError('');setMessage('');
    try{await fn();}catch(e){if(alive.current)setError(String(e));}finally{pending.current=false;if(alive.current)setBusy(false);}}
  const stale=!!data&&!!draft&&data.policy.revision!==draft.revision;
  const expired=(op:Operation|null)=>!op||Date.parse(op.expires_at)<=clock;
  function reset(){if(data){setDraft(data.policy);setPolicyPreview(null);}}
  async function policySave(){if(!draft)return;const value=await api<Policy>(`${base}/retention`,'PUT',{
    expected_revision:draft.revision,enabled:draft.enabled,days:draft.days,preview_id:policyPreview?.id});
    if(alive.current){setDraft(value);setPolicyPreview(null);setMessage('策略已保存。已开始的清理会继续完成或恢复对账；关闭策略不会恢复数据。');}}
  return <section aria-label="执行数据管理">
    <p>仅清理已结束评测的本地 work/output。平台结果与已上传产物、公共资产、缓存保持可用。删除不可撤销；占用为估算，硬链接可能使实际回收更少。</p>
    {(error||pollError)&&<p role="alert">{error||pollError}</p>}{message&&<p role="status">{message}</p>}
    {data?.slots.some(s=>!s.supported)&&<p>部分 Worker 需升级到 v6，暂不能清理。</p>}
    {data?.slots.some(s=>!s.online)&&<p>Worker 离线：展示的是历史盘点，不能开始新的清理。</p>}
    <button disabled={busy} onClick={()=>void action(async()=>{await api(`${base}/execution-data/scan`,'POST');if(alive.current)setMessage('扫描已请求，等待 Worker 后台上报。');})}>请求扫描</button>
    <button disabled={busy||!Object.keys(selected).length} onClick={()=>void action(async()=>{const v=await api<Operation>(`${base}/execution-data/preview`,'POST',{entries:Object.entries(selected).map(([id,generation])=>({id,generation}))});if(alive.current)setPreview(v);})}>预览所选清理</button>
    <button disabled={busy} onClick={()=>{setSelected({});setPreview(null);}}>清空选择</button>
    <p>已选 {Object.keys(selected).length} 项（最多 100 项）；扫描请求版本 {data?.scan_revision??'—'}</p>
    <table><thead><tr><th>选择</th><th>执行 / 槽位 / 目录</th><th>逻辑 / 分配占用</th><th>扫描与保护原因</th></tr></thead>
      <tbody>{data?.items.map(d=><tr key={d.id}><td><input type="checkbox" aria-label={`选择 ${d.entry_name}`} checked={!!selected[d.id]}
        disabled={busy||(!selected[d.id]&&Object.keys(selected).length>=100)} onChange={e=>{setPreview(null);setSelected(old=>{const next={...old};if(e.target.checked)next[d.id]=d.generation;else delete next[d.id];return next;});}}/></td>
        <td>{d.job_title&&<>{d.job_title} · 尝试 {d.attempt}<br/></>}{d.execution_id||d.entry_name}<br/>{d.slot_id}<br/><code>{d.root_path}/{d.entry_name}</code></td>
        <td>{bytes(d.logical_bytes)} / {bytes(d.allocated_bytes)}</td><td>{labels[d.scan_status]||d.scan_status}<br/>{d.scanned_at}<br/>{d.blocked_reason||'可预览，执行前再次复核'}{d.last_cleanup_status&&<p>{labels[d.last_cleanup_status]}</p>}</td></tr>)}</tbody></table>
    <p>{data?.total??0} 条；第 {Math.floor(offset/50)+1} 页 <button disabled={!offset||busy} onClick={()=>setOffset(offset-50)}>上一页</button>
      <button disabled={!data||offset+50>=data.total||busy} onClick={()=>setOffset(offset+50)}>下一页</button></p>
    {preview&&<section aria-label="清理预览"><h4>确认清理范围</h4><OperationDetails value={preview}/><p>预览截至 {preview.expires_at}；确认仅提交这次预览，完成时间以 Worker 回执为准。</p>
      <button disabled={busy||expired(preview)||preview.status!=='preview'||!preview.items.some(i=>i.status==='preview')} onClick={()=>void action(async()=>{
        const v=await api<Operation>(`${base}/execution-data/operations/${preview.id}/confirm`,'POST');if(alive.current){setPreview(v);setMessage('已提交清理，尚不代表删除成功。请查看逐项审计。');}})}>确认不可逆清理</button>
      {expired(preview)&&<p>预览已过期，请重新预览。</p>}</section>}
    {draft&&<fieldset><legend>自动保留策略</legend><p>当前：{data?.policy.enabled?'已启用':'关闭'}。按评测结束时间计算，启用会影响符合期限的历史数据；每小时最多 100 项。</p>
      <label><input type="checkbox" checked={draft.enabled} disabled={busy} onChange={e=>{setDraft({...draft,enabled:e.target.checked});setPolicyPreview(null);}}/>启用自动清理</label>
      <label>保留天数 <input aria-label="保留天数" type="number" min={1} max={3650} value={draft.days} disabled={busy} onChange={e=>{setDraft({...draft,days:Number(e.target.value)});setPolicyPreview(null);}}/></label>
      {stale&&<p role="alert">策略版本已变化。草稿已保留，请重新加载后确认。</p>}
      <button disabled={busy} onClick={reset}>重新加载策略</button>
      <button disabled={busy||stale||!Number.isInteger(draft.days)||draft.days<1||draft.days>3650} onClick={()=>void action(async()=>{
        const v=await api<Operation>(`${base}/retention/preview`,'POST',{expected_revision:draft.revision,enabled:draft.enabled,days:draft.days});if(alive.current)setPolicyPreview(v);})}>预览策略影响</button>
      <button disabled={busy||stale||(draft.enabled&&expired(policyPreview))} onClick={()=>void action(policySave)}>{draft.enabled?'确认启用策略':'保存关闭策略'}</button>
      {policyPreview&&<><OperationDetails value={policyPreview}/>{expired(policyPreview)&&<p>策略预览已过期。</p>}</>}
    </fieldset>}
    <h4>最近清理审计</h4>{operations?.map(op=><details key={op.id}><summary>{op.source} · {labels[op.status]||op.status} · {op.id}</summary><OperationDetails value={op}/>
      {op.items.some(i=>i.status==='failed')&&<p>重试失败项：先请求扫描，选择刷新后的条目，再预览并确认。不会自动重复永久失败的操作。</p>}</details>)}
    <button disabled={auditOffset===0||busy} onClick={()=>setAuditOffset(auditOffset-20)}>审计上一页</button>
    <button disabled={!operations||operations.length<20||busy} onClick={()=>setAuditOffset(auditOffset+20)}>审计下一页</button>
  </section>;
}
function OperationDetails({value}:{value:Operation}) {return <ul>{value.items.map(i=><li key={i.id}>{i.snapshot.entry_name} · {bytes(i.snapshot.allocated_bytes)} · {labels[i.status]||i.status} {i.reason}
  {i.receipt&&<span>；已处理 {i.receipt.deleted_entries} 项；磁盘可用空间观测 {bytes(i.receipt.free_before)} → {bytes(i.receipt.free_after)}（不等于精确释放量）</span>}</li>)}</ul>;}
export function JobExecutionData({id}:{id:string}) {const {data}=usePoll<{execution_id:string;status:string}[]>(`jobs/${id}/execution-data`);
  return <>{data?.map(d=><p key={d.execution_id}>尝试 {d.execution_id}：本地数据{labels[d.status]||d.status}。已上传的结果与产物仍可查看。</p>)}</>;}
