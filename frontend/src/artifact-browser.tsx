import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';
import {usePoll} from './poll';
import './artifact-browser.css';
import {SharedArtifactBrowser} from './shared-artifact-browser';
import {ArtifactTextPreview,textPreviewKind} from './artifact-text-preview';

export type Artifact={shared_directory?:'custom'|'platform';id:string;name:string;size_bytes:number;sha256?:string;shared_path?:string;version?:string;episode_ids?:number[];media_type?:string};
export function ArtifactBrowser({jobId,runId,episode}:{jobId:string;runId:string;episode?:number}){
 const [open,setOpen]=useState(false);
 return <><button onClick={()=>setOpen(true)}>查看产物</button>{open&&<ArtifactDrawer key={jobId+'/'+runId} jobId={jobId} initialRun={runId} episode={episode} close={()=>setOpen(false)}/>}</>;
}
function ArtifactDrawer({jobId,initialRun,episode,close}:{jobId:string;initialRun:string;episode?:number;close:()=>void}){
 const dialog=useRef<HTMLDialogElement>(null);
 const [run,setRun]=useState(initialRun);
 const {data:job}=usePoll<{attempts:{id:string;attempt:number;status:string}[]}>('jobs/'+jobId);
 useEffect(()=>{const previous=document.activeElement as HTMLElement;dialog.current?.showModal();return()=>previous?.focus();},[]);
 return <dialog ref={dialog} className="artifact-dialog" aria-label="运行产物" onCancel={close}>
  <header><h2>运行产物</h2><button onClick={close}>关闭产物</button></header>
  <p>任务 {jobId}</p>
  <label>产物执行尝试<select value={run} onChange={e=>setRun(e.target.value)}>
   {!job?.attempts?.some(a=>a.id===run)&&<option value={run}>当前执行 {run}</option>}
   {job?.attempts?.map(a=><option key={a.id} value={a.id}>尝试 {a.attempt} · {a.id}</option>)}
  </select></label>
  <ArtifactOutputLocation key={"output/"+run} jobId={jobId} run={run}/>
  <ArtifactRun key={run} jobId={jobId} run={run} episode={episode}/>
 </dialog>;
}
function ArtifactOutputLocation({jobId,run}:{jobId:string;run:string}){
 const {data,error}=usePoll<{result_directory?:{path?:string;node_name?:string;error?:string}|null;output_directory?:{path:string;execution_root:string|null;node_name:string|null;state:string}|null}>(`jobs/${jobId}/attempts/${run}/execution-location`);
 const output=data?.output_directory;
 return <section aria-label="产物输出目录"><h4>产物输出目录（节点路径）</h4>
 {error?<p>输出目录记录读取失败，请刷新或确认 API 版本。</p>:!data?<p>正在读取输出目录记录…</p>:output?<>
 <p>节点：{output.node_name||'未记录'}</p><p style={{overflowWrap:'anywhere'}}>输出目录：<code>{output.path}</code></p>
 {output.execution_root&&output.execution_root!==output.path&&<p style={{overflowWrap:'anywhere'}}>执行产物根：<code>{output.execution_root}</code>（包含公共输出及各回合子目录）</p>}
 <p>{output.state==='planned'?'计划目录，尚未收到节点登记确认。':'本次执行记录的目录。'}路径记录不保证文件仍存在或 API 有读取权限。</p>
 </>:<p>本次执行没有唯一可用的输出目录记录；不根据节点当前配置推测历史路径。</p>}
 {data?.result_directory&&<div><h4>自定义结果浏览目录（历史配置）</h4><p style={{overflowWrap:'anywhere'}}>节点：{data.result_directory.node_name||'未记录'} · <code>{data.result_directory.path||'路径未能解析'}</code></p>{data.result_directory.error&&<p>{data.result_directory.error}</p>}<p>共享实时内容，不作为本次任务产物；是否可读以实际共享授权和文件状态为准。</p></div>}
 </section>;
}

