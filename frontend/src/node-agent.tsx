import React,{useEffect,useState} from 'react';
import {api} from './api';
import {Field} from './form-fields';
type Agent={status:string;version?:string;seen_at?:string;revision:number;applied_revision?:number;config_status?:string;desired:{heartbeat_seconds:number;sample_seconds:number;programs:string};programs?:{name:string;state:string}[];samples?:Record<string,{status:string;sampled_at:number;values:Record<string,string|number>}>;events?:{sequence:number;code:string}[]};
export function NodeAgent({id}:{id:string}){
 const [state,setState]=useState<Agent|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 async function load(){const s=await api<Agent>('admin/nodes/'+id+'/agent');if(s?.status)setState(s)}
 useEffect(()=>{void load().catch(e=>setError(String(e)));const t=setInterval(()=>void load().catch(e=>setError(String(e))),5000);return()=>clearInterval(t)},[id]);
 async function configure(values:Record<string,unknown>){if(!state)return;setBusy(true);setError('');try{setState(await api<Agent>('admin/nodes/'+id+'/agent','PUT',{...state.desired,...values,expected_revision:state.revision}));setMessage('配置已提交，等待节点回执')}catch(e){setError(String(e))}finally{setBusy(false)}}
 const labels:Record<string,string>={online:'在线',offline:'离线',not_installed:'未安装',applied:'已生效',waiting:'等待处理',failed:'失败'};
 const codes:Record<string,string>={started:'Agent 启动',config_applied:'配置生效',config_failed:'配置失败',program_started:'Worker 启动',program_stopped:'Worker 停止',program_failed:'Worker 操作失败',connection_restored:'连接恢复'};
 return <section aria-label="节点 Agent"><h3>节点 Agent</h3>{error&&<p role="alert">{error}<button onClick={()=>void load().then(()=>setError('')).catch(e=>setError(String(e)))}>重试</button></p>}{message&&<p role="status">{message}</p>}
 {!state?<p>正在读取 Agent 状态…</p>:state.status==='not_installed'?<p>此节点尚未安装 Agent，现有 Worker 仍可运行。安装完整节点程序后可查看主机监测和管理 Worker。</p>:<>
 <p>Agent：{labels[state.status]||state.status} · 版本 {state.version||'未知'} · 最近心跳 {state.seen_at?new Date(state.seen_at).toLocaleString():'等待回连'}</p>
 <p>配置：期望 {state.revision} / 已应用 {state.applied_revision??'未知'} · {labels[state.config_status||'waiting']}</p>
 <p>Agent 在线不代表 Worker 已就绪。工作目录、资产映射与运行环境请在下方对应配置区管理。</p>
 <form key={state.revision} onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);void configure({heartbeat_seconds:Number(f.get('heartbeat')),sample_seconds:Number(f.get('sample'))})}}><div className="row"><Field label="Agent 心跳周期（秒）"><input name="heartbeat" type="number" min={2} max={15} defaultValue={state.desired.heartbeat_seconds} required/></Field><Field label="主机采样周期（秒）"><input name="sample" type="number" min={5} max={120} defaultValue={state.desired.sample_seconds} required/></Field></div><button disabled={busy}>保存 Agent 配置</button></form>
 <p>Worker 期望状态：{state.desired.programs==='running'?'运行':'排空后停止'}</p><button disabled={busy||state.status!=='online'||(state.desired.programs==='stopped'&&(state.applied_revision!==state.revision||state.config_status!=='applied'))} onClick={()=>void configure({programs:state.desired.programs==='running'?'stopped':'running'})}>{state.desired.programs==='running'?'排空并停止 Worker':'恢复 Worker 运行'}</button><p className="muted">排空会停止接收新任务，等待已有任务结束和资源清理，不会强行终止评测。恢复 Worker 不会自动启用已停用机器或加入资源池。</p>
 <table><thead><tr><th>受管程序</th><th>实际状态</th></tr></thead><tbody>{state.programs?.map(p=><tr key={p.name}><td>{p.name}</td><td>{p.state}</td></tr>)}</tbody></table>
 <details><summary>主机监测</summary>{Object.entries(state.samples||{}).map(([name,s])=><div key={name}><strong>{name}：{s.status==='ok'?'采样正常':s.status==='failed'?'采样失败':'等待采样'}</strong><span> · {new Date(s.sampled_at*1000).toLocaleString()}</span><pre>{JSON.stringify(s.values,null,2)}</pre></div>)}</details>
 <details><summary>近期 Agent 事件</summary><ul>{state.events?.map(e=><li key={e.sequence}>#{e.sequence} {codes[e.code]||e.code}</li>)}</ul><p>评测日志和推理服务日志在对应任务、服务实例中查看。</p></details>
 </> }</section>;
}
