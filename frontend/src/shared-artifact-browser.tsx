import React,{useEffect,useState} from 'react';
import {expireSession} from './login-session';
import {ArtifactPreview,type Artifact} from './artifact-browser';

type Entry={name:string;path:string;kind:string;size_bytes:number;version:string;episode_ids:number[]};
type Page={enabled:boolean;path:string;storage_id:string;output_root:string;node_name:string;items:Entry[];version:string;next:string|null};
export function SharedArtifactBrowser({jobId,runId,episode,legacy,custom=false}:{jobId:string;runId:string;episode?:number;legacy:()=>void;custom?:boolean}){
 const [path,setPath]=useState(''),[cursor,setCursor]=useState<{after:string;version:string}|null>(null);
 const [data,setData]=useState<Page|null>(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0);
 const [unavailable,setUnavailable]=useState(false),[retrying,setRetrying]=useState('');
 const [selected,setSelected]=useState<Artifact|null>(null),[filter,setFilter]=useState(episode==null?'all':String(episode));
 useEffect(()=>{
  const controller=new AbortController();setData(null);setError('');setUnavailable(false);setRetrying('');
  const token=sessionStorage.getItem('eval-token')||'';
  const params=new URLSearchParams({path,...(custom?{directory:'custom'}:{}),...(cursor||{})});
  const load=async()=>{
   for(let attempt=0;;attempt++){
    const response=await fetch(`/api/v1/jobs/${jobId}/attempts/${runId}/shared-artifacts?${params}`,{signal:controller.signal,headers:{Authorization:`Bearer ${token}`}});
    if(response.status===401)expireSession(token);
    const value=await response.json();
    if(response.status===429&&attempt<2){
     setRetrying(`读取繁忙，正在重试（${attempt+1}/2）…`);
     const delay=Math.min(5,Math.max(1,Number(response.headers.get('Retry-After'))||2))*1000;
     await new Promise<void>(resolve=>{
      const done=()=>{clearTimeout(timer);controller.signal.removeEventListener('abort',done);resolve();};
      const timer=setTimeout(done,delay);controller.signal.addEventListener('abort',done,{once:true});
      if(controller.signal.aborted)done();
     });
     if(controller.signal.aborted)return;
     continue;
    }
    if(!response.ok)throw new Error(value.detail||`读取失败（${response.status}）`);
    return value;
   }
  };
  load()
   .then(value=>{if(!controller.signal.aborted){setRetrying('');if(value.enabled===false)setUnavailable(true);else if(value.enabled===true&&Array.isArray(value.items))setData(value);else throw new Error('共享目录响应格式错误');}})
   .catch(e=>{if(!controller.signal.aborted){setRetrying('');setError(String(e));}});
  return()=>controller.abort();
 },[jobId,runId,path,cursor,refresh,custom]);
 const navigate=(next:string)=>{setSelected(null);setCursor(null);setPath(next);};
 const rounds=[...new Set([...(episode==null?[]:[episode]),...(data?.items||[]).flatMap(f=>f.episode_ids)])].sort((a,b)=>a-b);
 const items=data?.items.filter(f=>custom||f.kind==='directory'||filter==='all'||filter==='task'&&!f.episode_ids.length||f.episode_ids.includes(Number(filter)))||[];
 return <section aria-label="共享存储产物">
  {custom?<p>自定义共享实时目录，可能包含其他运行文件；不视为本次任务产物、不推断回合归属，也不自动上传或清理。此处只读访问。</p>:<p>API 直接读取共享输出目录，文件不经过 Worker 转发。目录可能包含正在写入的文件；文件被清理后将不可访问。</p>}
  {data&&<p>存储：{data.storage_id} · 执行节点：{data.node_name}<br/>{custom?'节点自定义目录':'节点输出目录'}：{data.output_root}</p>}
  {unavailable&&<div role="status"><p>API 尚未启用共享产物读取。请在部署环境设置 SHARED_ARTIFACT_HOST_ROOT，并更新、重建 API 容器；旧版 API 可能不支持该变量。API 主机必须能够访问节点的同一份产物文件。</p><button onClick={legacy}>查看历史上传产物</button></div>}
  {!unavailable&&<><nav aria-label="产物目录"><button disabled={!path} onClick={()=>navigate(path.split('/').slice(0,-1).join('/'))}>返回上一层</button><button onClick={()=>navigate('')}>{custom?'自定义目录根':'输出根目录'}</button>{path.split('/').filter(Boolean).map((name,i)=><button key={i} onClick={()=>navigate(path.split('/').slice(0,i+1).join('/'))}>{name}</button>)}</nav>
  {!custom&&<><label>共享产物归属<select value={filter} onChange={e=>{setSelected(null);setFilter(e.target.value);}}><option value="all">全部文件</option><option value="task">未声明回合的文件</option>{rounds.map(n=><option key={n} value={n}>回合 {n}</option>)}</select></label>
  <p className="hint">归属筛选作用于当前页文件；目录始终显示，便于进入。回合归属以协议明确引用为准，不根据目录名猜测。</p></>}</>}
  <button onClick={()=>{setSelected(null);setCursor(null);setRefresh(n=>n+1);}}>刷新共享目录</button>
  {error&&<p role="alert">共享产物读取失败：{error}</p>}{!data&&!error&&!unavailable&&<p role="status">{retrying||'正在读取共享目录…'}</p>}
  {data&&!items.length&&<p>当前目录页及筛选下没有文件。</p>}
  {data&&<div className="table-scroll"><table aria-label="共享产物文件"><thead><tr><th>名称</th><th>大小</th><th>归属</th><th>操作</th></tr></thead><tbody>{items.map(f=><tr key={f.path}><td>{f.name}</td><td>{f.kind==='directory'?'目录':`${(f.size_bytes/1024).toFixed(1)} KiB`}</td><td>{custom?'共享实时文件':f.episode_ids.map(n=>'回合 '+n).join('、')||'未声明'}</td><td>{f.kind==='directory'?<button onClick={()=>navigate(f.path)}>打开目录</button>:f.kind==='file'?<button onClick={()=>setSelected({id:f.path,name:f.path,size_bytes:f.size_bytes,shared_path:f.path,version:f.version,...(custom?{shared_directory:'custom' as const}:{})})}>查看 / 下载</button>:<span>不支持的文件类型</span>}</td></tr>)}</tbody></table></div>}
  {data?.next&&<button onClick={()=>{setSelected(null);setCursor({after:data.next!,version:data.version});}}>下一页文件</button>}
  {cursor&&<button onClick={()=>{setSelected(null);setCursor(null);}}>返回第一页</button>}
  {selected&&<ArtifactPreview key={runId+'/'+selected.id+'/'+selected.version} jobId={jobId} runId={runId} file={selected}/>}
 </section>;
}