function ArtifactRun({jobId,run,episode}:{jobId:string;run:string;episode?:number}){
 const [source,setSource]=useState('shared');
 return <><label>产物来源<select value={source} onChange={e=>setSource(e.target.value)}><option value="shared">共享存储（API 直读）</option><option value="custom">自定义共享目录（实时浏览）</option><option value="legacy">历史上传产物</option></select></label>
 {source!=='legacy'?<SharedArtifactBrowser key={source} custom={source==='custom'} jobId={jobId} runId={run} episode={episode} legacy={()=>setSource('legacy')}/>:<LegacyArtifacts jobId={jobId} run={run} episode={episode}/>}</>;
}
function LegacyArtifacts({jobId,run,episode}:{jobId:string;run:string;episode?:number}){
 const [filter,setFilter]=useState(episode==null?'all':String(episode));
 const [selected,setSelected]=useState<Artifact|null>(null);
 const {data:files,error}=usePoll<Artifact[]>(`jobs/${jobId}/attempts/${run}/artifacts`);
 const rounds=[...new Set([...(episode==null?[]:[episode]),...(files||[]).flatMap(f=>f.episode_ids||[])])].sort((a,b)=>a-b);
 const visible=(files||[]).filter(f=>filter==='all'||filter==='task'&&!f.episode_ids?.length||f.episode_ids?.includes(Number(filter)));
 return <>
  <label>产物归属<select value={filter} onChange={e=>{setSelected(null);setFilter(e.target.value);}}><option value="all">全部产物</option><option value="task">任务级产物</option>{rounds.map(n=><option key={n} value={n}>回合 {n}</option>)}</select></label>
  <p className="hint">这里只显示已上传文件。业务需写入 MINI_EVAL_OUTPUT_DIR（SDK 使用 context.output_dir）；旧上传模式在客户端程序结束时收集；共享直读模式请切换产物来源。未声明回合归属的文件列入任务级。</p>
  {error&&<p role="alert">产物读取失败：{error}</p>}{!files&&!error&&<p role="status">正在读取产物…</p>}
  {files&&!error&&!visible.length&&<p>当前筛选下暂无已上传产物。请确认业务输出目录及 Worker 上传日志；未上传不代表业务未生成文件。</p>}
  <div className="table-scroll"><table aria-label="运行产物文件"><thead><tr><th>文件</th><th>归属</th><th>大小</th><th>操作</th></tr></thead><tbody>{visible.map(f=><tr key={f.id}><td>{f.name}</td><td>{f.episode_ids?.length?f.episode_ids.map(n=>'回合 '+n).join('、'):'任务级'}</td><td>{(f.size_bytes/1024).toFixed(1)} KiB</td><td><button onClick={()=>setSelected(f)}>查看 / 下载</button></td></tr>)}</tbody></table></div>
  {selected&&<ArtifactPreview key={run+'/'+selected.id} jobId={jobId} runId={run} file={selected}/>}
 </>;
}
export function ArtifactPreview({jobId,runId,file}:{jobId:string;runId:string;file:Artifact}){
 const [access,setAccess]=useState<{url:string;media_type:string}|null>(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0);
 useEffect(()=>{let alive=true;setAccess(null);setError('');api<{url:string;media_type:string}>(file.shared_path!=null?`jobs/${jobId}/attempts/${runId}/shared-artifacts/media-access`:`jobs/${jobId}/attempts/${runId}/artifacts/${file.id}/media-access`,'POST',file.shared_path!=null?{path:file.shared_path,version:file.version,...(file.shared_directory?{directory:file.shared_directory}:{})}:{}).then(a=>{if(alive)setAccess(a);}).catch(e=>{if(alive)setError(String(e));});return()=>{alive=false;};},[jobId,runId,file.id,refresh]);
 return <section aria-label="产物预览"><h3>{file.name}</h3>{error&&<p role="alert">{error}</p>}
  {access?.media_type.startsWith('video/')&&<video key={access.url+refresh} src={access.url} controls preload="metadata" onError={()=>setError('视频暂时无法播放：授权可能已过期、文件读取失败或浏览器不支持此编码。可重新加载或下载。')}/>}
  {access?.media_type.startsWith('image/')&&<img src={access.url} alt={file.name} onError={()=>setError('图片读取失败，请重新加载或下载。')}/>}
  {access&&textPreviewKind(file.name)&&<ArtifactTextPreview key={refresh} url={access.url} kind={textPreviewKind(file.name)!}/>}
  {access?.media_type==='application/octet-stream'&&!textPreviewKind(file.name)&&<p>此格式暂不提供在线预览，请下载查看。</p>}
  {access&&<a href={access.url} download={file.name.split('/').at(-1)}>下载文件</a>}
  {(error||access)&&<button onClick={()=>setRefresh(n=>n+1)}>重新加载预览</button>}
 </section>;
}
