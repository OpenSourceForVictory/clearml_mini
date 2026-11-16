import React,{useEffect,useState} from 'react';
import {api} from './api';
export function BatchParameters({cases,maxConcurrent}:{cases:{job_id:string;case_id:string;task_name:string}[];maxConcurrent:number}) {
 const [selected,setSelected]=useState(cases[0]?.job_id||''),[data,setData]=useState<any>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true;setData(null);setError('');if(selected)void api('jobs/'+selected).then(v=>{if(active)setData(v)}).catch(e=>{if(active)setError(String(e))});return()=>{active=false}},[selected]);
 const spec=data?.client_spec;
 return <section className="batch-parameters" aria-label="本次运行参数"><h3>本次运行参数</h3><p>最大并发任务数：{maxConcurrent}。显示提交时保存的配置，模板后续修改不会改变本次运行。</p>
 <label>查看任务参数<select value={selected} onChange={e=>setSelected(e.target.value)}>{cases.map(c=><option key={c.job_id} value={c.job_id}>{c.case_id} · {c.task_name}</option>)}</select></label>
 {error&&<p role="alert">参数读取失败：{error}</p>}{!cases.length&&<p>此批次没有任务。</p>}
 {spec&&<><dl><dt>任务名</dt><dd>{spec.task_name}</dd><dt>回合 / 种子</dt><dd>{data.episodes} / {spec.seed}</dd><dt>客户端模板</dt><dd>{spec.template?.name} / {spec.template?.version}</dd><dt>服务地址</dt><dd>{data.attempts?.at(-1)?.endpoint||spec.endpoint||'由平台分配'}</dd></dl>
 <h4>业务参数</h4><pre>{JSON.stringify(spec.parameters||{},null,2)}</pre><h4>资源申请</h4><pre>{JSON.stringify(spec.resources||{gpu_mode:data.gpu_mode,requested_vram_gb:data.requested_vram_gb},null,2)}</pre>
 <details><summary>完整客户端运行配置</summary><pre>{JSON.stringify(spec,null,2)}</pre></details></>}
 </section>;
}
