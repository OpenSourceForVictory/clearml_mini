import React, {useEffect, useRef, useState} from 'react';
import {api} from './api';
import {SuiteManager} from './suites';

type Template = {id:string;name:string;version:string;image:string;command:string[];protocols:string[];enabled:boolean};
type Suite = {id:string;name:string;version:string;template_id:string;cases:Case[]};
type Pool = {id:string;name:string};
type Case = {case_id:string;task_name:string;episodes:number;seed:number;parameters?:Record<string,unknown>};
type Batch = {id:string;title:string;status:string;total:number;succeeded:number;finished:number;total_episodes:number;
  cases:{job_id:string;case_id:string;task_name:string;episodes:number;seed:number;status:string;attempt:number;error:string|null;run_id:string;episode_reporting:boolean;progress:{completed:number;successes:number;errors:number;success_rate:number|null}}[]};
const labels:Record<string,string>={PENDING:'排队中',RESERVED:'已分配',STARTING:'正在启动',RUNNING:'运行中',SUCCEEDED:'成功',FAILED:'失败',TIMED_OUT:'超时',CANCELLED:'已取消',CANCEL_REQUESTED:'取消中'};
function Field({label,children}:{label:string;children:React.ReactNode}) {
  const id=React.useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{React.cloneElement(children as React.ReactElement<{id:string}>,{id})}</div>;
}
function parseCases(value:unknown):Case[] {
  if(!Array.isArray(value)||!value.length||value.length>100) throw new Error('清单需要包含 1–100 项任务');
  const ids=new Set<string>();
  return value.map((raw,i)=>{
    if(!raw || typeof raw !== 'object') throw new Error(`第 ${i+1} 项格式错误`);
    const c=raw as Case;
    if(typeof c.case_id!=='string'||!/^[A-Za-z0-9_.-]{1,100}$/.test(c.case_id)||ids.has(c.case_id)) throw new Error(`第 ${i+1} 项标识无效或重复`);
    if(typeof c.task_name!=='string'||!c.task_name.trim()||c.task_name.length>200) throw new Error(`第 ${i+1} 项缺少有效任务名`);
    if(!Number.isInteger(c.episodes)||c.episodes<1||c.episodes>10000) throw new Error(`第 ${i+1} 项回合数应为 1–10000`);
    const seed=c.seed??0;
    if(!Number.isInteger(seed)||seed<0||seed>2147483647) throw new Error(`第 ${i+1} 项种子无效`);
    if(c.parameters && (typeof c.parameters!=='object'||Array.isArray(c.parameters))) throw new Error(`第 ${i+1} 项参数必须是 JSON 对象`);
    ids.add(c.case_id); return {case_id:c.case_id,task_name:c.task_name,episodes:c.episodes,seed,parameters:c.parameters||{}};
  });
}

