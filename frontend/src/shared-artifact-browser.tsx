import React,{useEffect,useState} from 'react';
import {expireSession} from './login-session';
import {ArtifactPreview,type Artifact} from './artifact-browser';

type Entry={name:string;path:string;kind:string;size_bytes:number;version:string;episode_ids:number[]};
type Page={enabled:boolean;path:string;storage_id:string;output_root:string;node_name:string;items:Entry[];version:string;next:string|null};
export function SharedArtifactBrowser({jobId,runId,episode,legacy}:{jobId:string;runId:string;episode?:number;legacy:()=>void}){
 const [path,setPath]=useState(''),[cursor,setCursor]=useState<{after:string;version:string}|null>(null);
 const [data,setData]=useState<Page|null>(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0);
 const [unavailable,setUnavailable]=useState(false);
 const [selected,setSelected]=useState<Artifact|null>(null),[filter,setFilter]=useState(episode==null?'all':String(episode));
 useEffect(()=>{
  const controller=new AbortController();setData(null);setError('');setUnavailable(false);
  const token=sessionStorage.getItem('eval-token')||'';
  const params=new URLSearchParams({path,...(cursor||{})});
  fetch(`/api/v1/jobs/${jobId}/attempts/${runId}/shared-artifacts?${params}`,{signal:controller.signal,headers:{Authorization:`Bearer ${token}`}})
   .then(async response=>{if(response.status===401)expireSession(token);const value=await response.json();if(!response.ok)throw new Error(value.detail||`读取失败（${response.status}）`);return value;})
   .then(value=>{if(!controller.signal.aborted){if(value.enabled===false)setUnavailable(true);else if(value.enabled===true&&Array.isArray(value.items))setData(value);else throw new Error('共享目录响应格式错误');}})
   .catch(e=>{if(!controller.signal.aborted)setError(String(e));});
  return()=>controller.abort();
 },[jobId,runId,path,cursor,refresh]);
 const navigate=(next:string)=>{setSelected(null);setCursor(null);setPath(next);};
 const rounds=[...new Set([...(episode==null?[]:[episode]),...(data?.items||[]).flatMap(f=>f.episode_ids)])].sort((a,b)=>a-b);
 const items=data?.items.filter(f=>f.kind==='directory'||filter==='all'||filter==='task'&&!f.episode_ids.length||f.episode_ids.includes(Number(filter)))||[];
 return <section aria-label="共享存储产物">
  <p>API 直接读取共享输出目录，文件不经过 Worker 转发。目录可能包含正在写入的文件；文件被清理后将不可访问。</p>
  {data&&<p>存储：{data.storage_id} · 执行节点：{data.node_name}<br/>节点输出目录：{data.output_root}</p>}
  {unavailable&&<div role="status"><p>API 尚未启用共享产物读取。请在部署环境设置 SHARED_ARTIFACT_HOST_ROOT，并更新、重建 API 容器；旧版 API 可能不支持该变量。API 主机必须能够访问节点的同一份产物文件。</p><button onClick={legacy}>查看历史上传产物</button></div>}
  {!unavailable&&<><nav aria-label="产物目录"><button onClick={()=>navigate('')}>输出根目录</button>{path.split('/').filter(Boolean).map((name,i)=><button key={i} onClick={()=>navigate(path.split('/').slice(0,i+1).join('/'))}>{name}</button>)}</nav>
  <label>共享产物归属<select value={filter} onChange={e=>{setSelected(null);setFilter(e.target.value);}}><option value="all">全部文件</option><option value="task">未声明回合的文件</option>{rounds.map(n=><option key={n} value={n}>回合 {n}</option>)}</select></label>
  <p className="hint">归属筛选作用于当前页文件；目录始终显示，便于进入。回合归属以协议明确引用为准，不根据目录名猜测。</p></>}
  <button onClick={()=>{setSelected(null);setCursor(null);setRefresh(n=>n+1);}}>刷新共享目录</button>
  {error&&<p role="alert">共享产物读取失败：{error}</p>}{!data&&!error&&!unavailable&&<p role="status">正在读取共享目录…</p>}
  {data&&!items.length&&<p>当前目录页及筛选下没有文件。</p>}
  {data&&<div className="table-scroll"><table aria-label="共享产物文件"><thead><tr><th>名称</th><th>大小</th><th>归属</th><th>操作</th></tr></thead><tbody>{items.map(f=><tr key={f.path}><td>{f.name}</td><td>{f.kind==='directory'?'目录':`${(f.size_bytes/1024).toFixed(1)} KiB`}</td><td>{f.episode_ids.map(n=>'回合 '+n).join('、')||'未声明'}</td><td>{f.kind==='directory'?<button onClick={()=>navigate(f.path)}>打开目录</button>:f.kind==='file'?<button onClick={()=>setSelected({id:f.path,name:f.path,size_bytes:f.size_bytes,shared_path:f.path,version:f.version})}>查看 / 下载</button>:<span>不支持的文件类型</span>}</td></tr>)}</tbody></table></div>}
  {data?.next&&<button onClick={()=>{setSelected(null);setCursor({after:data.next!,version:data.version});}}>下一页文件</button>}
  {cursor&&<button onClick={()=>{setSelected(null);setCursor(null);}}>返回第一页</button>}
  {selected&&<ArtifactPreview key={runId+'/'+selected.id+'/'+selected.version} jobId={jobId} runId={runId} file={selected}/>}
 </section>;
}
