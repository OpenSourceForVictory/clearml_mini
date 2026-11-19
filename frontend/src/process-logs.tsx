import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';
import {usePoll} from './poll';
import {CopyValue} from './execution-location';
import './process-logs.css';

type Placement={node_name:string;accelerator_type:string;gpu_index:number};
export type Catalog={placement?:Placement|null;id:string;execution_id:string;scope:string;episode_id:number;generation:string;status:string;path:string;next_sequence:number;truncated:boolean;dropped_bytes:number;updated_at:string};
type Row={sequence:number;time:string;stream:string;text:string;gap:boolean};
type Page={records:Row[];previous_cursor:string|null;next_cursor:string|null;has_more:boolean;gap:boolean;closed?:boolean;truncated?:boolean;source:string};
const title=(e:Catalog)=>e.scope==='episode'?'回合 '+e.episode_id+' 日志':e.scope==='service'?'服务实例日志':e.scope==='business'?'任务公共日志':'执行诊断';
const states:Record<string,string>={expired:'节点读取超时，请重试',busy:'日志正在轮转或清理，请重试',cleaned:'节点日志已清理',missing:'日志尚未生成或已不存在',failed:'节点日志读取失败',superseded:'日志代次已变化，请重新打开'};
export function ProcessLogs({jobId,runId,episode}:{jobId:string;runId:string;episode?:number}){
 const {data}=usePoll<{items:Catalog[];placement?:Placement|null}>(`jobs/${jobId}/attempts/${runId}/log-catalog`);
 const [selected,setSelected]=useState<string|null>(null);
 const entries=(data?.items||[]).map(e=>({...e,placement:data?.placement})).filter(e=>episode==null||e.scope==='episode'&&e.episode_id===episode||e.scope==='business');
 const current=entries.find(e=>e.id===selected);
 return <span className="process-log-launchers">{entries.map(e=><button key={e.id} onClick={()=>setSelected(e.id)}>{title(e)}{e.status==='cleaned'?'（已清理）':''}</button>)}{current&&<LogDrawer key={jobId+'/'+runId+'/'+current.id+'/'+current.generation} jobId={jobId} runId={runId} entry={current} close={()=>setSelected(null)}/>}</span>;
}
export function ServiceProcessLogs({jobId}:{jobId:string}){
 const {data}=usePoll<{attempts:{id:string;attempt:number}[]}>(`jobs/${jobId}`);
 const attempt=data?.attempts?.reduce<{id:string;attempt:number}|undefined>((a,b)=>!a||b.attempt>a.attempt?b:a,undefined);
 return attempt?<ProcessLogs jobId={jobId} runId={attempt.id}/>:null;
}
function LogDrawer({jobId,runId,entry,close}:{jobId:string;runId:string;entry:Catalog;close:()=>void}){
 const dialog=useRef<HTMLDialogElement>(null),alive=useRef(true),serial=useRef(0),busy=useRef(false);
 const [page,setPage]=useState<Page|null>(null),[error,setError]=useState(''),[stream,setStream]=useState('all'),[follow,setFollow]=useState(true),[loading,setLoading]=useState(false),[notice,setNotice]=useState('');
 const [downloading,setDownloading]=useState(false);const cancelDownload=useRef(false);
 const [continuation,setContinuation]=useState<{cursor:string;until:number;part:number;gap:boolean}|null>(null);
 useEffect(()=>{alive.current=true;const previous=document.activeElement as HTMLElement;dialog.current?.showModal();return()=>{alive.current=false;serial.current++;cancelDownload.current=true;previous?.focus();};},[]);
 async function read(direction:string,cursor?:string|null,until=entry.next_sequence){
  const ticket=await api<{read_id:string}>(`jobs/${jobId}/attempts/${runId}/log-reads`,'POST',{log_id:entry.id,direction,cursor:cursor||null,stream,limit_bytes:65536,until});
  const deadline=Date.now()+31000;
  while(alive.current&&Date.now()<deadline){
   const response=await api<{status:string;page:Page;generation:string;source?:string}>(`log-reads/${ticket.read_id}`);
   if(response.status==='ready'){if(response.generation!==entry.generation)throw Error('日志代次已变化');return {...response.page,source:response.source||response.page.source};}
   if(!['queued','claimed'].includes(response.status))throw Error(states[response.status]||'日志读取不可用');
   await new Promise(resolve=>setTimeout(resolve,700));
  }
  throw Error('节点读取超时或窗口已关闭');
 }
 async function load(direction='tail',cursor?:string|null){
  if(busy.current)return;busy.current=true;setLoading(true);const request=++serial.current;
  try{const value=await read(direction,cursor);if(alive.current&&request===serial.current){setPage(value);setError('');}}
  catch(e){if(alive.current&&request===serial.current)setError(String(e));}
  finally{busy.current=false;if(alive.current)setLoading(false);}
 }
 useEffect(()=>{serial.current++;setPage(null);setError('');setContinuation(null);void load();},[stream]);
 useEffect(()=>{if(!follow)return;const timer=setInterval(()=>{if(!page?.closed)void load();},3000);return()=>clearInterval(timer);},[follow,page?.closed,stream,entry.next_sequence]);
 async function download(next=false){
  const resume=next?continuation:null,until=resume?.until??entry.next_sequence,part=resume?.part??1;
  setDownloading(true);cancelDownload.current=false;setNotice('');const chunks:string[]=[];let bytes=0,cursor:string|null=resume?.cursor??null,gap=resume?.gap??false;
  function save(more:string|null){
   if(!alive.current||cancelDownload.current)throw Error('下载已取消');
   const suffix=more||part>1?'-part-'+String(part).padStart(3,'0'):'';
   const url=URL.createObjectURL(new Blob(chunks,{type:'application/x-ndjson'}));const link=document.createElement('a');link.href=url;link.download=entry.execution_id+'-'+entry.scope+'-'+entry.episode_id+suffix+'.jsonl';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
   setContinuation(more?{cursor:more,until,part:part+1,gap}:null);
   setNotice((more?'已下载第 '+part+' 个分片，请继续下载下一分片；尚非完整范围':'已下载本次读取范围内保留日志')+(gap?'；日志存在丢失或淘汰范围':''));
  }
  try{
   // Bound both browser memory and requests per part; the next part is explicit.
   for(let count=0;count<96;count++){
    if(cancelDownload.current||!alive.current)throw Error('下载已取消');
    const value=await read('forward',cursor,until);gap=gap||value.gap||!!value.truncated;
    const text=value.records.map(r=>JSON.stringify(r)+'\n').join(''),size=new TextEncoder().encode(text).length;
    if(bytes+size>8*1024*1024){
     if(!cursor||!chunks.length)throw Error('单页日志超出下载上限');
     save(cursor);return;
    }
    bytes+=size;chunks.push(text);
    if(!value.has_more){save(null);return;}
    if(!value.next_cursor||value.next_cursor===cursor)throw Error('日志游标未推进');
    cursor=value.next_cursor;
   }
   save(cursor);
  }catch(e){if(alive.current)setNotice(String(e)+'；本分片未生成，可稍后重试');}
  finally{if(alive.current)setDownloading(false);}
 }
 return <dialog className="process-log-drawer" ref={dialog} aria-label={title(entry)} onCancel={e=>{e.preventDefault();close();}}>
  <header><h3>{title(entry)}</h3><button onClick={close}>关闭独立日志</button></header>
  <p><CopyValue label="执行 ID" value={entry.execution_id}/></p><p><CopyValue label="日志目录" value={entry.path}/></p>
  {entry.placement&&<p>节点：{entry.placement.node_name} · {entry.placement.accelerator_type?.toUpperCase()} {entry.placement.gpu_index}</p>}
  <p>节点保留日志 · {entry.status} · 更新：{entry.updated_at}</p>{entry.scope==='business'&&<p>此业务进程的多个回合共用这份日志。</p>}
  {page&&<p>读取来源：{page.source==='platform_cache'?'平台临时页缓存（节点读取结果）':'节点日志文件'}</p>}
  <label>输出来源<select aria-label="输出来源" value={stream} disabled={loading||downloading} onChange={e=>{setFollow(false);setStream(e.target.value);}}><option value="all">全部输出</option><option>stdout</option><option>stderr</option><option>system</option></select></label>
  <button onClick={()=>setFollow(v=>!v)}>{follow?'暂停跟随':'自动跟随'}</button><button disabled={loading} onClick={()=>void load()}>刷新尾部</button>
  <button disabled={loading||!page?.previous_cursor} onClick={()=>{setFollow(false);void load('backward',page?.previous_cursor);}}>加载更早日志</button>
  <button disabled={downloading} onClick={()=>void download()}>下载保留日志（最多 8 MiB）</button>{continuation&&<button disabled={downloading} onClick={()=>void download(true)}>下载下一分片（第 {continuation.part} 份）</button>}{downloading&&<button onClick={()=>{cancelDownload.current=true;}}>取消下载</button>}
  {error&&<p role="alert">{error}</p>}{loading&&<p role="status">正在读取节点日志…</p>}
  {(page?.gap||page?.truncated||entry.truncated||entry.dropped_bytes>0)&&<p role="status">日志有缺口或已部分淘汰，不能视为完整归档。</p>}
  <pre onScroll={e=>{const el=e.currentTarget;if(el.scrollTop+el.clientHeight<el.scrollHeight-20)setFollow(false);}}>{page?.records.map(r=>`[${r.time}] [${r.stream}] ${r.text}`).join('')||'暂无正文'}</pre>
  <p>每次最多显示一页（64 KiB），翻页替换当前页；不影响业务执行或回合结果。</p><p role="status">{notice}</p>
 </dialog>;
}
