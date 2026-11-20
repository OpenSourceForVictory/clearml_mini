import {ArtifactBrowser} from './artifact-browser';
import {ProcessLogs} from './process-logs';
import type {RerunDraft,WizardLaunch} from './batch-rerun';
import {ResultOutcomes,executionLabels,protocolLabels} from './result-outcomes';
import {FunctionPages} from './navigation';
import {SuiteManager} from './suites';
import {BatchComparison} from './batch-reports';
import React, {useEffect, useState, useRef} from 'react';
import {api} from './api';
import {BatchDetail,statusLabels} from './batch-detail';
import {usePoll} from './poll';
import {RunWizard, type Template, type Suite} from './run-wizard';

import {BatchHistory,type Batch} from './batch-history';
function Field({label,children}:{label:string;children:React.ReactNode}) {
  const id=React.useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{React.cloneElement(children as React.ReactElement<{id:string}>,{id})}</div>;
}

export function CSWorkbench({renderJob,admin=false,pageView='wizard',initialLaunch=null}:{initialLaunch?:WizardLaunch|null;pageView?:'wizard'|'records';admin?:boolean;renderJob:(id:string,close:()=>void)=>React.ReactNode}) {
  const [templates,setTemplates]=useState<Template[]>([]),[pools,setPools]=useState<{id:string;name:string}[]>([]),[batches,setBatches]=useState<Batch[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState(''),[selected,setSelected]=useState(''),[page,setPage]=useState(1);
  const [suites,setSuites]=useState<Suite[]>([]),[launch,setLaunch]=useState<WizardLaunch|null>(initialLaunch);
  useEffect(()=>{if(initialLaunch)setLaunch(initialLaunch)},[initialLaunch]);
  const rerunSequence=useRef(0);
  const [importState,setImportState]=useState<{id:string;loading:boolean;error:string}|null>(null);
  async function importBatch(id:string){
    const seq=++rerunSequence.current;setLaunch(null);setImportState({id,loading:true,error:''});
    try{const draft=await api<RerunDraft>('batches/'+id+'/rerun-draft');if(seq!==rerunSequence.current)return;if(draft.schema_version!==1)throw Error('不支持的历史配置版本');setLaunch({rerun:draft});setImportState(null);location.hash='/cs';}
    catch(e){if(seq===rerunSequence.current)setImportState({id,loading:false,error:String(e)==='Error: Not Found'?'历史批次导入接口不可用，请管理员更新 API 并核对反向代理路由；仅更新前端不足以启用此功能。':String(e)==='Error: batch not found'?'原批次不存在、已删除或当前账号无权访问。':String(e)});}
  }
  const [resultsTab,setResultsTab]=useState('history');
  const deletedIds=useRef(new Set<string>());
  const [compareIds,setCompareIds]=useState<string[]>([]);
  const [compareRoute,setCompareRoute]=useState(()=>location.hash.match(/^#\/cs\/compare\/([^/]+)\/([^/]+)$/)?.slice(1)||[]);
  const batchFilters=useRef<Record<string,{status:string;reason:string;query:string}>>({});
  const [batchId,setBatchId]=useState(()=>location.hash.match(/^#\/cs\/batches\/([^/]+)$/)?.[1]||'');
  useEffect(()=>{const change=()=>{setCompareRoute(location.hash.match(/^#\/cs\/compare\/([^/]+)\/([^/]+)$/)?.slice(1)||[]);setBatchId(location.hash.match(/^#\/cs\/batches\/([^/]+)$/)?.[1]||'');};window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change);},[]);
  const openBatch=(id:string)=>{setResultsTab('history');location.hash='/cs/batches/'+id;};
  const refresh=async()=>{
    const [t,p,b,s]=await Promise.all([api<Template[]>('client-templates'),api<{id:string;name:string}[]>('pools'),api<Batch[]>('batches?page='+page),api<Suite[]>('suites')]);
    setSuites(old=>{const selected=old.find(x=>x.id===launch?.suite?.id);return selected&&!s.some(x=>x.id===selected.id)?[selected,...s]:s;});setTemplates(t.filter(x=>x.enabled));setPools(p);setBatches(b.filter(x=>!deletedIds.current.has(x.id)));
  };
  useEffect(()=>{
    setLoading(true);
    let active=true,timer:ReturnType<typeof setTimeout>;
    async function load(){try{
      const [t,p,b,s]=await Promise.all([api<Template[]>('client-templates'),api<{id:string;name:string}[]>('pools'),api<Batch[]>('batches?page='+page),api<Suite[]>('suites')]);
      if(active){setTemplates(t.filter(x=>x.enabled));setPools(p);setBatches(b.filter(x=>!deletedIds.current.has(x.id)));setSuites(old=>{const selected=old.find(x=>x.id===launch?.suite?.id);return selected&&!s.some(x=>x.id===selected.id)?[selected,...s]:s;});setError('');}
    }catch(e){if(active)setError(String(e));}finally{if(active){setLoading(false);timer=setTimeout(load,2000);}}}
    if(!batchId&&!compareRoute.length)void load();return()=>{active=false;clearTimeout(timer);};
  },[page,launch,batchId,compareRoute.join()]);
  return <>
    <div className="page-title"><div><p className="eyebrow">CLIENT / SERVER</p><h1>{pageView==='wizard'?'业务评测向导':'评测运行与结果'}</h1><p className="muted">{pageView==='wizard'?'选择任务、客户端和推理服务，检查后提交业务评测。':'按提交批次查看运行状态、任务参数、服务状态与评测结果。'}</p></div></div>
    {error&&<p className="error" role="alert">{error}</p>}
    {importState&&<section aria-label="历史批次导入">{importState.loading?<p role="status">正在读取历史批次配置…</p>:<p role="alert">历史配置导入失败：{importState.error}<button onClick={()=>void importBatch(importState.id)}>重试导入</button></p>}</section>}
    <div hidden={!!batchId||!!compareRoute.length}><div hidden={pageView==='records'}><RunWizard key={launch?.rerun?rerunSequence.current:0} templates={templates} pools={pools} suites={suites} launch={launch}
      onSubmitted={()=>{void refresh().catch(e=>setError(String(e)));}} onJob={setSelected} onBatch={openBatch}/></div>
    <div hidden={pageView==='wizard'}><FunctionPages label="评测运行与结果" value={resultsTab} onChange={setResultsTab} lazy items={[{id:'history',label:'运行记录',content:<BatchHistory batches={batches} page={page} loading={loading} error={error} compareIds={compareIds} onCompareChange={setCompareIds} onOpen={openBatch} onRerun={id=>void importBatch(id)} onPage={p=>{setBatches([]);setPage(p);}} onPriorityChanged={updated=>setBatches(old=>old.map(b=>b.id===updated.id?{...b,...updated}:b))} onDeleted={id=>{deletedIds.current.add(id);setCompareIds(old=>old.filter(x=>x!==id));setBatches(old=>old.filter(x=>x.id!==id));if(batches.length===1&&page>1)setPage(page-1);else void refresh().catch(e=>setError(String(e)));}}/>},{id:'suites',label:'评测任务集合管理',content:<SuiteManager templates={templates} onSaved={()=>void refresh().catch(e=>setError(String(e)))} onJob={setSelected} onRun={suite=>{setResultsTab('history');setLaunch({suite});location.hash='/cs';}}/>}]}/></div>
    </div>
    {compareRoute.length===2&&<BatchComparison left={compareRoute[0]} right={compareRoute[1]} onBack={()=>{location.hash='/cs/results';}}/>}
    {batchId&&<BatchDetail key={batchId} id={batchId} savedFilters={batchFilters} onRerun={id=>void importBatch(id)} onBack={()=>{location.hash='/cs/results';}} onJob={setSelected}/>}
    {selected&&renderJob(selected,()=>setSelected(''))}
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
  return <><h3>评测产物</h3><ArtifactBrowser jobId={jobId} runId={runId}/>{error&&<p className="error">{error}</p>}{data&&!error&&!files.length&&<p className="hint">尚无产物。每项最多 8 个文件，单文件 8 MiB，总计 32 MiB。</p>}{files.map(f=><p key={f.id}><button onClick={async()=>{try{const response=await fetch('/api/v1/'+base+'/'+f.id,{headers:{Authorization:'Bearer '+sessionStorage.getItem('eval-token')}});if(!response.ok)throw new Error('下载失败或登录已过期');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=f.name.split('/').at(-1)!;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError(String(e));}}}>下载 {f.name}</button> · {(f.size_bytes/1024).toFixed(1)} KiB</p>)}</>;
}


export function EpisodeList({jobId,runId}:{jobId:string;runId:string}) {
  const [page,setPage]=useState(1);
  const {data,error}=usePoll<any>(`jobs/${jobId}/attempts/${runId}/episodes?page=${page}&include_planned=true`);
  const terminal=data&&['SUCCEEDED','FAILED','TIMED_OUT','CANCELLED'].includes(data.run_status);
  const command=data?.result_source==='exit_code',single=data?.outcomes?.detail_mode==='execution'||(data?.outcomes?.detail_mode==null&&command&&(data?.outcomes?.process_scope==='single_process'||(!data?.outcomes?.process_scope&&!data?.episode_reporting)));
  function state(r:any){
    return executionLabels[r.execution_state] || (r.result?.exit_code===0?'正常结束':r.result?.exit_code!=null?'异常退出':r.started&&!terminal&&!r.result?'运行中':'退出码未上报');
  }
  if(data&&single)return <section aria-label="整次执行状态"><h3>整次执行状态</h3><ProcessLogs jobId={jobId} runId={runId}/>
    {error&&<p role="alert">执行状态读取失败：{error}</p>}
    <p>普通命令只启动一次，配置回合数 {data.planned}；平台不解析程序内部回合，不显示逐回合状态。</p>
    <p>任务生命周期：{statusLabels[data.run_status]||data.run_status} · 平台执行进程退出码：{data.exit_code??'未上报'}</p>
    <p>{data.exit_code===0?'正常结束':data.exit_code!=null?'执行异常（包括启动或包装器错误）':'尚无退出码，不能判定正常结束'}</p>
    <ResultOutcomes value={data.outcomes}/>
    {data.run_error&&<p className="error">任务诊断：{data.run_error}</p>}
    <p>输出见本次执行公共日志；退出正常不代表内部回合评测成功。</p>
  </section>;
  return <><h3>回合状态与结果</h3><ProcessLogs jobId={jobId} runId={runId}/>{error&&<p role="alert">回合状态读取失败：{error}</p>}{!data&&!error&&<p>正在读取回合状态…</p>}{data&&<>
    <p>判定来源：{command?'普通命令退出码（0 为执行成功）':'平台协议业务结果（success）'}。{single?'此命令只启动一次，显示整次执行结果。':`已上报完成 ${data.progress.completed}/${data.planned}，异常回合 ${data.progress.errors}。`}</p>
    <ResultOutcomes value={data.outcomes}/>
    <p>平台执行进程退出码：{data.exit_code ?? '未上报'}（整次执行，不等同于每回合业务子进程退出码）</p>
    {data.run_error&&<p className="error">任务诊断：{data.run_error}</p>}
    {terminal&&!command&&data.items.some((r:any)=>!r.result)&&<p>缺少协议回合结果；退出码 0 不能代替协议成功上报。</p>}
    <div className="table-scroll"><table aria-label="逐回合状态"><thead><tr><th>回合</th><th>种子</th><th>程序执行状态</th><th>回合结果（平台协议）</th><th>业务进程退出码</th><th>指标 / 异常</th></tr></thead><tbody>{(single?data.items.slice(0,1):data.items).map((r:any)=><tr key={r.episode_id}><td>{single?'整次执行':r.episode_id}</td><td>{single?'—':r.seed}</td><td>{single?(data.outcomes?.execution.normal?'正常结束':data.outcomes?.execution.abnormal?'异常退出':'参见程序执行统计'):state(r)}</td><td>{command?'不适用':protocolLabels[r.protocol_state]||(r.result?.status==='COMPLETED'?(r.result.success?'业务成功':'业务失败'):r.result?.status==='ERROR'?'回合异常（未分类）':'结果未上报')}</td><td>{r.execution_state==='not_applicable'?'不适用':r.result?.exit_code??'未上报'}</td><td>{JSON.stringify(r.result?.metrics||r.result?.error||(single?data.run_error:null)||{})}<ArtifactBrowser jobId={jobId} runId={runId} episode={r.episode_id}/></td></tr>)}</tbody></table></div>
    <button disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页回合</button><span>第 {page} 页</span><button disabled={single||page*100>=data.planned} onClick={()=>setPage(p=>p+1)}>下一页回合</button>
  </>}</>;
}


export function ClientSources() {
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
