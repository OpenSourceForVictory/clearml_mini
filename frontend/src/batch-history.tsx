import {ExecutionOutcome,ProtocolOutcome,type Outcomes} from './result-outcomes';
import React,{useState,useRef,useEffect} from 'react';
import {api} from './api';
import './batch-history.css';

export type Batch = {outcomes?:Outcomes;id:string;title:string;status:string;scheduling_paused?:boolean;created_at?:string;max_concurrent?:number;total:number;finished:number;succeeded:number;total_episodes:number;cases:{episode_reporting?:boolean;progress?:{completed:number}}[]};
const labels:Record<string,string>={PENDING:'排队中',RUNNING:'运行中',SUCCEEDED:'已结束',FAILED:'已结束（异常）',CANCELLED:'已取消'};
const terminal=new Set(['SUCCEEDED','FAILED','CANCELLED']);
const tone=(status:string)=>status==='SUCCEEDED'?'success':status==='FAILED'?'failure':status==='RUNNING'?'running':'neutral';

export function BatchHistory({batches,page,loading,error,compareIds,onCompareChange,onOpen,onRerun,onPage,onDeleted}:{batches:Batch[];page:number;loading:boolean;error:string;compareIds:string[];onCompareChange:(ids:string[])=>void;onOpen:(id:string)=>void;onRerun?:(id:string)=>void;onPage:(page:number)=>void;onDeleted:(id:string)=>void}) {
 const [query,setQuery]=useState(''),[status,setStatus]=useState(''),[deleting,setDeleting]=useState<Batch|null>(null),[busy,setBusy]=useState(false),[deleteError,setDeleteError]=useState(''),[notice,setNotice]=useState('');
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{if(deleting)dialog.current?.showModal();},[deleting]);
 const visible=batches.filter(b=>(!status||b.status===status)&&(!query.trim()||`${b.title} ${b.id}`.toLowerCase().includes(query.trim().toLowerCase())));
 const remove=async()=>{if(!deleting||busy)return;setBusy(true);setDeleteError('');try{await api('batches/'+deleting.id,'DELETE');onDeleted(deleting.id);setNotice('批次及其任务已删除，审计记录保留。');setDeleting(null);}catch(e){setDeleteError(String(e));}finally{setBusy(false);}};
 return <section className="panel batch-history" aria-label="评测批次记录">
  <div className="history-heading"><div><h2>批次进度</h2><p className="muted">查看执行进度，进入批次查看参数、服务、任务与结果。</p></div><span className="history-live">每 2 秒更新</span></div>
  <div className="history-stats" aria-label="当前页概览">{[['当前页批次',batches.length],['运行中',batches.filter(b=>b.status==='RUNNING').length],['排队中',batches.filter(b=>b.status==='PENDING').length],['已结束',batches.filter(b=>terminal.has(b.status)).length]].map(([name,count])=><div key={name}><span>{name}</span><strong>{count}</strong></div>)}</div>
  <div className="history-toolbar"><label>搜索当前页<input type="search" placeholder="批次名称或 ID" value={query} onChange={e=>setQuery(e.target.value)}/></label><label>当前页状态<select value={status} onChange={e=>setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(labels).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><span className="muted">显示 {visible.length} / {batches.length} 个批次</span></div>
  <div className="history-compare"><span>已选 <strong>{compareIds.length}/2</strong>，可跨页比较</span><div><button disabled={compareIds.length!==2} onClick={()=>{location.hash='/cs/compare/'+compareIds.join('/');}}>比较所选批次</button><button disabled={!compareIds.length} onClick={()=>onCompareChange([])}>清空比较选择</button></div></div>
  {notice&&<p role="status">{notice}</p>}
  {loading&&!batches.length?<p className="history-empty" role="status">正在加载评测批次…</p>:!batches.length?<p className="history-empty">{error?'暂时无法加载批次，请等待重试。':page===1?'尚无评测批次。请从业务评测向导发起评测。':'本页暂无评测批次，请返回上一页。'}</p>:!visible.length?<div className="history-empty"><p>当前页没有匹配的批次。</p><button onClick={()=>{setQuery('');setStatus('');}}>清除筛选</button></div>:<div className="history-table-scroll" role="region" aria-label="批次列表，可横向滚动" tabIndex={0}><table className="history-table"><thead><tr><th scope="col">比较</th><th scope="col">批次名称</th><th scope="col">提交时间</th><th scope="col">状态</th><th scope="col">并发</th><th scope="col">任务进度</th><th scope="col">程序执行</th><th scope="col">回合结果（平台协议）</th><th scope="col">操作</th></tr></thead><tbody>{visible.map(b=>{
   const percent=b.total?Math.min(100,Math.max(0,b.finished/b.total*100)):0;
   return <tr key={b.id} aria-label={'批次 '+b.title}>
    <td><input type="checkbox" aria-label={'比较选择 '+b.title} checked={compareIds.includes(b.id)} disabled={compareIds.length>=2&&!compareIds.includes(b.id)} onChange={e=>onCompareChange(e.target.checked?[...compareIds,b.id]:compareIds.filter(id=>id!==b.id))}/></td>
    <th scope="row"><span className="history-title" title={b.title}>{b.title}</span><span className="history-row-id" title={b.id}>ID: {b.id}</span></th>
    <td className="history-date">{b.created_at?new Date(b.created_at).toLocaleString('zh-CN'):'—'}</td>
    <td><span className={'history-badge '+tone(b.status)}>{labels[b.status]||b.status}</span>{b.scheduling_paused&&<small className="history-paused">调度已暂停</small>}</td>
    <td>{b.max_concurrent??'—'}</td>
    <td className="history-progress"><span>已结束 {b.finished}/{b.total}</span><progress aria-label={b.title+' 任务完成进度'} value={percent} max={100}/><small>任务正常完成 {b.succeeded} · 未结束 {Math.max(0,b.total-b.finished)}</small></td>
    <td><ExecutionOutcome value={b.outcomes}/></td><td><ProtocolOutcome value={b.outcomes}/></td>
    <td><div className="history-actions"><button onClick={()=>onOpen(b.id)}>查看批次</button>{onRerun&&<button onClick={()=>onRerun(b.id)}>基于此批次创建</button>}<button className="history-delete" disabled={!terminal.has(b.status)} title={!terminal.has(b.status)?'请先进入批次取消任务，等待停止并清理后删除':'删除批次及其任务，保留审计数据'} onClick={()=>{setDeleting(b);setDeleteError('');setNotice('');}}>删除批次</button></div></td>
   </tr>;
  })}</tbody></table></div>}
  <div className="pagination"><button disabled={page===1||loading} onClick={()=>onPage(page-1)}>上一页</button><span>第 {page} 页</span><button disabled={batches.length<20||loading} onClick={()=>onPage(page+1)}>下一页</button></div>
  {deleting&&<dialog ref={dialog} onCancel={e=>{e.preventDefault();if(!busy)setDeleting(null);}} className="history-confirm" aria-labelledby="delete-batch-title"><h3 id="delete-batch-title">删除评测批次</h3><p>确认删除「{deleting.title}」？</p><p>批次下的任务、运行记录、日志和产物会删除，审计记录和删除状态会保留。运行任务或关联服务尚未清理时无法删除。此操作不清除节点工作目录，不释放磁盘空间。</p>{deleteError&&<p role="alert" className="error">{deleteError}</p>}<button disabled={busy} onClick={()=>void remove()}>{busy?'正在删除…':'确认删除批次'}</button><button disabled={busy} onClick={()=>setDeleting(null)}>暂不删除</button></dialog>}
 </section>;
}
