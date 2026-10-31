import {BatchComparison} from './batch-reports';
import React, {useEffect, useState, useRef} from 'react';
import {api} from './api';
import {BatchDetail} from './batch-detail';
import {usePoll} from './poll';
import {SuiteManager} from './suites';
import {ImageClients} from './image-runtime';
import {RunWizard, type Template, type Suite} from './run-wizard';

type Batch = {scheduling_paused?:boolean;id:string;title:string;status:string;total:number;succeeded:number;finished:number;total_episodes:number;
  cases:{job_id:string;case_id:string;task_name:string;episodes:number;seed:number;status:string;attempt:number;error:string|null;run_id:string;episode_reporting:boolean;progress:{completed:number;successes:number;errors:number;success_rate:number|null}}[]};
const labels:Record<string,string>={PENDING:'排队中',RESERVED:'已分配',STARTING:'正在启动',RUNNING:'运行中',SUCCEEDED:'成功',FAILED:'失败',TIMED_OUT:'超时',CANCELLED:'已取消',CANCEL_REQUESTED:'取消中'};
function Field({label,children}:{label:string;children:React.ReactNode}) {
  const id=React.useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{React.cloneElement(children as React.ReactElement<{id:string}>,{id})}</div>;
}

export function CSWorkbench({renderJob,admin=false}:{admin?:boolean;renderJob:(id:string,close:()=>void)=>React.ReactNode}) {
  const [templates,setTemplates]=useState<Template[]>([]),[pools,setPools]=useState<{id:string;name:string}[]>([]),[batches,setBatches]=useState<Batch[]>([]);
  const [error,setError]=useState(''),[selected,setSelected]=useState(''),[page,setPage]=useState(1);
  const [suites,setSuites]=useState<Suite[]>([]),[launch,setLaunch]=useState<{suite:Suite}|null>(null);
  const [compareIds,setCompareIds]=useState<string[]>([]);
  const [compareRoute,setCompareRoute]=useState(()=>location.hash.match(/^#\/cs\/compare\/([^/]+)\/([^/]+)$/)?.slice(1)||[]);
  const batchFilters=useRef<Record<string,{status:string;reason:string;query:string}>>({});
  const [batchId,setBatchId]=useState(()=>location.hash.match(/^#\/cs\/batches\/([^/]+)$/)?.[1]||'');
  useEffect(()=>{const change=()=>{setCompareRoute(location.hash.match(/^#\/cs\/compare\/([^/]+)\/([^/]+)$/)?.slice(1)||[]);setBatchId(location.hash.match(/^#\/cs\/batches\/([^/]+)$/)?.[1]||'');setTab('runs');};window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change);},[]);
  const openBatch=(id:string)=>{location.hash='/cs/batches/'+id;};
  const [tab,setTab]=useState<'suites'|'runs'|'clients'>('runs');
  const tabs=[{id:'suites',label:'评测集合管理'},{id:'runs',label:'评测集合运行'},{id:'clients',label:'客户端注册'}] as const;
  const refresh=async()=>{
    const [t,p,b,s]=await Promise.all([api<Template[]>('client-templates'),api<{id:string;name:string}[]>('pools'),api<Batch[]>('batches?page='+page),api<Suite[]>('suites')]);
    setSuites(old=>{const selected=old.find(x=>x.id===launch?.suite.id);return selected&&!s.some(x=>x.id===selected.id)?[selected,...s]:s;});setTemplates(t.filter(x=>x.enabled));setPools(p);setBatches(b);
  };
  useEffect(()=>{
    let active=true,timer:ReturnType<typeof setTimeout>;
    async function load(){try{
      const [t,p,b,s]=await Promise.all([api<Template[]>('client-templates'),api<{id:string;name:string}[]>('pools'),api<Batch[]>('batches?page='+page),api<Suite[]>('suites')]);
      if(active){setTemplates(t.filter(x=>x.enabled));setPools(p);setBatches(b);setSuites(old=>{const selected=old.find(x=>x.id===launch?.suite.id);return selected&&!s.some(x=>x.id===selected.id)?[selected,...s]:s;});setError('');}
    }catch(e){if(active)setError(String(e));}finally{if(active)timer=setTimeout(load,2000);}}
    if(!batchId&&!compareRoute.length)void load();return()=>{active=false;clearTimeout(timer);};
  },[page,launch,batchId,compareRoute.join()]);
  function useSuite(suite:Suite) {
    setSuites(old=>[suite,...old.filter(x=>x.id!==suite.id)]);
    setLaunch({suite});setTab('runs');
  }
  return <>
    <div className="page-title"><div><p className="eyebrow">CLIENT / SERVER</p><h1>CS 评测集合与运行</h1><p className="muted">提交完整清单，逐项调度、查看结果并补跑失败任务。</p></div></div>
    {error&&<p className="error" role="alert">{error}</p>}
    <div className="cs-tabs" role="tablist" aria-label="CS 评测管理">
      {tabs.map((item,index)=><button key={item.id} id={'cs-tab-'+item.id} type="button" role="tab"
        aria-selected={tab===item.id} aria-controls={'cs-panel-'+item.id} tabIndex={tab===item.id?0:-1}
        onClick={()=>setTab(item.id)} onKeyDown={e=>{
          const next=e.key==='ArrowRight'?(index+1)%tabs.length:e.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:null;
          if(next!==null){e.preventDefault();setTab(tabs[next].id);document.getElementById('cs-tab-'+tabs[next].id)?.focus();}
        }}>{item.label}</button>)}
    </div>
    <div role="tabpanel" id="cs-panel-suites" aria-labelledby="cs-tab-suites" hidden={tab!=='suites'}>
      <SuiteManager templates={templates} onRun={useSuite} onJob={id=>{setSelected(id);setTab('runs');}} onSaved={()=>{void refresh().catch(e=>setError(String(e)));}}/>
    </div>
    <div role="tabpanel" id="cs-panel-clients" aria-labelledby="cs-tab-clients" hidden={tab!=='clients'}>
      <ImageClients onChange={()=>{void refresh().catch(e=>setError(String(e)));}}/>
      <ClientSources/>
      {admin?<ClientTemplates onChange={()=>{void refresh().catch(e=>setError(String(e)));}}/>:
        <section className="panel"><h2>可用客户端模板</h2><p className="hint">选择已有模板注册集合，或提交自己的客户端代码。</p>
          {!templates.length&&<p>暂无可用客户端模板。</p>}
          <ul>{templates.map(t=><li key={t.id}>{t.name} / {t.version} · {t.protocols.join(', ')}</li>)}</ul>
        </section>}
    </div>
    <div role="tabpanel" id="cs-panel-runs" aria-labelledby="cs-tab-runs" hidden={tab!=='runs'}>
    <div hidden={!!batchId||!!compareRoute.length}><RunWizard templates={templates} pools={pools} suites={suites} launch={launch}
      onSubmitted={()=>{void refresh().catch(e=>setError(String(e)));}} onJob={setSelected} onBatch={openBatch}/>
    <section className="panel"><h2>批次进度</h2><p>选择两个批次比较结果（已选 {compareIds.length}/2，可跨页选择）。</p><button disabled={compareIds.length!==2} onClick={()=>{location.hash="/cs/compare/"+compareIds.join("/");}}>比较所选批次</button><button disabled={!compareIds.length} onClick={()=>setCompareIds([])}>清空比较选择</button>{!batches.length&&<p>尚无评测批次。</p>}
      {batches.map(b=><div key={b.id}><label><input type="checkbox" aria-label={"比较选择 "+b.title} checked={compareIds.includes(b.id)} disabled={compareIds.length>=2&&!compareIds.includes(b.id)} onChange={e=>setCompareIds(old=>e.target.checked?[...old,b.id]:old.filter(x=>x!==b.id))}/>加入比较</label><h3>{b.title} · {labels[b.status]}{b.scheduling_paused?' · 调度已暂停':''} · 已成功 {b.succeeded}/{b.total}</h3><p className="hint">已结束 {b.finished}/{b.total}，计划 {b.total_episodes} 个回合。</p><button onClick={()=>openBatch(b.id)}>查看批次</button></div>)}
      <div className="pagination"><button disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页</button><span>第 {page} 页</span><button disabled={batches.length<20} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
    </section>
    </div>
    {compareRoute.length===2&&tab==='runs'&&<BatchComparison left={compareRoute[0]} right={compareRoute[1]} onBack={()=>{location.hash='/cs';}}/>}
    {batchId&&tab==='runs'&&<BatchDetail key={batchId} id={batchId} savedFilters={batchFilters} onBack={()=>{location.hash='/cs';}} onJob={setSelected}/>}
    {selected&&renderJob(selected,()=>setSelected(''))}
    </div>
  </>;
}



export function ClientTemplates({onChange}:{onChange?:()=>void}={}) {
  const [templates,setTemplates]=useState<Template[]>([]),[error,setError]=useState('');
  const refresh=()=>api<Template[]>('client-templates').then(setTemplates);
  useEffect(()=>{void refresh().catch(e=>setError(String(e)));},[]);
  return <section className="panel"><h2>客户端模板</h2><p className="hint">每个版本固定镜像和命令。镜像需包含平台 runner 包，并在 Worker 的 client_images 中登记。生产建议使用镜像 digest。</p>{error&&<p role="alert" className="error">{error}</p>}
    <form onSubmit={async e=>{e.preventDefault();try{const f=new FormData(e.currentTarget);await api('admin/client-templates','POST',{name:f.get('name'),version:f.get('version'),image:f.get('image'),command:JSON.parse(String(f.get('command'))),protocols:String(f.get('protocols')).split(',').map(x=>x.trim()),episode_reporting:f.get('episode_reporting')==='on',endpoint_exclusive:f.get('endpoint_exclusive')==='on'});await refresh();onChange?.();setError('');}catch(e){setError(String(e));}}}>
      <div className="submit-grid"><Field label="客户端名称"><input name="name" required maxLength={100}/></Field><Field label="客户端版本"><input name="version" required maxLength={100}/></Field><Field label="客户端镜像"><input name="image" required maxLength={500}/></Field><Field label="启动参数数组 JSON"><textarea name="command" defaultValue='["python", "-m", "runner.cs_demo"]' required/></Field><Field label="支持协议（逗号分隔）"><input name="protocols" defaultValue="http" required/></Field></div>
      <label><input type="checkbox" name="episode_reporting"/>SDK 逐回合上报（成功收尾时校验完整性）</label><label><input type="checkbox" name="endpoint_exclusive"/>推理服务独占连接（同主机端口串行）</label><button>登记客户端模板</button>
    </form>
    <table><thead><tr><th>名称 / 版本</th><th>镜像</th><th>协议</th><th></th></tr></thead><tbody>{templates.map(t=><tr key={t.id}><td>{t.name} / {t.version}</td><td>{t.image}</td><td>{t.protocols.join(', ')}</td><td><button onClick={async()=>{try{await api('admin/client-templates/'+t.id,'PATCH',{enabled:!t.enabled});await refresh();onChange?.();}catch(e){setError(String(e));}}}>{t.enabled?'停用模板':'启用模板'}</button></td></tr>)}</tbody></table>
  </section>;
}

export function ArtifactList({jobId,runId}:{jobId:string;runId:string}) {
  const [downloadError,setError]=useState('');
  const base=`jobs/${jobId}/attempts/${runId}/artifacts`;
  const {data,error:pollError}=usePoll<{id:string;name:string;size_bytes:number}[]>(base);
  const files=data||[],error=downloadError||pollError;
  return <><h3>评测产物</h3>{error&&<p className="error">{error}</p>}{!files.length&&<p className="hint">尚无产物。每项最多 8 个文件，单文件 8 MiB，总计 32 MiB。</p>}{files.map(f=><p key={f.id}><button onClick={async()=>{try{const response=await fetch('/api/v1/'+base+'/'+f.id,{headers:{Authorization:'Bearer '+sessionStorage.getItem('eval-token')}});if(!response.ok)throw new Error('下载失败或登录已过期');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=f.name.split('/').at(-1)!;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError(String(e));}}}>下载 {f.name}</button> · {(f.size_bytes/1024).toFixed(1)} KiB</p>)}</>;
}


export function EpisodeList({jobId,runId}:{jobId:string;runId:string}) {
  const [page,setPage]=useState(1);
  const {data,error}=usePoll<any>(`jobs/${jobId}/attempts/${runId}/episodes?page=${page}`);
  return <><h3>回合进度与部分结果</h3>{error&&<p role="alert">{error}</p>}{data&&<>
    <p>已完成 {data.progress.completed}/{data.planned}，目标成功 {data.progress.successes}，执行异常 {data.progress.errors}。成功率仅统计已完成回合。</p>
    <table><thead><tr><th>回合</th><th>种子</th><th>执行状态</th><th>目标结果</th><th>指标 / 异常</th></tr></thead><tbody>{data.items.map((r:any)=><tr key={r.episode_id}><td>{r.episode_id}</td><td>{r.seed}</td><td>{r.result?(r.result.status==='COMPLETED'?'已完成':'执行异常'):'已开始'}</td><td>{r.result?.status==='COMPLETED'?(r.result.success?'成功':'失败'):'—'}</td><td>{JSON.stringify(r.result?.metrics||r.result?.error||{})}</td></tr>)}</tbody></table>
    <button disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页回合</button><span>第 {page} 页</span><button disabled={data.items.length<100} onClick={()=>setPage(p=>p+1)}>下一页回合</button>
  </>}</>;
}


function ClientSources() {
  const [builds,setBuilds]=useState<any[]>([]),[files,setFiles]=useState<Record<string,string>>({}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const load=()=>api<any[]>('client-builds').then(setBuilds);
  useEffect(()=>{void load().catch(e=>setError(String(e)));const t=setInterval(()=>{void load().catch(e=>setError(String(e)));},4000);return()=>clearInterval(t);},[]);
  return <section className="panel"><h2>提交仿真客户端代码</h2><p className="hint">选择 Python 源码和 requirements.txt，入口格式为 client:Client。构建完成后还需将镜像分发到对应 Worker；未就绪的评测会排队。</p>
    {error&&<p role="alert" className="error">{error}</p>}
    <form onSubmit={async e=>{e.preventDefault();const f=new FormData(e.currentTarget);setBusy(true);try{await api('client-builds','POST',{name:f.get('name'),version:f.get('version'),entrypoint:f.get('entrypoint'),files});await load();setError('');}catch(err){setError(String(err));}finally{setBusy(false);}}}>
      <div className="submit-grid"><Field label="代码客户端名称"><input name="name" required maxLength={100}/></Field><Field label="代码版本"><input name="version" required maxLength={100}/></Field><Field label="SDK 入口类"><input name="entrypoint" required placeholder="client:Client"/></Field>
      <Field label="客户端源码文件"><input type="file" multiple accept=".py,.txt,.json,.yaml,.yml,.md" onChange={async e=>{try{const selected=Array.from(e.target.files||[]);if(selected.reduce((n,f)=>n+f.size,0)>1048576)throw new Error('代码包最多 1 MiB');const entries=await Promise.all(selected.map(async f=>[f.name,await f.text()]));setFiles(Object.fromEntries(entries));setError('');}catch(err){setError(String(err));}}}/></Field></div>
      <button disabled={busy||!Object.keys(files).length}>提交构建</button>
    </form>
    {builds.map(b=><details key={b.id}><summary>{b.name} / {b.version} · {b.status==='PENDING'?'等待构建':b.status==='BUILDING'?'构建中':b.status==='SUCCEEDED'?'构建成功':'构建失败'}</summary><p>源码摘要：{b.sha256}</p>{b.template_id&&<p>可选择客户端：source-{b.id} / {b.version}</p>}<pre>{b.log||'等待构建执行器处理'}</pre></details>)}
  </section>;
}
