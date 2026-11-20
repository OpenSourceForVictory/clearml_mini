import {WizardModule,EnvironmentEditor,RuntimeSummary,ValueTable,JsonDraft} from './wizard-fields';
import type {WizardLaunch} from './batch-rerun';
import {requestId} from './request-id';
import React, {useEffect, useRef, useState} from 'react';
import {ResourceContract,ResourceRequest} from './resource-requirements';
import {api} from './api';
import {BusinessValues,ParameterSchema,ValuesDraft,parseValues} from './business-values';
import {ManagedServices,ServerTemplate,newService,servicePayload} from './managed-services';
import {TemplateCopy,CopyTemplate} from './template-copy';

export type Template = {resource_requirements?:ResourceContract|null;task_type?:string;execution_mode?:string;integration_mode?:string;host_runtime?:{episode_launch?:string;episode_error_policy?:string;overridable_env?:string[]}|null;parameter_schema?:ParameterSchema|null;id:string;name:string;version:string;image:string;command:string[];protocols:string[];enabled:boolean;episode_reporting?:boolean;endpoint_exclusive?:boolean;runtime?:{overridable_env:string[]}|null};
export type Case = {case_id:string;task_name:string;episodes:number;seed:number;parameters?:Record<string,unknown>;service_keys?:string[];resources?:Record<string,unknown>};
export type Suite = {id:string;name:string;version:string;template_id:string;template_ids?:string[];cases:Case[];client_defaults?:Record<string,unknown>};
type Reason = {code:string;category:string;message:string;action:string};
type Candidate = {slot_id:string;node_name:string;gpu_index:number;gpu_model:string;ready:boolean;reasons:Reason[]};
type Preview = {case_checks?:{case_id:string;resources:Record<string,unknown>;resource_sources:Record<string,string>}[];service_plan?:{key:string;resources:Record<string,unknown>;resource_sources:Record<string,string>}[];server_checks?:{key:string;pool_name:string;status:string;reasons:Reason[];nodes:Candidate[]}[];effective_parameters?:{case_id:string;parameters:Record<string,unknown>;sources:Record<string,string>}[];pair_checks?:{case_id:string;pair_ready:boolean}[];endpoints?:{url:string;mode:string;available:boolean;occupancy:number}[];checked_at:string;can_submit:boolean;status:string;task_count:number;total_episodes:number;cases:Case[];note:string;reasons:Reason[];nodes:Candidate[]};
type Trial = {id:string;title:string;status:string;cases:{task_type?:string;job_id:string;status:string;episode_reporting:boolean;progress:{completed:number;successes:number};error:string|null}[]};
const statuses:Record<string,string>={PENDING:'排队中',RESERVED:'已分配',STARTING:'正在启动',RUNNING:'运行中',SUCCEEDED:'执行完成',FAILED:'执行失败',TIMED_OUT:'超时',CANCELLED:'已取消',CANCEL_REQUESTED:'取消中'};
function Field({label,children}:{label:string;children:React.ReactNode}) {
  const id=React.useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{React.cloneElement(children as React.ReactElement<{id:string}>,{id})}</div>;
}
const initial={unlimited_timeout:false,cpu_unlimited:false,memory_unlimited:false,cpus:'',ram_gb:'',title:'',pool_id:'',gpu_mode:'exclusive',requested_vram_gb:0,required_gpu_model:'',max_concurrent:1,request_timeout_seconds:10,timeout_seconds:300,environment:'{}'};
export function RunWizard({templates,pools,suites,launch,onSubmitted,onJob,onBatch}:{templates:Template[];pools:{id:string;name:string}[];suites:Suite[];launch:WizardLaunch|null;onSubmitted:()=>void;onBatch?:(id:string)=>void;onJob:(id:string)=>void}) {
  const [resourceDrafts,setResourceDrafts]=useState<Record<string,Record<string,any>>>({});
  const [draft,setDraft]=useState(initial),[suiteId,setSuiteId]=useState(''),[templateId,setTemplateId]=useState('');
  const [serviceMode,setServiceMode]=useState<'external'|'managed'|'none'>('external'),[servers,setServers]=useState<ServerTemplate[]>([]),[serviceRows,setServiceRows]=useState([newService()]),[stopAfter,setStopAfter]=useState(true);
  const [executionLimit,setExecutionLimit]=useState<number|null>(null),[limitError,setLimitError]=useState(''),[limitReload,setLimitReload]=useState(0);
  const timeoutEdited=useRef(!!launch?.rerun);
  const [importAcknowledged,setImportAcknowledged]=useState(false);
  const imported=launch?.rerun;
  const [historicalSuite,setHistoricalSuite]=useState(false);
  const historyOption=imported?.source.suite_id||'';
  const sourceSuite=suites.find(s=>s.id===imported?.source.suite_id);
  const historyLabel=sourceSuite?`${sourceSuite.name}（历史运行快照）`:`原集合 ${imported?.source.suite_id}（历史运行快照，当前不可用）`;
  useEffect(()=>{
    let active=true;setExecutionLimit(null);setLimitError('');
    api<{client_execution_max_seconds:number;client_execution_default_seconds:number}>('execution-limits').then(data=>{
      if(!active)return;
      if(!Number.isSafeInteger(data.client_execution_max_seconds)||data.client_execution_max_seconds<1||!Number.isSafeInteger(data.client_execution_default_seconds)||data.client_execution_default_seconds<1||data.client_execution_default_seconds>data.client_execution_max_seconds)throw Error('平台执行时限响应无效');
      setExecutionLimit(data.client_execution_max_seconds);
      if(!timeoutEdited.current)setDraft(old=>({...old,timeout_seconds:data.client_execution_default_seconds}));
    }).catch(e=>{if(active)setLimitError(String(e))});
    return()=>{active=false;};
  },[limitReload]);
  function validateTimeout(){
    if(executionLimit===null)throw Error('请先读取平台执行时限上限');
    if(draft.unlimited_timeout)return;
    if(!Number.isInteger(draft.timeout_seconds)||draft.timeout_seconds<1||draft.timeout_seconds>executionLimit)throw Error(`每项总执行时限须为 1–${executionLimit} 秒的整数`);
  }
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
  const selectedSuite=suites.find(s=>s.id===suiteId)||(launch?.suite?.id===suiteId?launch.suite:undefined),template=allTemplates.find(t=>t.id===templateId);
  const ordinaryOnce=template?.task_type==='command'&&!(template.execution_mode==='host'&&template.host_runtime?.episode_launch==='per_episode');
  useEffect(()=>{if(imported&&template?.id===imported.submission.client.template_id)return;if(template)setServiceMode(old=>template.task_type==='command'?'none':old==='none'?'external':old);},[template?.id,template?.task_type]);
  useEffect(()=>{if(!addressDirty.current)setAddresses(a=>a.map(e=>({...e,mode:template?.endpoint_exclusive?'exclusive':'shared'})));},[template?.id,template?.endpoint_exclusive]);
  function availableTemplate(s:Suite){return (s.template_ids||[s.template_id]).find(id=>allTemplates.some(t=>t.id===id))||s.template_id;}
  useEffect(()=>{if(launch?.suite){setHistoricalSuite(false);setSaveSuite(false);setSuiteDefaults(launch.suite.client_defaults||{});setValues({});setSuiteId(launch.suite.id);setTemplateId(availableTemplate(launch.suite));setCases(launch.suite.cases);setStep(0);}},[launch]);
  useEffect(()=>{
    if(!launch?.rerun)return;
    const input=launch.rerun.submission,c=input.client;
    timeoutEdited.current=true;addressDirty.current=true;sequence.current++;keys.current={};
    setHistoricalSuite(!!launch.rerun.source.suite_id);setImportAcknowledged(false);setTrial(null);setTrialError('');setTrialCase('');setSubmitted('');setSubmittedId('');setPreview(null);setError('');setStep(0);
    setSaveSuite(false);setSuiteId('');setSuiteDefaults({});setValues({});setTemplateId(c.template_id);setCases(input.cases);
    setDraft({...initial,title:input.title,pool_id:input.pool_id,max_concurrent:input.max_concurrent,
      environment:JSON.stringify(c.environment||{},null,2),request_timeout_seconds:c.request_timeout_seconds,
      timeout_seconds:c.timeout_seconds,unlimited_timeout:c.timeout_seconds===0});
    setResourceDrafts({[c.template_id]:input.resources});setServiceMode(c.service_mode);
    setAddresses(c.endpoints||[{url:'',mode:'shared'}]);setStopAfter(input.stop_after_batch);
    setServiceRows(input.services.length?input.services.map((v,i)=>({...newService(i),...v,
      instance_count:v.instance_count??0,parameters:{},imported_parameters:v.parameters,
      environment:JSON.stringify(v.environment||{},null,2),resource_overrides:v.resources,
      cpus:v.resources?.cpus==null?'':String(v.resources.cpus),ram_gb:v.resources?.ram_gb==null?'':String(v.resources.ram_gb),
      cpu_unlimited:v.resources?.cpu_unlimited,memory_unlimited:v.resources?.memory_unlimited,
      gpu_mode:v.resources?.gpu_mode||'exclusive',requested_vram_gb:v.resources?.requested_vram_gb??0})): [newService()]);
  },[launch]);
  // Invalidating in an effect also covers edits by CaseEditor and suite handoff.
  useEffect(()=>{sequence.current++;setPreview(null);setError('');setSubmitted('');},[resourceDrafts,draft,addresses,cases,suiteId,templateId,serviceMode,serviceRows,stopAfter,values,saveSuite,suiteName,suiteVersion,suiteDefaults]);
  useEffect(()=>{
    if(!trial?.id)return;
    let active=true;const id=trial.id;
    const poll=async()=>{try{const b=await api<Trial>('batches/'+id);if(active){setTrial(b);setTrialError('');}}catch(e){if(active)setTrialError(String(e));}};
    void poll();const timer=setInterval(poll,2000);return()=>{active=false;clearInterval(timer);};
  },[trial?.id]);
  const update=(key:keyof typeof initial,value:string|number|boolean)=>setDraft(d=>({...d,[key]:value}));
  function chooseSuite(id:string,current=false){if(id&&id===historyOption&&imported&&!current){setHistoricalSuite(true);setSuiteId('');setSaveSuite(false);setSuiteDefaults({});setValues({});setCases(imported.submission.cases);setTemplateId(imported.submission.client.template_id);return;}setHistoricalSuite(false);setSuiteId(id);const s=suites.find(x=>x.id===id);if(s){setSaveSuite(false);setSuiteDefaults(s.client_defaults||{});if(s.template_id!==templateId)setValues({});setCases(s.cases);setTemplateId(availableTemplate(s));}}
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
    if(imported?.issues.some(i=>i.fatal))throw Error('历史配置存在无法自动恢复的问题，不能提交：'+imported.issues.filter(i=>i.fatal).map(i=>`${i.path}：${i.message}`).join('；'));
    if(imported?.issues.length&&!importAcknowledged)throw Error('请先核对并确认历史配置问题');
    validateTimeout();
    const environment=JSON.parse(draft.environment);
    if(!environment||Array.isArray(environment)||typeof environment!=='object'||Object.values(environment).some(v=>typeof v!=='string'))throw Error('启动变量覆盖 JSON 必须是字符串键值对象');
    if(!draft.title.trim())throw Error('请填写批次名称');
    if(!templateId||!template)throw Error('请选择可用的客户端模板');
    if(!draft.pool_id)throw Error('请选择客户端资源池');
    if(serviceMode==='external')validateAddresses();
    if(serviceMode==='none'&&template.task_type!=='command')throw Error('无服务模式需要普通命令模板');
    if(saveSuite&&(!suiteName.trim()||!suiteVersion.trim()))throw Error('请填写新集合名称和版本');
    const inputCases=parseCases(ordinaryOnce&&!imported?cases.map(c=>({...c,episodes:1})):cases);
    return {resources:template.resource_requirements?(resourceDrafts[templateId]||{}):{...(draft.cpu_unlimited?{cpu_unlimited:true}:{}),...(draft.memory_unlimited?{memory_unlimited:true}:{}),...(draft.cpus?{cpus:Number(draft.cpus)}:{}),...(draft.ram_gb?{ram_gb:Number(draft.ram_gb)}:{})},title:draft.title,pool_id:draft.pool_id,...(template.resource_requirements?{}:{gpu_mode:draft.gpu_mode,requested_vram_gb:draft.requested_vram_gb,required_gpu_model:draft.required_gpu_model.trim()||null}),max_concurrent:draft.max_concurrent,suite_id:suiteId||null,cases:suiteId||saveSuite?[]:inputCases,
      new_suite:saveSuite?{name:suiteName,version:suiteVersion,template_id:templateId,cases:inputCases,client_defaults:suiteDefaults}:null,services:serviceMode==='managed'?servicePayload(serviceRows,servers,draft.pool_id):[],stop_after_batch:stopAfter,
      client:{template_id:templateId,service_mode:serviceMode,endpoints:serviceMode==='external'?addresses:undefined,parameters:{...(suiteId||saveSuite?{}:suiteDefaults),...parseValues(template.parameter_schema,values)},environment,request_timeout_seconds:draft.request_timeout_seconds,timeout_seconds:draft.unlimited_timeout?0:draft.timeout_seconds}};
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
      if(keys.current[kind]?.body!==serialized)keys.current[kind]={body:serialized,value:requestId()};
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
        validateTimeout();
        parseValues(template.parameter_schema,values);
        const env=JSON.parse(draft.environment);if(!env||Array.isArray(env)||typeof env!=='object'||Object.values(env).some(v=>typeof v!=='string'))throw Error('启动变量覆盖 JSON 必须是字符串键值对象');
        if(Object.keys(env).some(k=>!(template.runtime||template.host_runtime)?.overridable_env?.includes(k)))throw Error('启动变量包含模板未允许覆盖的键');
      }
      if(step===2){if(serviceMode==='external')validateAddresses();if(serviceMode==='managed')servicePayload(serviceRows,servers,draft.pool_id);if(serviceMode==='none'&&template?.task_type!=='command')throw Error('请选择普通命令模板');}
      setStep(s=>Math.min(4,s+1));if(step===3)void inspect();
    }catch(e){setError(String(e));}
  }
  function goBack(target:number){setStep(target);setError('');}
  const steps=['选择集合','客户端','服务端','资源配置','预检确认'];
  const stepTitles=['任务清单','客户端运行配置','服务端运行配置','资源与并发','检查与提交'];
  function revealInvalid(event:React.FormEvent<HTMLFormElement>){const el=event.target as HTMLElement;let parent=el.parentElement;while(parent){if(parent instanceof HTMLDetailsElement)parent.open=true;parent=parent.parentElement;}setError('请检查标出的字段；已展开对应配置。');requestAnimationFrame(()=>{el.focus();el.scrollIntoView({block:'center'});});}

  return <section className="panel run-wizard" aria-label="集合运行向导"><h2>发起集合运行</h2>
    <ol className="wizard-steps" aria-label="运行步骤">{steps.map((name,i)=><li key={name} aria-current={i===step?'step':undefined}><button type="button" disabled={busy||i>=step} onClick={()=>goBack(i)} aria-label={`返回${name}`}>{i+1}. {name}</button></li>)}</ol>
    <p className="hint">选择业务配置与并发偏好，平台负责分配机器和传递参数；实际通信和执行可通过试跑验证。</p>
    {error&&<p role="alert" className="error">{error}</p>}
    {imported&&<section aria-label="历史批次来源"><h3>基于历史批次创建</h3><p>来源：{imported.source.title} · {imported.source.batch_id}。只创建新批次，不改变原批次。原客户端：{imported.templates.client.name} / {imported.templates.client.version}。</p><ul>{imported.templates.services.map((t,i)=><li key={i}>原服务 {i+1}：{t.name} / {t.version} · {t.id}</li>)}</ul>
    <p>已恢复各任务的最终参数和资源，单项配置优先于公共配置；请在清单中逐项修改。重新预检后才能提交，不复用原实例或预检结果。</p>
    {imported.issues.length>0&&<><ul>{imported.issues.map((i,n)=><li key={n}>{i.path}：{i.message}{i.fatal?'（无法恢复，禁止提交）':''}</li>)}</ul>
    {!imported.issues.some(i=>i.fatal)&&<label><input type="checkbox" checked={importAcknowledged} onChange={e=>setImportAcknowledged(e.target.checked)}/>我已重新选择或明确确认使用当前模板、资源池，并补齐缺失配置；接受与历史配置的差异，重新预检</label>}</>}
    </section>}
    <p className="wizard-context">{draft.title||'未命名批次'} · {cases.length} 项任务 · {stepTitles[step]}</p><form onSubmit={next} onInvalidCapture={revealInvalid}><fieldset disabled={busy} className="wizard-form">
    {step===0&&<WizardModule title="任务与集合">
      <div className="submit-grid"><Field label="选择已保存集合"><select value={historicalSuite?historyOption:suiteId} onChange={e=>chooseSuite(e.target.value)}>{historicalSuite&&imported?.source.suite_id&&<option value={historyOption}>{historyLabel}</option>}<option value="">自定义清单</option>{suites.filter(s=>!historicalSuite||s.id!==historyOption).map(s=><option key={s.id} value={s.id}>{s.name} / {s.version}</option>)}</select></Field>
      <Field label="批次名称"><input value={draft.title} onChange={e=>update('title',e.target.value)} required maxLength={200}/></Field></div>
      {historicalSuite&&<p>当前使用原集合的历史运行快照，保留本批次实际任务、参数及回合数，可直接修改；不会读取当前集合内容覆盖。选择当前集合会替换任务清单。{sourceSuite&&<button type="button" onClick={()=>chooseSuite(sourceSuite.id,true)}>使用该集合当前配置</button>}</p>}
      {!suiteId&&<><label><input type="checkbox" checked={saveSuite} onChange={e=>setSaveSuite(e.target.checked)}/>同时保存为可复用集合</label>{saveSuite&&<div className="row"><Field label="新集合名称"><input required value={suiteName} onChange={e=>setSuiteName(e.target.value)}/></Field><Field label="集合版本"><input required value={suiteVersion} onChange={e=>setSuiteVersion(e.target.value)}/></Field></div>}</>}
      {suiteId&&<button type="button" onClick={()=>{setSuiteDefaults(selectedSuite?.client_defaults||{});setSuiteName(selectedSuite?.name||'');setSuiteVersion('');setSuiteId('');setSaveSuite(true)}}>复制集合并修改</button>}
      {suiteId&&<p>使用已发布集合及关联客户端。临时修改请显式切换“自定义清单”。</p>}
      <CaseEditor cases={cases} setCases={setCases} setError={setError} disabled={!!suiteId} ordinary={ordinaryOnce&&!imported}/>
    </WizardModule>}
    {step===1&&<><WizardModule title="运行模板"><div className="submit-grid">
      <Field label="客户端模板"><select required value={templateId} onChange={e=>{setTemplateId(e.target.value);setValues({})}}><option value="" disabled>选择客户端及版本</option>{templateId&&!template&&<option value={templateId} disabled>{imported?.templates.client.id===templateId?`${imported.templates.client.name} / ${imported.templates.client.version}`:templateId}（当前不可用，请重新选择）</option>}{allTemplates.filter(t=>{const relation=historicalSuite?sourceSuite:selectedSuite;return !relation||(relation.template_ids||[relation.template_id]).includes(t.id)||(historicalSuite&&t.id===imported?.submission.client.template_id)}).map(t=><option key={t.id} value={t.id}>{t.name} / {t.version}</option>)}</select></Field>
      </div>{templateId&&!template&&<p role="alert" className="error">原客户端模板 {imported?.templates.client.name||templateId} 当前不在可用模板列表中，可能已停用、归档、删除或无访问权限。请从原集合允许的可用客户端模板中重新选择；若没有选项，请到集合管理检查关联模板及发布状态。</p>}<RuntimeSummary template={template}/></WizardModule><WizardModule title="执行时限"><div className="submit-grid">
      <Field label="请求超时秒"><input type="number" required min={1} max={120} value={draft.request_timeout_seconds} onChange={e=>update('request_timeout_seconds',Number(e.target.value))}/></Field>
      <label><input type="checkbox" checked={draft.unlimited_timeout} onChange={e=>update('unlimited_timeout',e.target.checked)}/>不限制每项总执行时长</label><Field label="每项总超时秒"><input disabled={draft.unlimited_timeout} type="number" required min={1} max={executionLimit??undefined} value={draft.timeout_seconds} onChange={e=>{timeoutEdited.current=true;update('timeout_seconds',Number(e.target.value))}}/></Field></div>
      <p>客户端总执行时限：每项任务全部回合共用，不是每回合单独计时。选择不限仍需心跳续租，且支持取消；需要 Worker 协议 16。{executionLimit!==null?`平台上限 ${executionLimit} 秒。`:'正在读取平台上限。'}</p>
      {limitError&&<p role="alert">平台执行时限读取失败：{limitError}</p>}<button type="button" onClick={()=>{setPreview(null);setLimitReload(n=>n+1)}}>刷新执行时限</button>
      </WizardModule>{template?.task_type==='command'&&<p className="hint">{ordinaryOnce?'普通命令：每项执行一次，清单回合数不用于重复执行。':'宿主机逐回合普通命令：每回合启动一次，按退出码记录完成进度。'}默认无需推理服务；不计算评测目标成功率。</p>}
      {template&&<WizardModule title="业务参数"><p className="hint">这里设置公共参数；集合和任务单项同名配置可能覆盖它。预检后可逐项核对最终值与来源。</p><button type="button" onClick={()=>setCopy({kind:'client',source:template})}>复制修改客户端模板</button><BusinessValues label="客户端业务参数" schema={template.parameter_schema} value={values} onChange={setValues}/>{imported&&<button type="button" onClick={()=>{try{const parameters=parseValues(template.parameter_schema,values);setCases(old=>old.map(c=>({...c,parameters:{...c.parameters,...parameters}})));setError('');}catch(e){setError(String(e));}}}>将已填写的公共参数应用到所有任务</button>}</WizardModule>}
      <WizardModule title="环境变量"><EnvironmentEditor label="启动变量覆盖 JSON" value={draft.environment} onChange={v=>update('environment',v)} allowed={(template?.runtime||template?.host_runtime)?.overridable_env||[]}/></WizardModule>
    </>}
    {step===2&&<WizardModule title="服务配置"><Field label="服务来源"><select value={serviceMode} onChange={e=>setServiceMode(e.target.value as typeof serviceMode)}><option value="external">提供外部服务地址</option><option value="managed">平台启动服务端模板</option><option value="none">无需服务（普通批处理）</option></select></Field>
      {serviceMode==='external'&&<>      <div aria-label="候选地址列表">{addresses.map((address,i)=><div key={i}>
        <Field label={i===0?'推理服务地址':`推理服务地址 ${i+1}`}><input required value={address.url} maxLength={2000} placeholder="ws://推理机器:端口" onChange={e=>editAddress(i,{url:e.target.value})}/></Field>
        <Field label={`服务地址 ${i+1} 使用模式`}><select value={address.mode} onChange={e=>editAddress(i,{mode:e.target.value as 'exclusive'|'shared'})}><option value="exclusive">独占</option><option value="shared">共享</option></select></Field>
        <button type="button" disabled={addresses.length===1} onClick={()=>{addressDirty.current=true;setAddresses(old=>old.filter((_,n)=>n!==i));}}>移除地址 {i+1}</button>
      </div>)}<button type="button" disabled={addresses.length>=100} onClick={()=>{addressDirty.current=true;setAddresses(old=>[...old,{url:'',mode:template?.endpoint_exclusive?'exclusive':'shared'}]);}}>添加推理地址</button>
      <p className="hint">独占地址同时只分配一个任务；共享需要服务端支持多会话。平台自动分配，资源或地址不足时排队。</p></div>
</>}
      {serviceMode==='managed'&&<><ManagedServices value={serviceRows} onChange={setServiceRows} templates={servers} pools={pools} clientPoolId={draft.pool_id} onCopy={t=>setCopy({kind:'server',source:t})}/><label><input type="checkbox" checked={stopAfter} onChange={e=>setStopAfter(e.target.checked)}/>批次结束且客户端清理完成后停止服务</label></>}
      {serviceMode==='none'&&<p>普通命令按退出码判断执行结果，无需提供服务地址。</p>}
    </WizardModule>}
    {step===3&&<WizardModule title="客户端资源与批次并发"><div className="submit-grid wizard-resource-grid">
      <Field label="客户端资源池"><select required value={draft.pool_id} onChange={e=>update('pool_id',e.target.value)}><option value="" disabled>选择资源池</option>{pools.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
      <Field label="最大并发任务数"><input type="number" required min={1} max={100} value={draft.max_concurrent} onChange={e=>update('max_concurrent',Number(e.target.value))}/></Field>
      {template?.resource_requirements?<ResourceRequest contract={template.resource_requirements} value={resourceDrafts[templateId]||{}} onChange={v=>{setResourceDrafts({...resourceDrafts,[templateId]:v});setPreview(null)}} label="客户端资源"/>:<>      <Field label="客户端 GPU 模式"><select value={draft.gpu_mode} onChange={e=>update('gpu_mode',e.target.value)}><option value="exclusive">独占</option><option value="shared">共享</option></select></Field>
      <Field label="每项所需显存 GiB"><input type="number" required min={0} max={10000} value={draft.requested_vram_gb} onChange={e=>update('requested_vram_gb',Number(e.target.value))}/></Field>
      <details><summary>高级资源约束</summary><label><input type="checkbox" checked={draft.cpu_unlimited} onChange={e=>update('cpu_unlimited',e.target.checked)}/>不限制 CPU 上限</label><label><input type="checkbox" checked={draft.memory_unlimited} onChange={e=>update('memory_unlimited',e.target.checked)}/>不限制内存上限</label><p>选择不限时，仍须填写对应正数最低申请量，用于调度预约；实际超出预约的用量不保证可用。</p><Field label="每项 CPU 核数（可选）"><input type="number" min={1} step={1} required={draft.cpu_unlimited} value={draft.cpus} placeholder="使用资源池默认值" onChange={e=>update('cpus',e.target.value)}/></Field>
      <Field label="每项内存 GiB（可选）"><input type="number" min={1} step={1} required={draft.memory_unlimited} value={draft.ram_gb} placeholder="使用资源池默认值" onChange={e=>update('ram_gb',e.target.value)}/></Field>
      <Field label="指定 GPU 型号（可选）"><input maxLength={100} value={draft.required_gpu_model} onChange={e=>update('required_gpu_model',e.target.value)}/></Field></details></>}
    </div>{serviceMode==='managed'&&<section className="wizard-service-resources" aria-label="服务端资源摘要"><h4>服务端资源池</h4>{serviceRows.map(row=><p key={row.key}>{row.key} · {row.instance_count} 个实例 · {pools.find(p=>p.id===(row.pool_id||draft.pool_id))?.name||'尚未选择资源池'}</p>)}<button type="button" onClick={()=>goBack(2)}>修改服务端资源</button></section>}</WizardModule>}
    {step===4&&<>
      <h3>确认运行配置</h3><p>核对以下配置；预检通过前，参数和资源仅为待解析配置。提交不会立即保证获得资源。</p>
      <WizardModule title="任务清单确认"><button type="button" onClick={()=>goBack(0)}>修改任务清单</button><dl className="run-summary"><dt>批次名称</dt><dd>{draft.title}</dd><dt>集合</dt><dd>{selectedSuite?selectedSuite.name+' / '+selectedSuite.version:historicalSuite?historyLabel:'自定义清单'}</dd><dt>任务与回合</dt><dd>{ordinaryOnce?`${cases.length} 项任务，每项启动一次`: `${cases.length} 项任务，${cases.reduce((sum,c)=>sum+c.episodes,0)} 个回合`}</dd></dl>
      <details><summary>逐项任务与参数</summary>{cases.map(c=><section key={c.case_id}><h4>{c.task_name} · {c.case_id}</h4><p>{ordinaryOnce?'执行一次':`${c.episodes} 回合`} · 种子 {c.seed}</p><ValueTable value={c.parameters}/>{c.resources&&<ValueTable value={c.resources}/>}<p>服务关联：{c.service_keys?.join('、')||'兼容服务'}</p></section>)}</details></WizardModule>
      <WizardModule title="客户端配置确认"><button type="button" onClick={()=>goBack(1)}>修改客户端</button><p>{template?.name} / {template?.version}</p><RuntimeSummary template={template}/><p>请求 {draft.request_timeout_seconds} 秒 / 每项总执行时限 {draft.unlimited_timeout?'不限':draft.timeout_seconds+' 秒'}（全部回合共用）；平台上限 {executionLimit??'未获取'} 秒</p><details><summary>模板与集合参数默认值</summary><h4>模板默认</h4><ValueTable value={Object.fromEntries(Object.entries(template?.parameter_schema?.fields||{}).filter(([,v])=>v.default!==undefined).map(([k,v])=>[k,v.default]))}/><h4>集合公共参数</h4><ValueTable value={suiteDefaults}/></details><h4>本次公共参数覆盖</h4><ValueTable value={values}/><p className="hint">未输入的参数继承模板或集合；单项覆盖以预检最终值为准。</p><h4>本次启动变量覆盖</h4><pre>{draft.environment}</pre></WizardModule>
      <WizardModule title="服务端配置确认"><button type="button" onClick={()=>goBack(2)}>修改服务端</button>{serviceMode==='managed'?serviceRows.map(row=><section className="wizard-service-card" key={row.key}><h4>{row.key} · {servers.find(t=>t.id===row.template_id)?.name} / {servers.find(t=>t.id===row.template_id)?.version}</h4><dl className="run-summary"><dt>服务实例数</dt><dd>{row.instance_count}</dd><dt>资源池</dt><dd>{pools.find(p=>p.id===(row.pool_id||draft.pool_id))?.name||'未选择'}{!row.pool_id?'（跟随客户端）':''}</dd><dt>服务访问方式</dt><dd>{row.mode==='shared'?'多个客户端共享服务':'每实例同时一个客户端'}</dd><dt>服务地址</dt><dd>启动后由平台分配，等待就绪检查</dd><dt>固定槽位</dt><dd>{row.slot_id||'自动选择'}</dd></dl><h4>模板参数默认值</h4><ValueTable value={Object.fromEntries(Object.entries(servers.find(t=>t.id===row.template_id)?.parameter_schema?.fields||{}).filter(([,v])=>v.default!==undefined).map(([k,v])=>[k,v.default]))}/><h4>本次业务参数覆盖</h4><ValueTable value={row.imported_parameters??row.parameters}/><h4>本次运行变量</h4><pre>{row.environment}</pre><h4>资源申请</h4><ValueTable value={preview?.service_plan?.find(s=>s.key===row.key)?.resources||row.resource_overrides||{cpus:row.cpus||'待解析',ram_gb:row.ram_gb||'待解析',cpu_unlimited:!!row.cpu_unlimited,memory_unlimited:!!row.memory_unlimited,gpu_mode:row.gpu_mode,requested_vram_gb:row.requested_vram_gb}} sources={preview?.service_plan?.find(s=>s.key===row.key)?.resource_sources}/></section>):serviceMode==='none'?<p>无需服务</p>:addresses.map(e=><p key={e.url}>{e.url} · {e.mode==='shared'?'共享':'独占'}</p>)}{serviceMode==='managed'&&<p>结束策略：{stopAfter?'批次结束且客户端清理完成后停止服务':'保留服务运行'}</p>}</WizardModule>
      <WizardModule title="资源与并发确认"><button type="button" onClick={()=>goBack(3)}>修改资源与并发</button><p>{pools.find(p=>p.id===draft.pool_id)?.name} · 最大并发 {draft.max_concurrent}</p><p>每项申请与来源在下方预检结果中逐项显示；服务端与客户端分别占用资源。</p></WizardModule>
      <button type="button" onClick={()=>void inspect()}>重新检查</button>
      {!preview&&<p role="status">{busy?'正在检查配置与资源…':'尚无有效检查结果，请重新检查。'}</p>}
      {preview&&<div className="preflight-result" aria-label="预检结果">
        <details><summary>有效资源申请与来源</summary><p>以下为后端按模板、提交及集合单项解析的结果。</p>{preview.case_checks?.map(c=><section key={c.case_id}><h4>客户端 · {c.case_id}</h4><ValueTable value={c.resources} sources={c.resource_sources}/></section>)}{preview.service_plan?.map(c=><section key={c.key}><h4>服务端 · {c.key}</h4><ValueTable value={c.resources} sources={c.resource_sources}/></section>)}</details>
        <h3>{preview.status==='ready'?'当前有节点可接纳单项任务':preview.status==='waiting_resources'?'需要等待资源':'需要准备运行环境'}</h3>
        <p>配置校验：{preview.can_submit?'允许提交':'需要修正'}。资源与环境状态见下方；连通性尚未通过本次实际执行验证。</p><p className="hint">检查时间：{new Date(preview.checked_at).toLocaleTimeString()}。{preview.note} 最大并发为上限，不保证同时启动。</p>
        <details><summary>有效业务参数与来源</summary>{preview.effective_parameters?.map(p=><div key={p.case_id}><h4>{p.case_id}</h4><ValueTable value={p.parameters} sources={p.sources}/></div>)}</details>{preview.pair_checks?.map(p=><p key={p.case_id}>{p.case_id}：{p.pair_ready?'两端资源当前可接纳':'等待两端资源或服务环境'}</p>)}<Reasons reasons={preview.reasons}/><ul>{preview.endpoints?.map(e=><li key={e.url}>{e.url} · {e.mode==='exclusive'?'独占':'共享'} · {e.available?'当前可分配':'等待互斥占用释放'} · 当前占用 {e.occupancy} · 可分配不代表已验证连通</li>)}</ul>
        <h4>客户端候选设备</h4>{!preview.nodes.length&&<p>没有客户端候选设备，请检查资源池及上方原因。</p>}<div className="table-scroll"><table><thead><tr><th>节点 / 设备</th><th>当前状态与操作建议</th></tr></thead><tbody>{preview.nodes.map(n=><tr key={n.slot_id}><td>{n.node_name} / 设备 {n.gpu_index}<small>{n.gpu_model}</small></td><td>{n.ready?'当前可接纳单项任务':<><Reasons reasons={n.reasons}/>{!n.reasons.length&&'等待上方平台或用户配额释放'}</>}</td></tr>)}</tbody></table></div>
        {preview.server_checks?.map(service=><section key={service.key} aria-label={`服务端 ${service.key} 候选设备`}><h4>服务端 {service.key} · 资源池 {service.pool_name}</h4><p>{service.status==='ready'?'当前有服务端候选设备':service.status==='waiting_resources'?'服务端等待资源':'服务端环境未就绪'}</p><Reasons reasons={service.reasons}/>{service.nodes.length?<div className="table-scroll"><table><thead><tr><th>节点 / 设备</th><th>当前状态与操作建议</th></tr></thead><tbody>{service.nodes.map(node=><tr key={node.slot_id}><td>{node.node_name} / 设备 {node.gpu_index}<small>{node.gpu_model}</small></td><td>{node.ready?'符合服务端要求':node.reasons.length?<Reasons reasons={node.reasons}/>:<span>设备自身满足要求，等待上方配额或两端联合分配</span>}</td></tr>)}</tbody></table></div>:<p>资源池中没有可用的服务端设备。</p>}</section>)}
        <p>环境缺失和资源占用不会阻止合法任务提交排队；环境问题需要管理员处理，不会因为提交排队就自动解决。</p>

        <details><summary>{template?.task_type==='command'?'可选：先试跑一项命令':'可选：先试跑一个任务、一个回合'}</summary>
          <Field label="试跑任务"><select value={trialCase||preview.cases[0]?.case_id} onChange={e=>setTrialCase(e.target.value)}>{preview.cases.map(c=><option key={c.case_id} value={c.case_id}>{c.case_id} / {c.task_name}</option>)}</select></Field>
          <p className="hint">试跑独立排队，不修改集合和正式运行配置，不自动开始正式运行。</p>
          <button type="button" disabled={!preview.can_submit} onClick={()=>void submit(true)}>{template?.task_type==='command'?'提交命令试跑':'提交单回合试跑'}</button>
        </details>
      </div>}
    </>}
    <div className={`wizard-actions${step===3?' wizard-resource-actions':''}`}>{step===4&&preview&&<button type="button" className="primary" disabled={!preview.can_submit||!!submitted} onClick={()=>void submit()}>{preview.status==='ready'?'提交评测批次':'提交排队'}</button>}{step>0&&<button type="button" onClick={()=>goBack(step-1)}>上一步</button>}{step<4&&<button type="submit" className="primary">{step===3?'检查并确认':'下一步'}</button>}</div>
    </fieldset></form>
    {copy&&<TemplateCopy key={copy.source.id} {...copy} onClose={()=>setCopy(null)} onPublished={t=>{if(copy.kind==='client'){setExtraTemplates(old=>[...old,t as Template]);setTemplateId(t.id);if(selectedSuite)setSuiteDefaults(selectedSuite.client_defaults||{});setSuiteId('');setValues({})}else{setServers(old=>[...old,t as ServerTemplate]);setServiceRows(old=>old.map(s=>s.template_id===copy.source.id?{...s,template_id:t.id}:s))}setCopy(null)}}/>}
    {submitted&&<p role="status">{submitted}{onBatch&&<button onClick={()=>onBatch(submittedId)}>查看已提交批次</button>}</p>}
    {trial&&<section aria-label="试跑进度"><button onClick={()=>onBatch?.(trial.id)}>查看试跑批次</button><h3>{trial.title} · {statuses[trial.status]||trial.status}</h3>
      {trialError&&<p role="alert">试跑状态更新失败：{trialError}，将自动重试。</p>}
      {trial.cases.map(c=><div key={c.job_id}><p>{statuses[c.status]||c.status} · {c.task_type==='command'?(c.episode_reporting?`普通命令已完成 ${c.progress.completed}/1 回合`:'普通命令，按退出码判定执行结果'):c.episode_reporting?`已完成 ${c.progress.completed}/1 回合，目标成功 ${c.progress.successes}`:'客户端未声明回合上报，无法验证回合报告'}</p>{c.error&&<p>{c.error}</p>}<button onClick={()=>onJob(c.job_id)}>试跑详情 / 取消</button></div>)}
      <p className="hint">{trial.cases.length>0&&trial.cases.every(c=>c.task_type==='command')?'试跑只代表所选命令的一次执行，不代表完整集合执行结果。':'试跑只代表所选任务的一个回合，不代表完整集合评测结果。'}</p>
    </section>}
  </section>;
}
function Reasons({reasons}:{reasons:Reason[]}){const labels:Record<string,string>={resource:'资源条件',resources:'资源条件',environment:'运行环境',configuration:'配置校验',capacity:'资源容量',quota:'并发额度',connectivity:'连通性'};const groups:Record<string,Reason[]>={};for(const r of reasons)(groups[labels[r.category]||'其他检查']??=[]).push(r);return <>{Object.entries(groups).map(([name,items])=><section key={name}><strong>{name}</strong><ul>{items.map((r,i)=><li key={r.code+i}>{r.message}；{r.action}</li>)}</ul></section>)}</>;}

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

