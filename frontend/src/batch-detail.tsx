import React,{useEffect,useState} from 'react';
import {api} from './api';
import {usePoll} from './poll';
import {ArtifactList,EpisodeList} from './cs';

export const statusLabels:Record<string,string>={PENDING:'排队中',RESERVED:'已分配',STARTING:'正在启动',RUNNING:'运行中',SUCCEEDED:'成功',FAILED:'失败',TIMED_OUT:'超时',CANCELLED:'已取消',CANCEL_REQUESTED:'取消中'};
type Reason={code:string;message:string};
type Case={job_id:string;case_id:string;task_name:string;status:string;attempt:number;run_id:string;episodes:number;seed:number;episode_reporting:boolean;progress:{completed:number;successes:number};node_name:string|null;gpu_index:number|null;reason_codes:string[];waiting:null|{reasons:Reason[];nodes:{node_name:string;gpu_index:number;reasons:Reason[]}[]};config:{pool_name:string;endpoint:string;client_name:string;client_version:string;gpu_mode:string;requested_vram_gb:number};error:string|null};
type Monitor={checked_at:string;id:string;title:string;max_concurrent:number;total:number;finished:number;total_episodes:number;cases:Case[];summary:{states:Record<string,number>;completed_episodes:number;successes:number;goal_failures:number;episode_errors:number;execution_failures:number;unreported_tasks:number;success_rate:number|null}};
type RetryResult={retried:number;skipped:number;items:{job_id:string;outcome:string;message?:string;attempt:number}[]};
const eligible=(c:Case)=>['FAILED','TIMED_OUT'].includes(c.status);
type Filters={status:string;reason:string;query:string};
export function BatchDetail({id,onBack,onJob,savedFilters}:{savedFilters:React.MutableRefObject<Record<string,Filters>>;id:string;onBack:()=>void;onJob:(id:string)=>void}) {
  const {data:b,error}=usePoll<Monitor>('batches/'+id+'/monitor');
  const [status,setStatus]=useState(savedFilters.current[id]?.status||''),[reason,setReason]=useState(savedFilters.current[id]?.reason||''),[query,setQuery]=useState(savedFilters.current[id]?.query||''),[job,setJob]=useState('');
  useEffect(()=>{savedFilters.current[id]={status,reason,query};},[id,status,reason,query,savedFilters]);
  const [selected,setSelected]=useState<Record<string,string>>({}),[busy,setBusy]=useState(false),[retryError,setRetryError]=useState(''),[result,setResult]=useState<RetryResult|null>(null);
  const cases=b?.cases.filter(c=>(!status||c.status===status)&&(!reason||c.reason_codes.includes(reason))&&(`${c.case_id} ${c.task_name}`).includes(query))||[];
  const reasons=new Map<string,string>();b?.cases.forEach(c=>[...(c.waiting?.reasons||[]),...(c.waiting?.nodes.flatMap(n=>n.reasons)||[])].forEach(r=>reasons.set(r.code,r.message)));
  async function retry(){setBusy(true);setRetryError('');try{
    const r=await api<RetryResult>('batches/'+id+'/retry-failed','POST',{items:Object.entries(selected).map(([job_id,expected_run_id])=>({job_id,expected_run_id}))});
    setResult(r);setSelected({});
  }catch(e){setRetryError(String(e)+'；保留原尝试选择，可安全再次提交。');}finally{setBusy(false);}}
  return <section className="panel" aria-label="批次详情"><button onClick={onBack}>返回批次列表</button>
    {error&&<p className="error" role="alert">监控更新失败：{error}。将自动重试。</p>}
    {!b&&!error&&<p>正在加载批次…</p>}
    {b&&<><h2>{b.title}</h2><p className="hint">最近更新：{b.checked_at?new Date(b.checked_at).toLocaleString():'—'}</p><p>任务已结束 {b.finished}/{b.total} · 最大并行 {b.max_concurrent}</p>
      <p>{Object.entries(b.summary.states).map(([state,n])=>`${statusLabels[state]||state} ${n}`).join(' · ')}</p>
      <p>已完成回合 {b.summary.completed_episodes}/{b.total_episodes} · 目标成功 {b.summary.successes} · 目标失败 {b.summary.goal_failures} · 回合异常 {b.summary.episode_errors} · 执行失败/超时 {b.summary.execution_failures}</p>
      <p>目标成功率 {b.summary.success_rate===null?'暂无数据':(b.summary.success_rate*100).toFixed(1)+'%'} · 未上报任务 {b.summary.unreported_tasks}</p>
      <p className="hint">汇总仅统计每项最新尝试，成功率按已完成回合加权。目标失败不等于执行失败；历史尝试可在任务排查中查看。</p>
      <div className="submit-grid"><label>查找任务<input value={query} onChange={e=>setQuery(e.target.value)}/></label>
        <label>任务状态<select aria-label="任务状态" value={status} onChange={e=>setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(statusLabels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
        <label>等待原因<select aria-label="等待原因" value={reason} onChange={e=>setReason(e.target.value)}><option value="">全部原因</option>{Array.from(reasons).map(([k,v])=><option key={k} value={k}>{v}</option>)}{reason&&!reasons.has(reason)&&<option value={reason}>{reason}（已消除）</option>}</select></label></div>
      <button disabled={busy} onClick={()=>setSelected(old=>({...old,...Object.fromEntries(cases.filter(eligible).map(c=>[c.job_id,old[c.job_id]||c.run_id]))}))}>选择可补跑任务</button>
      <button disabled={busy||!Object.keys(selected).length} onClick={()=>void retry()}>补跑选中 {Object.keys(selected).length} 项</button>
      <button disabled={busy||!Object.keys(selected).length} onClick={()=>setSelected({})}>清空选择</button>
      {retryError&&<p role="alert" className="error">{retryError}</p>}
      {result&&<div role="status"><p>已补跑 {result.retried} 项，跳过 {result.skipped} 项。</p>{result.items.map(r=><p key={r.job_id}>{b.cases.find(c=>c.job_id===r.job_id)?.case_id}: {r.outcome==='retried'?`已创建尝试 ${r.attempt}`:r.message}</p>)}</div>}
      <table><thead><tr><th>补跑</th><th>任务</th><th>状态 / 位置</th><th>回合</th><th>等待诊断 / 配置</th><th>排查</th></tr></thead><tbody>{cases.map(c=><tr key={c.job_id}>
        <td><input type="checkbox" aria-label={'选择补跑 '+c.case_id} checked={!!selected[c.job_id]} disabled={busy||(!eligible(c)&&!selected[c.job_id])} onChange={e=>setSelected(old=>{const next={...old};if(e.target.checked)next[c.job_id]=c.run_id;else delete next[c.job_id];return next;})}/></td>
        <td>{c.case_id}<small>{c.task_name} · 种子 {c.seed}</small></td><td>{statusLabels[c.status]}<small>{c.node_name||'未分配'}{c.gpu_index!==null?' / GPU '+c.gpu_index:''} · 尝试 {c.attempt}</small></td>
        <td>{c.episode_reporting?`${c.progress.completed}/${c.episodes}，目标成功 ${c.progress.successes}`:'客户端未上报回合'}</td>
        <td>{c.waiting&&<><p>{c.waiting.reasons.map(r=>r.message).join('；')||'等待可用节点'}</p><details><summary>节点诊断</summary>{c.waiting.nodes.map(n=><p key={n.node_name+':'+n.gpu_index}>{n.node_name} / GPU {n.gpu_index}: {n.reasons.map(r=>r.message).join('；')||'符合要求'}</p>)}</details></>}
          <details><summary>执行配置</summary><p>{c.config.pool_name} · {c.config.client_name}/{c.config.client_version}</p><p>{c.config.endpoint}</p><p>{c.config.gpu_mode} · 显存 {c.config.requested_vram_gb} GiB</p></details>{c.error&&<p>{c.error}</p>}</td>
        <td><button onClick={()=>setJob(c.job_id)}>排查任务</button><button onClick={()=>onJob(c.job_id)}>详情 / 重试</button></td></tr>)}</tbody></table>
      {!cases.length&&<p>没有符合筛选条件的任务。</p>}
      {job&&<AttemptInspector key={job} jobId={job} onClose={()=>setJob('')}/>}
    </>}
  </section>;
}
function AttemptInspector({jobId,onClose}:{jobId:string;onClose:()=>void}) {
  const {data,error}=usePoll<{attempts:{id:string;attempt:number;status:string}[]}>('jobs/'+jobId);
  const [run,setRun]=useState('');
  useEffect(()=>{if(!run&&data?.attempts.length)setRun(data.attempts.at(-1)!.id);},[data,run]);
  return <section aria-label="任务排查"><h3>执行尝试与结果</h3><button onClick={onClose}>关闭任务排查</button>{error&&<p role="alert">{error}</p>}
    <label>执行尝试<select aria-label="执行尝试" value={run} onChange={e=>setRun(e.target.value)}>{data?.attempts.map(r=><option key={r.id} value={r.id}>尝试 {r.attempt} · {statusLabels[r.status]||r.status}</option>)}</select></label>
    {run&&<React.Fragment key={run}><AttemptLogs jobId={jobId} runId={run}/><EpisodeList jobId={jobId} runId={run}/><ArtifactList jobId={jobId} runId={run}/></React.Fragment>}
  </section>;
}
function AttemptLogs({jobId,runId}:{jobId:string;runId:string}) {
  const [logs,setLogs]=useState(''),[error,setError]=useState('');
  useEffect(()=>{let active=true,cursor=-1,timer:ReturnType<typeof setTimeout>;
    async function load(){try{const r=await api<{chunks:{content:string}[];next_after:number}>(`jobs/${jobId}/logs?run_id=${runId}&after=${cursor}&limit=100`);if(active){cursor=r.next_after;setLogs(old=>(old+r.chunks.map(c=>c.content).join('')).slice(-262144));setError('');}}catch(e){if(active)setError(String(e));}finally{if(active)timer=setTimeout(load,2000);}}
    void load();return()=>{active=false;clearTimeout(timer);};
  },[jobId,runId]);
  return <><h3>执行日志</h3>{error&&<p role="alert">{error}</p>}<pre className="logs">{logs||'暂无日志'}</pre></>;
}