export function CSWorkbench({renderJob,admin=false}:{admin?:boolean;renderJob:(id:string,close:()=>void)=>React.ReactNode}) {
  const [templates,setTemplates]=useState<Template[]>([]),[pools,setPools]=useState<Pool[]>([]),[batches,setBatches]=useState<Batch[]>([]);
  const [cases,setCases]=useState<Case[]>([{case_id:'case-001',task_name:'demo',episodes:3,seed:0,parameters:{}}]);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[selected,setSelected]=useState(''),[page,setPage]=useState(1);
  const [suites,setSuites]=useState<Suite[]>([]),[suiteId,setSuiteId]=useState(''),[templateId,setTemplateId]=useState('');
  const [episodeView,setEpisodeView]=useState<{jobId:string;runId:string}|null>(null);
  const [tab,setTab]=useState<'suites'|'runs'|'clients'>('runs');
  const tabs=[{id:'suites',label:'评测集合管理'},{id:'runs',label:'评测集合运行'},{id:'clients',label:'客户端注册'}] as const;
  const key=useRef({body:'',value:''});
  const refresh=async()=>{
    const [t,p,b,s]=await Promise.all([api<Template[]>('client-templates'),api<Pool[]>('pools'),api<Batch[]>('batches?page='+page),api<Suite[]>('suites')]);
    setSuites(old=>{const selected=old.find(x=>x.id===suiteId);return selected&&!s.some(x=>x.id===selected.id)?[selected,...s]:s;});setTemplates(t.filter(x=>x.enabled));setPools(p);setBatches(b);
  };
  useEffect(()=>{let active=true;const load=()=>{if(active) void refresh().catch(e=>setError(String(e)));};load();const timer=setInterval(load,2000);return()=>{active=false;clearInterval(timer);};},[page,suiteId]);
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault();setError('');setBusy(true);
    try {
      const f=new FormData(e.currentTarget);
      const body={title:f.get('title'),pool_id:f.get('pool_id'),gpu_mode:f.get('gpu_mode'),requested_vram_gb:Number(f.get('requested_vram_gb')),
        required_gpu_model:String(f.get('required_gpu_model')||'').trim()||null,
        client:{template_id:f.get('template_id'),endpoint:f.get('endpoint'),request_timeout_seconds:Number(f.get('request_timeout_seconds')),timeout_seconds:Number(f.get('timeout_seconds'))},
        cases:suiteId?[]:parseCases(cases),suite_id:suiteId||null,max_concurrent:Number(f.get('max_concurrent'))};
      const serialized=JSON.stringify(body);
      if(key.current.body!==serialized) key.current={body:serialized,value:crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random()};
      await api<Batch>('batches','POST',body,{'Idempotency-Key':key.current.value});key.current={body:'',value:''};await refresh();
    } catch(e){setError(String(e));} finally{setBusy(false);}
  }
  function useSuite(suite:Suite) {
    setSuites(old=>[suite,...old.filter(x=>x.id!==suite.id)]);
    setSuiteId(suite.id);setTemplateId(suite.template_id);setCases(suite.cases);setTab('runs');
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
      <ClientSources/>
      {admin?<ClientTemplates onChange={()=>{void refresh().catch(e=>setError(String(e)));}}/>:
        <section className="panel"><h2>可用客户端模板</h2><p className="hint">选择已有模板注册集合，或提交自己的客户端代码。</p>
          {!templates.length&&<p>暂无可用客户端模板。</p>}
          <ul>{templates.map(t=><li key={t.id}>{t.name} / {t.version} · {t.protocols.join(', ')}</li>)}</ul>
        </section>}
    </div>
    <div role="tabpanel" id="cs-panel-runs" aria-labelledby="cs-tab-runs" hidden={tab!=='runs'}>
    <section className="panel"><h2>发起集合运行</h2>
      <p className="hint">模型由外部推理服务管理。客户端模板定义可执行任务；“协议自检”模板仅验证通信和任务链路。</p>
      <form onSubmit={submit}>
        <div className="submit-grid">
          <Field label="选择已保存集合"><select value={suiteId} onChange={e=>{setSuiteId(e.target.value);const s=suites.find(x=>x.id===e.target.value);if(s){setCases(s.cases);setTemplateId(s.template_id);}}}><option value="">自定义清单</option>{suites.map(s=><option key={s.id} value={s.id}>{s.name} / {s.version}</option>)}</select></Field>
          <Field label="最大并发任务数"><input name="max_concurrent" type="number" min={1} max={100} defaultValue={1} required/></Field>
          <Field label="批次名称"><input name="title" required maxLength={200}/></Field>
          <Field label="客户端模板"><select name="template_id" required value={templateId} onChange={e=>{setTemplateId(e.target.value);setSuiteId('');}}><option value="" disabled>选择客户端及版本</option>{templates.map(t=><option key={t.id} value={t.id}>{t.name} / {t.version}</option>)}</select></Field>
          <Field label="推理服务地址"><input name="endpoint" required maxLength={2000} placeholder="http://推理机器:端口/infer"/></Field>
          <Field label="客户端资源池"><select name="pool_id" required defaultValue=""><option value="" disabled>选择资源池</option>{pools.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
          <Field label="客户端 GPU 模式"><select name="gpu_mode"><option value="exclusive">独占</option><option value="shared">共享</option></select></Field>
          <Field label="每项所需显存 GiB"><input name="requested_vram_gb" type="number" min={0} max={10000} defaultValue={0} required/></Field>
          <Field label="指定 GPU 型号（可选）"><input name="required_gpu_model" maxLength={100}/></Field>
          <Field label="请求超时秒"><input name="request_timeout_seconds" type="number" min={1} max={120} defaultValue={10} required/></Field>
          <Field label="每项总超时秒"><input name="timeout_seconds" type="number" min={1} max={3600} defaultValue={300} required/></Field>
        </div>
        <h3>评测清单</h3>
        {suiteId&&<p>使用已保存版本。临时调整请先选择“自定义清单”；保存新集合请到“评测集合管理”页。</p>}
        <CaseEditor cases={cases} setCases={setCases} setError={setError} disabled={!!suiteId}/>
        <button className="primary" disabled={busy||!templates.length}>{busy?'提交中…':'提交评测批次'}</button>
      </form>
    </section>
    <section className="panel"><h2>批次进度</h2>{!batches.length&&<p>尚无评测批次。</p>}
      {batches.map(b=><div key={b.id}><h3>{b.title} · {labels[b.status]} · 已成功 {b.succeeded}/{b.total}</h3><p className="hint">已结束 {b.finished}/{b.total}，计划 {b.total_episodes} 个回合。失败和取消不计为成功。</p>
        <table><thead><tr><th>标识 / 任务</th><th>回合 / 种子</th><th>状态</th><th>尝试</th><th></th></tr></thead><tbody>{b.cases.map(c=><tr key={c.job_id}><td>{c.case_id}<small>{c.task_name}</small></td><td>{c.episode_reporting?`${c.progress.completed} / ${c.episodes} 回合，目标成功 ${c.progress.successes}`:`计划 ${c.episodes} 回合（客户端未上报回合）`}<small>种子 {c.seed}</small></td><td>{labels[c.status]}</td><td>{c.attempt}</td><td><button onClick={()=>setEpisodeView({jobId:c.job_id,runId:c.run_id})}>回合结果</button><button onClick={()=>setSelected(c.job_id)}>详情 / 重试</button></td></tr>)}</tbody></table>
      </div>)}
      <div className="pagination"><button disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页</button><span>第 {page} 页</span><button disabled={batches.length<20} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
    </section>
    {episodeView&&<section className="panel"><button onClick={()=>setEpisodeView(null)}>关闭回合结果</button><EpisodeList {...episodeView}/></section>}
    {selected&&renderJob(selected,()=>setSelected(''))}
    </div>
  </>;
}


function CaseEditor({cases,setCases,setError,disabled=false}:{cases:Case[];setCases:React.Dispatch<React.SetStateAction<Case[]>>;setError:(error:string)=>void;disabled?:boolean}) {
  function change(i:number,k:keyof Case,value:unknown) {setCases(old=>old.map((c,n)=>n===i?{...c,[k]:value}:c));}
  return <>
        <fieldset disabled={disabled}>
        <Field label="导入 JSON 清单"><input type="file" accept="application/json,.json" onChange={async e=>{const file=e.target.files?.[0];if(!file)return;try{if(file.size>1048576)throw new Error('清单文件不能超过 1 MiB');setCases(parseCases(JSON.parse(await file.text())));setError('');}catch(error){setError(String(error));}e.target.value='';}}/></Field>
        <div className="table-scroll"><table><thead><tr><th>唯一标识</th><th>仿真任务名</th><th>回合数</th><th>种子</th><th>参数 JSON</th><th></th></tr></thead><tbody>
          {cases.map((c,i)=><tr key={i}>
            <td><input aria-label={`第 ${i+1} 项标识`} value={c.case_id} maxLength={100} required onChange={e=>change(i,'case_id',e.target.value)}/></td>
            <td><input aria-label={`第 ${i+1} 项任务名`} value={c.task_name} maxLength={200} required onChange={e=>change(i,'task_name',e.target.value)}/></td>
            <td><input aria-label={`第 ${i+1} 项回合数`} type="number" min={1} max={10000} required value={c.episodes} onChange={e=>change(i,'episodes',Number(e.target.value))}/></td>
            <td><input aria-label={`第 ${i+1} 项种子`} type="number" min={0} max={2147483647} required value={c.seed} onChange={e=>change(i,'seed',Number(e.target.value))}/></td>
            <td><textarea aria-label={`第 ${i+1} 项参数`} key={JSON.stringify(c.parameters)} defaultValue={JSON.stringify(c.parameters||{})} onBlur={e=>{try{const v=JSON.parse(e.target.value);if(!v||typeof v!=='object'||Array.isArray(v))throw new Error();change(i,'parameters',v);e.target.setCustomValidity('');}catch{e.target.setCustomValidity('参数必须是 JSON 对象');e.target.reportValidity();}}}/></td>
            <td><button type="button" disabled={cases.length===1} onClick={()=>setCases(old=>old.filter((_,n)=>n!==i))}>移除</button></td>
          </tr>)}
        </tbody></table></div>
        <button type="button" disabled={cases.length>=100} onClick={()=>setCases(old=>[...old,{case_id:'case-'+(old.length+1)+'-'+Date.now().toString(36),task_name:'',episodes:3,seed:0,parameters:{}}])}>添加任务</button>
        </fieldset>
        <p>共 {cases.length} 项、{cases.reduce((sum,c)=>sum+c.episodes,0)} 个回合。每项独立调度；资源不足时保留排队；最大并发是上限。</p>
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
  const [files,setFiles]=useState<{id:string;name:string;size_bytes:number}[]>([]),[error,setError]=useState('');
  const base=`jobs/${jobId}/attempts/${runId}/artifacts`;
  useEffect(()=>{let active=true;setFiles([]);const load=()=>api<typeof files>(base).then(v=>{if(active)setFiles(v);}).catch(e=>{if(active)setError(String(e));});void load();const t=setInterval(load,2000);return()=>{active=false;clearInterval(t);};},[base]);
  return <><h3>评测产物</h3>{error&&<p className="error">{error}</p>}{!files.length&&<p className="hint">尚无产物。每项最多 8 个文件，单文件 8 MiB，总计 32 MiB。</p>}{files.map(f=><p key={f.id}><button onClick={async()=>{try{const response=await fetch('/api/v1/'+base+'/'+f.id,{headers:{Authorization:'Bearer '+sessionStorage.getItem('eval-token')}});if(!response.ok)throw new Error('下载失败或登录已过期');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=f.name.split('/').at(-1)!;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError(String(e));}}}>下载 {f.name}</button> · {(f.size_bytes/1024).toFixed(1)} KiB</p>)}</>;
}


export function EpisodeList({jobId,runId}:{jobId:string;runId:string}) {
  const [page,setPage]=useState(1),[data,setData]=useState<any>(null),[error,setError]=useState('');
  useEffect(()=>{let active=true;setData(null);const load=()=>api<any>(`jobs/${jobId}/attempts/${runId}/episodes?page=${page}`).then(v=>{if(active)setData(v);}).catch(e=>{if(active)setError(String(e));});void load();const timer=setInterval(load,2000);return()=>{active=false;clearInterval(timer);};},[jobId,runId,page]);
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
