import {ExecutionPlacement,type Placement} from './execution-placement';
import {ServiceProbeStatus} from './service-probe-status';
import React,{useState} from 'react';
import {api} from './api';
import {usePoll} from './poll';
type Service={placement?:Placement|null;id:string;name:string;state:string;endpoint:string;revision:number;error:string|null;cleanup_pending:boolean;service_key:string};
type Scheduling={key:string;pool_name:string;status:string;reasons:{code:string;message:string}[];nodes:{node_name:string;gpu_index:number;ready:boolean;reasons:{code:string;message:string}[]}[]};
type Detail={cases:{placement?:Placement|null;case_id:string;endpoint:string|null;service_key:string|null;service_instance_id:string|null;service_execution_id:string|null;waiting_service:boolean}[];orchestration:{service_mode:string;stop_after_batch:boolean;services:Service[];scheduling?:Scheduling[];definitions:{key:string;mode:string;parameters?:Record<string,unknown>;resources?:Record<string,unknown>;template?:{name:string;version:string}}[]}};
const serviceStates:Record<string,string>={PENDING:'等待调度',RESERVED:'已分配资源',STARTING:'正在启动或等待健康检查',RUNNING:'进程运行中',HEALTHY:'已就绪',UNHEALTHY:'健康检查未通过',UNKNOWN:'健康状态未知',FAILED:'启动或运行失败',TIMED_OUT:'启动或运行超时',CANCEL_REQUESTED:'停止中',CANCELLED:'已取消',SUCCEEDED:'已结束',STOPPED:'已停止'};
const serviceState=(state:string)=>serviceStates[state]||state;
function SchedulingStatus({definition,diagnosis}:{definition:{key:string};diagnosis?:Scheduling}){
 const ready=diagnosis?.nodes.filter(node=>node.ready).length||0;
 const label=!diagnosis?'等待调度':diagnosis.status==='ready'?'服务端候选就绪，等待两端联合调度':diagnosis.status==='waiting_resources'?'服务端等待资源':'服务端环境未就绪';
 return <div className="service-scheduling" key={definition.key}><p><strong>{definition.key}</strong>：{label}{diagnosis&&<> · 资源池 {diagnosis.pool_name} · 可用候选 {ready}/{diagnosis.nodes.length}</>}</p>
   {diagnosis&&<><p>{diagnosis.reasons.map(reason=>reason.message).join('；')}</p><details><summary>查看服务端候选设备与阻塞原因</summary>{diagnosis.nodes.length?diagnosis.nodes.map(node=><p key={node.node_name+':'+node.gpu_index}>{node.node_name} / 设备 {node.gpu_index}：{node.reasons.map(reason=>reason.message).join('；')||'符合服务端要求'}</p>):<p>资源池中没有可用的服务端设备。</p>}</details></>}
 </div>;
}

export function ServiceStartupSummary({id,onDetails}:{id:string;onDetails:()=>void}){
 const {data,error}=usePoll<Detail>('orchestrations/'+id);
 if(!data&&!error)return <section className="service-startup-summary" aria-label="服务端启动状态"><h3>服务端启动状态</h3><p>正在查询服务端状态…</p></section>;
 if(data?.orchestration.service_mode!=='managed'&&!error)return null;
 const services=data?.orchestration.services||[];
 return <section className="service-startup-summary" aria-label="服务端启动状态"><h3>服务端启动状态</h3>
   {error&&<p role="alert">服务端状态更新失败：{error}。将自动重试。</p>}
   {data&&<><p className="hint">服务进程启动后仍须通过健康检查，客户端才会开始执行。</p>
     {services.map(service=><p key={service.id}><strong>{service.name}</strong>：{serviceState(service.state)} · 服务端分配位置：<ExecutionPlacement value={service.placement}/>{service.endpoint&&<> · {service.endpoint}</>}{service.error&&<> · {service.error}</>}</p>)}
     {data.orchestration.definitions.filter(definition=>!services.some(service=>service.service_key===definition.key)).map(definition=><SchedulingStatus key={definition.key} definition={definition} diagnosis={data.orchestration.scheduling?.find(item=>item.key===definition.key)}/>)}
     <button type="button" onClick={onDetails}>查看服务端与关联详情</button></>}
 </section>;
}
export function OrchestrationStatus({id}:{id:string}){
 const {data,error}=usePoll<Detail>('orchestrations/'+id),[message,setMessage]=useState(''),[logs,setLogs]=useState(''),[busy,setBusy]=useState(false);
 async function action(service:Service,action:'start'|'stop'){setBusy(true);setMessage('');try{await api(`orchestrations/${id}/services/${service.id}/control`,'POST',{action,expected_revision:service.revision});setMessage(action==='stop'?'已请求停止，等待服务清理。':'已请求恢复，等待调度。')}catch(e){setMessage(String(e))}finally{setBusy(false)}}
 async function read(service:Service){try{const r=await api<{content:string}>(`orchestrations/${id}/services/${service.id}/logs`);setLogs(service.name+'\n'+r.content);setMessage('')}catch(e){setMessage(String(e))}}
 return <section aria-label="客户端与服务关系"><h3>客户端与服务关系</h3>{error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
 {data&&<><p>{data.orchestration.service_mode==='managed'?(data.orchestration.stop_after_batch?'客户端全部结束并清理后停止服务':'批次结束保留服务，请按需手动停止'):data.orchestration.service_mode==='external'?'外部服务由用户维护':'普通批处理无需服务'}</p>
 {data.orchestration.definitions.map(d=><details key={d.key} className="batch-parameters"><summary>{d.key} · {d.mode==='exclusive'?'独占访问':'共享访问'} · 服务端运行参数</summary><p>{d.template?.name} / {d.template?.version}</p><h4>业务参数</h4><pre>{JSON.stringify(d.parameters||{},null,2)}</pre><h4>资源申请</h4><pre>{JSON.stringify(d.resources||{},null,2)}</pre></details>)}
 {data.orchestration.service_mode==='managed'&&data.orchestration.definitions.filter(d=>!data.orchestration.services.some(s=>s.service_key===d.key)).map(d=><SchedulingStatus key={d.key} definition={d} diagnosis={data.orchestration.scheduling?.find(item=>item.key===d.key)}/>)}
 <div className="table-scroll"><table><thead><tr><th>任务</th><th>客户端分配位置</th><th>分配的服务</th><th>服务执行</th></tr></thead><tbody>{data.cases.map(c=><tr key={c.case_id}><td>{c.case_id}</td><td><ExecutionPlacement value={c.placement}/></td><td>{c.endpoint||'等待分配'}{c.waiting_service&&<small>等待服务健康</small>}</td><td>{c.service_key||'—'}<small>{c.service_execution_id||'—'}</small></td></tr>)}</tbody></table></div>
 {data.orchestration.services.map(s=><div key={s.id}><h4>{s.name} · {serviceState(s.state)}</h4><p>服务端分配位置：<ExecutionPlacement value={s.placement}/></p><p>{s.endpoint}{s.cleanup_pending?' · 执行资源尚未释放':''}</p>{s.error&&<p>{s.error}</p>}<ServiceProbeStatus service={s}/><button onClick={()=>void read(s)}>查看 {s.name} 日志</button><button disabled={busy} onClick={()=>void action(s,'stop')}>停止 {s.name}</button><button disabled={busy||s.cleanup_pending} onClick={()=>void action(s,'start')}>恢复 {s.name}</button></div>)}</>}{logs&&<><button onClick={()=>setLogs('')}>关闭服务日志</button><pre className="logs">{logs}</pre></>}
 </section>;
}
