import React,{useEffect,useState} from 'react';
import {api} from './api';
import {Field} from './form-fields';
type Release={id:string;version:string;protocol_version:number;capabilities:string[]};
type Operation={id:string;release_id:string;status:string;error:string|null;created_at:string};
type Info={revision:number;state:string|null;updater_online:boolean;slots:{id:string;gpu_index:number;online:boolean;protocol_version:number;software:{version:string;build_digest:string|null;capabilities:string[]}|null}[];history:Operation[]};
const states:Record<string,string>={DRAINING:'等待全机任务排空',APPLYING:'安装中',VERIFYING:'等待全部 GPU 回连',ROLLING_BACK:'恢复旧版本中',SUCCEEDED:'升级成功',FAILED:'升级失败（暂停接单）',CANCELLED:'已取消'};
const capabilities:Record<string,string>={docker:'Docker 执行',host:'宿主机执行',episode_reporting:'逐回合上报',typed_parameters:'类型化参数',directory_mounts:'通用目录挂载',inference_services:'推理服务',host_login_environment:'宿主机登录环境',host_episode_launch:'宿主机逐回合启动'};
export function WorkerUpgrades({id}:{id:string}){
 const [info,setInfo]=useState<Info|null>(null),[releases,setReleases]=useState<Release[]>([]),[selected,setSelected]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const refresh=async()=>{const [i,r]=await Promise.all([api<Info>('admin/nodes/'+id+'/upgrades'),api<Release[]>('admin/worker-releases')]);if(Array.isArray(i.slots))setInfo(i);setReleases(r)};
 useEffect(()=>{void refresh().catch(e=>setError(String(e)));const t=setInterval(()=>void refresh().catch(e=>setError(String(e))),5000);return()=>clearInterval(t)},[id]);
 async function act(path:string,body?:unknown){setBusy(true);setError('');try{await api(path,'POST',body);await refresh()}catch(e){setError(String(e))}finally{setBusy(false)}}
 const current=info?.history[0],blocked=!!info?.state&&['DRAINING','APPLYING','VERIFYING','ROLLING_BACK','FAILED'].includes(info.state);
 return <details className="panel"><summary>Worker 版本与升级</summary><p>软件版本表示安装包，协议版本表示平台通信能力。升级会暂停全机新任务，已有任务继续执行；长期服务需先停止并完成清理。</p>
 {error&&<p role="alert">{error}</p>}{!info?<p>正在读取版本信息…</p>:<>
 <p>升级执行器：{info.updater_online?'在线':'未连接，请先安装或检查执行器'}</p>
 <table><thead><tr><th>GPU</th><th>软件版本 / 发布摘要</th><th>协议</th><th>支持能力</th></tr></thead><tbody>{info.slots.map(s=><tr key={s.id}><td>{s.gpu_index} · {s.online?'在线':'离线'}</td><td>{s.software?.version||'未知（旧 Worker）'}<br/><code>{s.software?.build_digest?.slice(0,12)||'未使用托管发布包'}</code></td><td>v{s.protocol_version}</td><td>{s.software?.capabilities.map(c=>capabilities[c]||c).join('、')||'未上报'}</td></tr>)}</tbody></table>
 <p>当前状态：{info.state?states[info.state]||info.state:'未发起升级'}</p>
 <button disabled={busy} onClick={()=>void act('admin/worker-releases')}>发布控制端随附 Worker 包</button>
 <Field label="目标 Worker 发布版本"><select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">选择发布版本</option>{releases.map(r=><option key={r.id} value={r.id}>{r.version} · 协议 v{r.protocol_version} · {r.id.slice(0,12)}</option>)}</select></Field>
 <button disabled={busy||blocked||!info.updater_online||!selected||!info.slots.length||info.slots.some(s=>!s.online)} onClick={()=>void act('admin/nodes/'+id+'/upgrades',{release_id:selected,expected_revision:info.revision})}>排空并升级此机器</button>
 {current&&['DRAINING','FAILED'].includes(current.status)&&current.error!=='rollback_failed'&&<button disabled={busy} onClick={()=>void act('admin/nodes/'+id+'/upgrades/'+current.id+'/cancel',{expected_revision:info.revision})}>{current.status==='FAILED'?'确认失败已处理，解除升级暂停':'取消升级，恢复接单'}</button>}
 <p className="muted">升级执行器独立安装，使用现有服务单元，不改写 NFS、凭据和工作目录。未连接的节点不能发起升级；安装期间不能取消。恢复旧版本失败时需先修复节点，执行器会重试恢复。</p>
 <h4>升级历史</h4>{!info.history.length?<p>暂无升级记录</p>:<ul>{info.history.map(h=><li key={h.id}>{h.created_at} · {states[h.status]||h.status} · {h.release_id.slice(0,12)}{h.error&&' · '+(h.error==='rollback_failed'?'恢复旧版本失败，请检查节点执行器':'安装或新版本回连失败，已恢复旧代码')}</li>)}</ul>}
 </>}
 </details>;
}