function CaseEditor({cases,setCases,setError,disabled=false,ordinary=false}:{cases:Case[];setCases:React.Dispatch<React.SetStateAction<Case[]>>;setError:(error:string)=>void;disabled?:boolean;ordinary?:boolean}) {
  function change(i:number,k:keyof Case,value:unknown) {setCases(old=>old.map((c,n)=>n===i?{...c,[k]:value}:c));}
  if(disabled)return <section aria-label="集合任务摘要"><p>共 {cases.length} 项、{ordinary?0:cases.reduce((sum,c)=>sum+c.episodes,0)} 个回合。当前集合只读；使用“复制集合并修改”调整本次清单。</p><div className="table-scroll"><table><thead><tr><th>任务名</th><th>回合数</th><th>种子</th><th>单项配置</th></tr></thead><tbody>{cases.map(c=><tr key={c.case_id}><td>{c.task_name}</td><td>{ordinary?'执行一次':c.episodes}</td><td>{c.seed}</td><td><details><summary>查看 {c.case_id} 配置</summary><ValueTable value={c.parameters}/><ValueTable value={c.resources}/><p>服务：{c.service_keys?.join('、')||'兼容服务'}</p></details></td></tr>)}</tbody></table></div></section>;
  return <>
        <fieldset disabled={disabled}>
        <Field label="导入 JSON 清单"><input type="file" accept="application/json,.json" onChange={async e=>{const file=e.target.files?.[0];if(!file)return;try{if(file.size>1048576)throw new Error('清单文件不能超过 1 MiB');setCases(parseCases(JSON.parse(await file.text())));setError('');}catch(error){setError(String(error));}e.target.value='';}}/></Field>
        <div className="table-scroll"><table><thead><tr><th>任务名</th><th>回合数</th><th>种子</th><th>单项配置</th><th>操作</th></tr></thead><tbody>
          {cases.map((c,i)=><tr key={i}>
            <td><input aria-label={`第 ${i+1} 项任务名`} value={c.task_name} maxLength={200} required onChange={e=>change(i,'task_name',e.target.value)}/></td>
            <td><input aria-label={`第 ${i+1} 项回合数`} type="number" min={1} max={10000} required disabled={ordinary} value={ordinary?1:c.episodes} onChange={e=>change(i,'episodes',Number(e.target.value))}/></td>
            <td><input aria-label={`第 ${i+1} 项种子`} type="number" min={0} max={2147483647} required value={c.seed} onChange={e=>change(i,'seed',Number(e.target.value))}/></td>
            <td><details><summary>任务 {i+1} 详细配置</summary><Field label={`第 ${i+1} 项标识`}><input value={c.case_id} maxLength={100} required onChange={e=>change(i,'case_id',e.target.value)}/></Field>
            <JsonDraft key={'params'+i} label={`第 ${i+1} 项参数`} value={c.parameters} onChange={v=>change(i,'parameters',v)}/>
            <details><summary>单项资源与服务关联</summary><JsonDraft key={'resources'+i} label={`第 ${i+1} 项资源`} value={c.resources} onChange={v=>change(i,'resources',v)}/><JsonDraft key={'services'+i} label={`第 ${i+1} 项服务关联`} value={c.service_keys} kind="strings" onChange={v=>change(i,'service_keys',v)}/><p>单项值优先于公共配置。空资源对象使用公共申请；空服务列表使用兼容服务。</p></details></details></td>
            <td><button type="button" disabled={cases.length===1} onClick={()=>setCases(old=>old.filter((_,n)=>n!==i))}>移除</button></td>
          </tr>)}
        </tbody></table></div>
        <button type="button" disabled={cases.length>=100} onClick={()=>setCases(old=>[...old,{case_id:'case-'+(old.length+1)+'-'+Date.now().toString(36),task_name:'',episodes:3,seed:0,parameters:{}}])}>添加任务</button>
        </fieldset>
        <p>共 {cases.length} 项、{ordinary?0:cases.reduce((sum,c)=>sum+c.episodes,0)} 个回合。{ordinary?'普通命令每项执行一次。':''}每项独立调度；资源不足时保留排队；最大并发是上限。</p>
  </>;
}
