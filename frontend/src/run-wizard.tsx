import React, {useEffect, useRef, useState} from 'react';
import {ResourceContract,ResourceRequest} from './resource-requirements';
import {api} from './api';
import {BusinessValues,ParameterSchema,ValuesDraft,parseValues} from './business-values';
import {ManagedServices,ServerTemplate,newService,servicePayload} from './managed-services';
import {TemplateCopy,CopyTemplate} from './template-copy';

export type Template = {resource_requirements?:ResourceContract|null;task_type?:string;execution_mode?:string;parameter_schema?:ParameterSchema|null;id:string;name:string;version:string;image:string;command:string[];protocols:string[];enabled:boolean;episode_reporting?:boolean;endpoint_exclusive?:boolean;runtime?:{overridable_env:string[]}|null};
export type Case = {case_id:string;task_name:string;episodes:number;seed:number;parameters?:Record<string,unknown>;service_keys?:string[];resources?:Record<string,unknown>};
export type Suite = {id:string;name:string;version:string;template_id:string;cases:Case[];client_defaults?:Record<string,unknown>};
type Reason = {code:string;category:string;message:string;action:string};
type Preview = {case_checks?:{case_id:string;resources:Record<string,unknown>;resource_sources:Record<string,string>}[];service_plan?:{key:string;resources:Record<string,unknown>;resource_sources:Record<string,string>}[];effective_parameters?:{case_id:string;parameters:Record<string,unknown>;sources:Record<string,string>}[];pair_checks?:{case_id:string;pair_ready:boolean}[];endpoints?:{url:string;mode:string;available:boolean;occupancy:number}[];checked_at:string;can_submit:boolean;status:string;task_count:number;total_episodes:number;cases:Case[];note:string;reasons:Reason[];nodes:{slot_id:string;node_name:string;gpu_index:number;gpu_model:string;ready:boolean;reasons:Reason[]}[]};
type Trial = {id:string;title:string;status:string;cases:{job_id:string;status:string;episode_reporting:boolean;progress:{completed:number;successes:number};error:string|null}[]};
const statuses:Record<string,string>={PENDING:'排队中',RESERVED:'已分配',STARTING:'正在启动',RUNNING:'运行中',SUCCEEDED:'执行完成',FAILED:'执行失败',TIMED_OUT:'超时',CANCELLED:'已取消',CANCEL_REQUESTED:'取消中'};
function Field({label,children}:{label:string;children:React.ReactNode}) {
  const id=React.useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{React.cloneElement(children as React.ReactElement<{id:string}>,{id})}</div>;
}
const initial={cpus:'',ram_gb:'',title:'',pool_id:'',gpu_mode:'exclusive',requested_vram_gb:0,required_gpu_model:'',max_concurrent:1,request_timeout_seconds:10,timeout_seconds:300,environment:'{}'};
export function RunWizard({templates,pools,suites,launch,onSubmitted,onJob,onBatch}:{templates:Template[];pools:{id:string;name:string}[];suites:Suite[];launch:{suite:Suite}|null;onSubmitted:()=>void;onBatch?:(id:string)=>void;onJob:(id:string)=>void}) {
  const [resourceDrafts,setResourceDrafts]=useState<Record<string,Record<string,any>>>({});
  const [draft,setDraft]=useState(initial),[suiteId,setSuiteId]=useState(''),[templateId,setTemplateId]=useState('');
  const [serviceMode,setServiceMode]=useState<'external'|'managed'|'none'>('external'),[servers,setServers]=useState<ServerTemplate[]>([]),[serviceRows,setServiceRows]=useState([newService()]),[stopAfter,setStopAfter]=useState(true);
  const [values,setValues]=useState<ValuesDraft>({}),[extraTemplates,setExtraTemplates]=useState<Template[]>([]),[copy,setCopy]=useState<{kind:'client'|'server';source:CopyTemplate}|null>(null);
  const [saveSuite,setSaveSuite]=useState(false),[suiteName,setSuiteName]=useState(''),[suiteVersion,setSuiteVersion]=useState('1'),[suiteDefaults,setSuiteDefaults]=useState<Record<string,unknown>>({});
  const allTemplates=[...templates,...extraTemplates.filter(t=>!templates.some(x=>x.id===t.id))];
  useEffect(()=>{let active=true;api<ServerTemplate[]>('runtime-templates/server').then(rows=>{if(active)setServers(rows)}).catch(e=>{if(active)setError(String(e))});return()=>{active=false}},[]);
  const [addresses,setAddresses]=useState<{url:string;mode:'exclusive'|'shared'}[]>([{url:'',mode:'shared'}]);
  const addressDirty=useRef(false);
  const [cases,setCases]=useState<Case[]>([{case_id:'case-001',task_name:'demo',episodes:3,seed:0,parameters:{}}]);
  const [step,setStep]=useState(0),[preview,setPreview]=useState<Preview|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [submittedId,setSubmittedId]=useState('');
  const [trialCase,setTrialCase]=useState(''),[trial,setTrial]=useState<Trial|null>(null),[trialError,setTrialError]=useState(''),[submitted,setSubmitted]=useState('');
  const sequence=useRef(0),pending=useRef(false),keys=useRef<Record<string,{body:string;value:string}>>({});
  const selectedSuite=suites.find(s=>s.id===suiteId),template=allTemplates.find(t=>t.id===templateId);
  useEffect(()=>{if(!addressDirty.current)setAddresses(a=>a.map(e=>({...e,mode:template?.endpoint_exclusive?'exclusive':'shared'})));},[template?.id,template?.endpoint_exclusive]);
  useEffect(()=>{if(launch){setSaveSuite(false);setSuiteDefaults(launch.suite.client_defaults||{});setValues({});setSuiteId(launch.suite.id);setTemplateId(launch.suite.template_id);setCases(launch.suite.cases);setStep(0);}},[launch]);
  // Invalidating in an effect also covers edits by CaseEditor and suite handoff.
  useEffect(()=>{sequence.current++;setPreview(null);setError('');setSubmitted('');},[resourceDrafts,draft,addresses,cases,suiteId,templateId,serviceMode,serviceRows,stopAfter,values,saveSuite,suiteName,suiteVersion,suiteDefaults]);
  useEffect(()=>{
    if(!trial?.id)return;
    let active=true;const id=trial.id;
    const poll=async()=>{try{const b=await api<Trial>('batches/'+id);if(active){setTrial(b);setTrialError('');}}catch(e){if(active)setTrialError(String(e));}};
    void poll();const timer=setInterval(poll,2000);return()=>{active=false;clearInterval(timer);};
  },[trial?.id]);
  const update=(key:keyof typeof initial,value:string|number)=>setDraft(d=>({...d,[key]:value}));
  function chooseSuite(id:string){setSuiteId(id);const s=suites.find(x=>x.id===id);if(s){setSaveSuite(false);setSuiteDefaults(s.client_defaults||{});if(s.template_id!==templateId)setValues({});setCases(s.cases);setTemplateId(s.template_id);}}
  function validateAddresses(){
    const keys=new Set<string>();
    for(const [i,e] of addresses.entries()){
      let parsed:URL;
      try{parsed=new URL(e.url);}catch{throw Error(`第 ${i+1} 个推理服务地址无效`);}
      if(!template?.protocols.includes(parsed.protocol.slice(0,-1)))throw Error(`第 ${i+1} 个推理服务地址协议不符合客户端要求`);
      const key=parsed.hostname.toLowerCase().replace(/\.$/,'')+':'+(parsed.port||(['https:','wss:'].includes(parsed.protocol)?'443':'80'));
      if(keys.has(key))throw Error(`第 ${i+1} 个推理服务地址主机和端口重复`);
      keys.add(key);
    }
  }
  function editAddress(index:number,change:Partial<{url:string;mode:'exclusive'|'shared'}>){addressDirty.current=true;setAddresses(old=>old.map((e,i)=>i===index?{...e,...change}:e));}
  function body(){
    const environment=JSON.parse(draft.environment);
    if(!environment||Array.isArray(environment)||typeof environment!=='object'||Object.values(environment).some(v=>typeof v!=='string'))throw Error('启动变量覆盖 JSON 必须是字符串键值对象');
    if(!draft.title.trim())throw Error('请填写批次名称');
    if(!templateId||!template)throw Error('请选择可用的客户端模板');
    if(!draft.pool_id)throw Error('请选择客户端资源池');
    if(serviceMode==='external')validateAddresses();
    if(serviceMode==='none'&&template.task_type!=='command')throw Error('无服务模式需要普通命令模板');
    if(saveSuite&&(!suiteName.trim()||!suiteVersion.trim()))throw Error('请填写新集合名称和版本');
    return {resources:template.resource_requirements?(resourceDrafts[templateId]||{}):{...(draft.cpus?{cpus:Number(draft.cpus)}:{}),...(draft.ram_gb?{ram_gb:Number(draft.ram_gb)}:{})},title:draft.title,pool_id:draft.pool_id,...(template.resource_requirements?{}:{gpu_mode:draft.gpu_mode,requested_vram_gb:draft.requested_vram_gb,required_gpu_model:draft.required_gpu_model.trim()||null}),max_concurrent:draft.max_concurrent,suite_id:suiteId||null,cases:suiteId||saveSuite?[]:parseCases(cases),
      new_suite:saveSuite?{name:suiteName,version:suiteVersion,template_id:templateId,cases:parseCases(cases),client_defaults:suiteDefaults}:null,services:serviceMode==='managed'?servicePayload(serviceRows,servers,draft.pool_id):[],stop_after_batch:stopAfter,
      client:{template_id:templateId,service_mode:serviceMode,endpoints:serviceMode==='external'?addresses:undefined,parameters:{...(suiteId||saveSuite?{}:suiteDefaults),...parseValues(template.parameter_schema,values)},environment,request_timeout_seconds:draft.request_timeout_seconds,timeout_seconds:draft.timeout_seconds}};
  }
  async function inspect(){
    if(pending.current)return;
    pending.current=true;setBusy(true);setError('');setPreview(null);const seq=++sequence.current;
    try{const result=await api<Preview>('orchestrations/preflight','POST',body());if(seq===sequence.current)setPreview(result);}
    catch(e){if(seq===sequence.current)setError(String(e));}finally{pending.current=false;setBusy(false);}
  }
  async function submit(isTrial=false){
    if(pending.current||!preview)return;
    pending.current=true;setBusy(true);setError('');
    const seq=sequence.current;
    try{
      const full=body();
      const selected=preview.cases.find(c=>c.case_id===trialCase)||preview.cases[0];
      const payload=isTrial?{...full,title:('[试跑] '+(selectedSuite?selectedSuite.name+'/'+selectedSuite.version+' ':'')+draft.title).slice(0,200),suite_id:null,new_suite:null,max_concurrent:1,cases:[{...selected,episodes:1}]}:full;
      const kind=isTrial?'trial':'full',serialized=JSON.stringify(payload);
      if(keys.current[kind]?.body!==serialized)keys.current[kind]={body:serialized,value:crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random()};
      // The POST revalidates under the existing transaction; inspection reserves nothing.
      const b=await api<Trial>('orchestrations','POST',payload,{'Idempotency-Key':keys.current[kind].value});
      if(isTrial){setTrial(b);setTrialError('');}else if(seq===sequence.current){setSubmitted('已提交：'+b.title+'。可在下方批次进度查看。');setSubmittedId(b.id);}
      onSubmitted();
    }catch(e){setError(String(e));}finally{pending.current=false;setBusy(false);}
  }
  function next(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();setError('');
    try{
      if(step===0)parseCases(cases);
      if(step===1){
        if(!template)throw Error('请选择可用的客户端模板');
        parseValues(template.parameter_schema,values);
        const env=JSON.parse(draft.environment);if(!env||Array.isArray(env)||typeof env!=='object'||Object.values(env).some(v=>typeof v!=='string'))throw Error('启动变量覆盖 JSON 必须是字符串键值对象');
        if(Object.keys(env).some(k=>!template.runtime?.overridable_env.includes(k)))throw Error('启动变量包含模板未允许覆盖的键');
      }
      if(step===2){if(serviceMode==='external')validateAddresses();if(serviceMode==='managed')servicePayload(serviceRows,servers,draft.pool_id);if(serviceMode==='none'&&template?.task_type!=='command')throw Error('请选择普通命令模板');}
      setStep(s=>Math.min(4,s+1));if(step===3)void inspect();
    }catch(e){setError(String(e));}
  }
  const steps=['选择集合','客户端','服务端','资源配置','预检确认'];
  return <section className="panel run-wizard" aria-label="集合运行向导"><h2>发起集合运行</h2>
    <ol className="wizard-steps" aria-label="运行步骤">{steps.map((name,i)=><li key={name} aria-current={i===step?'step':undefined}>{i+1}. {name}</li>)}</ol>
    <p className="hint">选择业务配置与并发偏好，平台负责分配机器和传递参数；实际通信和执行可通过试跑验证。</p>
    {error&&<p role="alert" className="error">{error}</p>}
    <form onSubmit={next}><fieldset disabled={busy}>
    {step===0&&<>
      <div className="submit-grid"><Field label="选择已保存集合"><select value={suiteId} onChange={e=>chooseSuite(e.target.value)}><option value="">自定义清单</option>{suites.map(s=><option key={s.id} value={s.id}>{s.name} / {s.version}</option>)}</select></Field>
      <Field label="批次名称"><input value={draft.title} onChange={e=>update('title',e.target.value)} required maxLength={200}/></Field></div>
      {!suiteId&&<><label><input type="checkbox" checked={saveSuite} onChange={e=>setSaveSuite(e.target.checked)}/>同时保存为可复用集合</label>{saveSuite&&<div className="row"><Field label="新集合名称"><input required value={suiteName} onChange={e=>setSuiteName(e.target.value)}/></Field><Field label="集合版本"><input required value={suiteVersion} onChange={e=>setSuiteVersion(e.target.value)}/></Field></div>}</>}
      {suiteId&&<button type="button" onClick={()=>{setSuiteDefaults(selectedSuite?.client_defaults||{});setSuiteName(selectedSuite?.name||'');setSuiteVersion('');setSuiteId('');setSaveSuite(true)}}>复制集合并修改</button>}
      {suiteId&&<p>使用已发布集合及关联客户端。临时修改请显式切换“自定义清单”。</p>}
      <CaseEditor cases={cases} setCases={setCases} setError={setError} disabled={!!suiteId}/>
    </>}
    {step===1&&<><div className="submit-grid">
      <Field label="客户端模板"><select required value={templateId} disabled={!!suiteId} onChange={e=>{setTemplateId(e.target.value);setValues({})}}><option value="" disabled>选择客户端及版本</option>{allTemplates.map(t=><option key={t.id} value={t.id}>{t.name} / {t.version}</option>)}</select></Field>
      <Field label="请求超时秒"><input type="number" required min={1} max={120} value={draft.request_timeout_seconds} onChange={e=>update('request_timeout_seconds',Number(e.target.value))}/></Field>
      <Field label="每项总超时秒"><input type="number" required min={1} max={3600} value={draft.timeout_seconds} onChange={e=>update('timeout_seconds',Number(e.target.value))}/></Field></div>
      {template&&<><button type="button" onClick={()=>setCopy({kind:'client',source:template})}>复制修改客户端模板</button><BusinessValues label="客户端业务参数" schema={template.parameter_schema} value={values} onChange={setValues}/></>}
      <Field label="启动变量覆盖 JSON"><textarea rows={3} value={draft.environment} onChange={e=>update('environment',e.target.value)}/></Field>
      <p className="hint">仅可覆盖模板允许的变量：{template?.runtime?.overridable_env?.join(', ')||'无'}。请勿填写密码或令牌。</p>
    </>}
    {step===2&&<><Field label="服务来源"><select value={serviceMode} onChange={e=>setServiceMode(e.target.value as typeof serviceMode)}><option value="external">提供外部服务地址</option><option value="managed">平台启动服务端模板</option><option value="none">无需服务（普通批处理）</option></select></Field>
      {serviceMode==='external'&&<>      <div aria-label="候选地址列表">{addresses.map((address,i)=><div key={i}>
        <Field label={i===0?'推理服务地址':`推理服务地址 ${i+1}`}><input required value={address.url} maxLength={2000} placeholder="ws://推理机器:端口" onChange={e=>editAddress(i,{url:e.target.value})}/></Field>
        <Field label={`服务地址 ${i+1} 使用模式`}><select value={address.mode} onChange={e=>editAddress(i,{mode:e.target.value as 'exclusive'|'shared'})}><option value="exclusive">独占</option><option value="shared">共享</option></select></Field>
        <button type="button" disabled={addresses.length===1} onClick={()=>{addressDirty.current=true;setAddresses(old=>old.filter((_,n)=>n!==i));}}>移除地址 {i+1}</button>
      </div>)}<button type="button" disabled={addresses.length>=100} onClick={()=>{addressDirty.current=true;setAddresses(old=>[...old,{url:'',mode:template?.endpoint_exclusive?'exclusive':'shared'}]);}}>添加推理地址</button>
      <p className="hint">独占地址同时只分配一个任务；共享需要服务端支持多会话。平台自动分配，资源或地址不足时排队。</p></div>
</>}
      {serviceMode==='managed'&&<><ManagedServices value={serviceRows} onChange={setServiceRows} templates={servers} pools={pools} onCopy={t=>setCopy({kind:'server',source:t})}/><label><input type="checkbox" checked={stopAfter} onChange={e=>setStopAfter(e.target.checked)}/>批次结束且客户端清理完成后停止服务</label></>}
      {serviceMode==='none'&&<p>普通命令按退出码判断执行结果，无需提供服务地址。</p>}
    </>}
    {step===3&&<div className="submit-grid">
      <Field label="客户端资源池"><select required value={draft.pool_id} onChange={e=>update('pool_id',e.target.value)}><option value="" disabled>选择资源池</option>{pools.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
      <Field label="最大并发任务数"><input type="number" required min={1} max={100} value={draft.max_concurrent} onChange={e=>update('max_concurrent',Number(e.target.value))}/></Field>
      {template?.resource_requirements?<ResourceRequest contract={template.resource_requirements} value={resourceDrafts[templateId]||{}} onChange={v=>{setResourceDrafts({...resourceDrafts,[templateId]:v});setPreview(null)}} label="客户端资源"/>:<>      <Field label="客户端 GPU 模式"><select value={draft.gpu_mode} onChange={e=>update('gpu_mode',e.target.value)}><option value="exclusive">独占</option><option value="shared">共享</option></select></Field>
      <Field label="每项所需显存 GiB"><input type="number" required min={0} max={10000} value={draft.requested_vram_gb} onChange={e=>update('requested_vram_gb',Number(e.target.value))}/></Field>
      <details><summary>高级资源约束</summary><Field label="每项 CPU 核数（可选）"><input type="number" min={1} step={1} value={draft.cpus} placeholder="使用资源池默认值" onChange={e=>update('cpus',e.target.value)}/></Field>
      <Field label="每项内存 GiB（可选）"><input type="number" min={1} step={1} value={draft.ram_gb} placeholder="使用资源池默认值" onChange={e=>update('ram_gb',e.target.value)}/></Field>
      <Field label="指定 GPU 型号（可选）"><input maxLength={100} value={draft.required_gpu_model} onChange={e=>update('required_gpu_model',e.target.value)}/></Field></details></>}
    </div>}
    {step===4&&<>
      <h3>确认运行配置</h3><dl className="run-summary">
        <dt>集合</dt><dd>{selectedSuite?selectedSuite.name+' / '+selectedSuite.version:'自定义清单'}</dd>
        <dt>任务与回合</dt><dd>{cases.length} 项任务，{template?.task_type==='command'?0:cases.reduce((sum,c)=>sum+c.episodes,0)} 个回合</dd>
        <dt>客户端</dt><dd>{template?.name} / {template?.version}</dd><dt>推理服务</dt><dd>{serviceMode==='managed'?serviceRows.map(s=><p key={s.key}>{s.key} · {servers.find(t=>t.id===s.template_id)?.name} · {s.mode==='exclusive'?'独占':'共享'} · 自动地址</p>):serviceMode==='none'?'无需服务':addresses.map((e,i)=><p key={i}>{e.url} · {e.mode==='exclusive'?'独占':'共享'}</p>)}</dd>
        <dt>资源</dt><dd>{pools.find(p=>p.id===draft.pool_id)?.name} · {template?.resource_requirements?'按模板及本次覆盖申请':(draft.gpu_mode==='shared'?'共享':'独占')+' · 每项 '+draft.requested_vram_gb+' GiB'} · 最大并发 {draft.max_concurrent} · {draft.required_gpu_model||'不限定 GPU 型号'}</dd>
        <dt>超时</dt><dd>请求 {draft.request_timeout_seconds} 秒 / 每项 {draft.timeout_seconds} 秒</dd>
        <dt>启动变量</dt><dd><code>{draft.environment}</code></dd>
      </dl>
      <button type="button" onClick={()=>void inspect()}>重新检查</button>
      {!preview&&<p role="status">{busy?'正在检查配置与资源…':'尚无有效检查结果，请重新检查。'}</p>}
      {preview&&<div className="preflight-result" aria-label="预检结果">
        <details><summary>有效资源申请与来源</summary><p>以下为后端按模板、提交及集合单项解析的结果。</p><pre>{JSON.stringify({客户端:preview.case_checks?.map(c=>({任务:c.case_id,申请:c.resources,来源:c.resource_sources})),服务端:preview.service_plan?.map(s=>({服务:s.key,申请:s.resources,来源:s.resource_sources}))},null,2)}</pre></details>
        <h3>{preview.status==='ready'?'当前有节点可接纳单项任务':preview.status==='waiting_resources'?'需要等待资源':'需要准备运行环境'}</h3>
        <p className="hint">检查时间：{new Date(preview.checked_at).toLocaleTimeString()}。{preview.note} 最大并发为上限，不保证同时启动。</p>
        <details><summary>有效业务参数与来源</summary>{preview.effective_parameters?.map(p=><div key={p.case_id}><h4>{p.case_id}</h4><pre>{JSON.stringify(p,null,2)}</pre></div>)}</details>{preview.pair_checks?.map(p=><p key={p.case_id}>{p.case_id}：{p.pair_ready?'两端资源当前可接纳':'等待两端资源或服务环境'}</p>)}<Reasons reasons={preview.reasons}/><ul>{preview.endpoints?.map(e=><li key={e.url}>{e.url} · {e.mode==='exclusive'?'独占':'共享'} · {e.available?'当前可分配':'等待互斥占用释放'} · 当前占用 {e.occupancy}</li>)}</ul>
        <div className="table-scroll"><table><thead><tr><th>节点 / GPU</th><th>当前状态与操作建议</th></tr></thead><tbody>{preview.nodes.map(n=><tr key={n.slot_id}><td>{n.node_name} / GPU {n.gpu_index}<small>{n.gpu_model}</small></td><td>{n.ready?'当前可接纳单项任务':<><Reasons reasons={n.reasons}/>{!n.reasons.length&&'等待上方平台或用户配额释放'}</>}</td></tr>)}</tbody></table></div>
        <p>环境缺失和资源占用不会阻止合法任务提交排队；环境问题需要管理员处理。</p>
        <button type="button" className="primary" disabled={!preview.can_submit||!!submitted} onClick={()=>void submit()}>{preview.status==='ready'?'提交评测批次':'提交排队'}</button>
        <details><summary>可选：先试跑一个任务、一个回合</summary>
          <Field label="试跑任务"><select value={trialCase||preview.cases[0]?.case_id} onChange={e=>setTrialCase(e.target.value)}>{preview.cases.map(c=><option key={c.case_id} value={c.case_id}>{c.case_id} / {c.task_name}</option>)}</select></Field>
          <p className="hint">试跑独立排队，不修改集合和正式运行配置，不自动开始正式运行。</p>
          <button type="button" disabled={!preview.can_submit} onClick={()=>void submit(true)}>提交单回合试跑</button>
        </details>
      </div>}
    </>}
    <div className="wizard-actions">{step>0&&<button type="button" onClick={()=>{setStep(s=>s-1);setError('');}}>上一步</button>}{step<4&&<button type="submit" className="primary">{step===3?'检查并确认':'下一步'}</button>}</div>
    </fieldset></form>
    {copy&&<TemplateCopy key={copy.source.id} {...copy} onClose={()=>setCopy(null)} onPublished={t=>{if(copy.kind==='client'){setExtraTemplates(old=>[...old,t as Template]);setTemplateId(t.id);if(selectedSuite)setSuiteDefaults(selectedSuite.client_defaults||{});setSuiteId('');setValues({})}else{setServers(old=>[...old,t as ServerTemplate]);setServiceRows(old=>old.map(s=>s.template_id===copy.source.id?{...s,template_id:t.id}:s))}setCopy(null)}}/>}
    {submitted&&<p role="status">{submitted}{onBatch&&<button onClick={()=>onBatch(submittedId)}>查看已提交批次</button>}</p>}
    {trial&&<section aria-label="试跑进度"><button onClick={()=>onBatch?.(trial.id)}>查看试跑批次</button><h3>{trial.title} · {statuses[trial.status]||trial.status}</h3>
      {trialError&&<p role="alert">试跑状态更新失败：{trialError}，将自动重试。</p>}
      {trial.cases.map(c=><div key={c.job_id}><p>{statuses[c.status]||c.status} · {c.episode_reporting?`已完成 ${c.progress.completed}/1 回合，目标成功 ${c.progress.successes}`:'客户端未声明回合上报，无法验证回合报告'}</p>{c.error&&<p>{c.error}</p>}<button onClick={()=>onJob(c.job_id)}>试跑详情 / 取消</button></div>)}
      <p className="hint">试跑只代表所选任务的一个回合，不代表完整集合评测结果。</p>
    </section>}
  </section>;
}
function Reasons({reasons}:{reasons:Reason[]}){return reasons.length?<ul>{reasons.map((r,i)=><li key={r.code+i}>{r.message}；{r.action}</li>)}</ul>:null;}
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
    ids.add(c.case_id); return {...c,case_id:c.case_id,task_name:c.task_name,episodes:c.episodes,seed,parameters:c.parameters||{}};
  });
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
