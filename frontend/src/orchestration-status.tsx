import React,{useState} from 'react';
import {api} from './api';
import {usePoll} from './poll';
type Service={id:string;name:string;state:string;endpoint:string;revision:number;error:string|null;cleanup_pending:boolean;service_key:string};
type Detail={cases:{case_id:string;endpoint:string|null;service_key:string|null;service_instance_id:string|null;service_execution_id:string|null;waiting_service:boolean}[];orchestration:{service_mode:string;stop_after_batch:boolean;services:Service[];definitions:{key:string;mode:string;parameters?:Record<string,unknown>;resources?:Record<string,unknown>;template?:{name:string;version:string}}[]}};
export function OrchestrationStatus({id}:{id:string}){
 const {data,error}=usePoll<Detail>('orchestrations/'+id),[message,setMessage]=useState(''),[logs,setLogs]=useState(''),[busy,setBusy]=useState(false);
 async function action(service:Service,action:'start'|'stop'){setBusy(true);setMessage('');try{await api(`orchestrations/${id}/services/${service.id}/control`,'POST',{action,expected_revision:service.revision});setMessage(action==='stop'?'已请求停止，等待服务清理。':'已请求恢复，等待调度。')}catch(e){setMessage(String(e))}finally{setBusy(false)}}
 async function read(service:Service){try{const r=await api<{content:string}>(`orchestrations/${id}/services/${service.id}/logs`);setLogs(service.name+'\n'+r.content);setMessage('')}catch(e){setMessage(String(e))}}
 return <section aria-label="客户端与服务关系"><h3>客户端与服务关系</h3>{error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
 {data&&<><p>{data.orchestration.service_mode==='managed'?(data.orchestration.stop_after_batch?'客户端全部结束并清理后停止服务':'批次结束保留服务，请按需手动停止'):data.orchestration.service_mode==='external'?'外部服务由用户维护':'普通批处理无需服务'}</p>
 {data.orchestration.definitions.map(d=><details key={d.key} className="batch-parameters"><summary>{d.key} · {d.mode==='exclusive'?'独占访问':'共享访问'} · 服务端运行参数</summary><p>{d.template?.name} / {d.template?.version}</p><h4>业务参数</h4><pre>{JSON.stringify(d.parameters||{},null,2)}</pre><h4>资源申请</h4><pre>{JSON.stringify(d.resources||{},null,2)}</pre></details>)}
 <div className="table-scroll"><table><thead><tr><th>任务</th><th>分配的服务</th><th>服务执行</th></tr></thead><tbody>{data.cases.map(c=><tr key={c.case_id}><td>{c.case_id}</td><td>{c.endpoint||'等待分配'}{c.waiting_service&&<small>等待服务健康</small>}</td><td>{c.service_key||'—'}<small>{c.service_execution_id||'—'}</small></td></tr>)}</tbody></table></div>
 {data.orchestration.services.map(s=><div key={s.id}><h4>{s.name} · {s.state}</h4><p>{s.endpoint}{s.cleanup_pending?' · 执行资源尚未释放':''}</p>{s.error&&<p>{s.error}</p>}<button onClick={()=>void read(s)}>查看 {s.name} 日志</button><button disabled={busy} onClick={()=>void action(s,'stop')}>停止 {s.name}</button><button disabled={busy||s.cleanup_pending} onClick={()=>void action(s,'start')}>恢复 {s.name}</button></div>)}</>}{logs&&<><button onClick={()=>setLogs('')}>关闭服务日志</button><pre className="logs">{logs}</pre></>}
 </section>;
}
