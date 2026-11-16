import React,{useEffect,useState} from 'react';
import {mountDraft,mountValues} from './mount-editor';
import {api} from './api';
import {Field} from './form-fields';
import {TemplateExamples} from './template-examples';
import {CommandEditor} from './command-editor';
import {TemplateDirectories,assetValues,AssetRow} from './template-directories';
import {CommandMappings,validateMappings,PlatformBinding} from './command-mappings';
import {TemplateHelp,TemplateConfig} from './template-guidance';
import {ParameterEditor,parameterDraft,parameterSchema} from './parameter-editor';

export type Template={owner_id?:string|null;revision?:number;available_to_members?:boolean;task_type?:string;platform_bindings?:unknown[];parameter_schema?:Record<string,unknown>|null;id:string;name:string;version:string;image:string;execution_mode?:string;integration_mode?:string;command:string[];enabled:boolean;episode_reporting:boolean;endpoint_exclusive?:boolean;protocols:string[];runtime:any;host_runtime:any;port?:number;protocol?:string;health_type?:string;health_path?:string;startup_timeout?:number};
export function Templates(){
 const [kind,setKind]=useState('client');
 return <><div className="tabs" role="tablist" aria-label="模板类型">{[['client','客户端模板'],['server','服务端模板']].map(([id,label])=><button role="tab" aria-selected={kind===id} key={id} onClick={()=>setKind(id)}>{label}</button>)}</div>
 {['client','server'].map(id=><div key={id} hidden={kind!==id}><TemplateWorkspace kind={id}/></div>)}</>;
}
function TemplateWorkspace({kind}:{kind:string}){
 const [rows,setRows]=useState<Template[]>([]),[edit,setEdit]=useState<Template|null|undefined>(undefined),[error,setError]=useState('');
 const [tab,setTab]=useState('list'),[view,setView]=useState<Template|null>(null),[dirty,setDirty]=useState(false),[generation,setGeneration]=useState(0);
 const refresh=()=>api<Template[]>(kind==='client'?'client-templates':'admin/server-templates').then(setRows);
 useEffect(()=>{void refresh().catch(e=>setError(String(e)))},[kind]);
 function register(source?:Template){
  if(source&&dirty&&!confirm('复制将替换当前未提交的模板草稿，是否继续？'))return;
  if(source||edit===undefined){setEdit(source||null);setDirty(false);setGeneration(n=>n+1)}
  setView(null);setTab('register');
 }
 async function act(t:Template,action:'state'|'archive'){try{setError('');await api('admin/'+kind+'-templates/'+t.id,action==='state'?'PATCH':'DELETE',action==='state'?{enabled:!t.enabled}:undefined);await refresh()}catch(e){setError(String(e))}}
 return <>
 <div className="tabs" role="tablist" aria-label={(kind==='client'?'客户端':'服务端')+'模板操作'}><button role="tab" aria-selected={tab==='list'} onClick={()=>setTab('list')}>已注册模板</button><button role="tab" aria-selected={tab==='register'} onClick={()=>register()}>注册新模板</button></div>
 <section className="panel" hidden={tab!=='list'}><h2>{kind==='client'?'客户端':'服务端'}运行模板</h2><p>模板版本固定执行配置；修改时复制为新版本，已提交任务保留原快照。</p>{error&&<p role="alert" className="error">{error}</p>}<button onClick={()=>register()}>注册模板</button>
 {!rows.length&&<p className="muted">暂无模板。进入“注册新模板”，根据操作说明准备运行环境并填写配置。</p>}
 <div className="template-table"><table><thead><tr><th>名称 / 版本</th><th>执行方式</th><th>接入方式</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows.map(t=><tr key={t.id}><td>{t.name} / {t.version}</td><td>{t.execution_mode==='host'?'宿主机':'Docker'}</td><td>{kind==='server'?'长期服务':t.integration_mode==='sdk-command'?'SDK + 业务命令':'直接评测协议'}</td><td>{t.enabled?'启用':'停用'}</td><td><button onClick={()=>setView(t)}>查看配置</button><button onClick={()=>register(t)}>复制新版本</button>{kind==='server'&&!t.owner_id&&<button onClick={async()=>{try{await api('admin/server-templates/'+t.id+'/access','PATCH',{expected_revision:t.revision||0,available_to_members:!t.available_to_members});await refresh()}catch(e){setError(String(e))}}}>{t.available_to_members?'撤回成员使用':'允许成员使用'}</button>}<button onClick={()=>void act(t,'state')}>{t.enabled?'停用':'启用'}</button><button onClick={()=>{if(confirm('归档此模板？已有任务快照保留。'))void act(t,'archive')}}>归档</button></td></tr>)}</tbody></table></div>
 {view&&<TemplateConfig template={view} kind={kind} onClose={()=>setView(null)} onCopy={()=>register(view)}/>}</section>
 <div hidden={tab!=='register'}><TemplateExamples kind={kind} onUse={register}/>{edit!==undefined&&<div onChange={()=>setDirty(true)} onClick={e=>{if((e.target as HTMLElement).closest('form button'))setDirty(true)}}><TemplateForm key={generation} kind={kind} source={edit} onClose={()=>setTab('list')} onSave={async()=>{await refresh();setEdit(undefined);setDirty(false);setTab('list')}}/></div>}</div></>;
}
function TemplateForm({kind,source,onSave,onClose}:{kind:string;source:Template|null;onSave:()=>Promise<void>;onClose:()=>void}){
 const [mode,setMode]=useState(source?.execution_mode||'docker'),[integration,setIntegration]=useState(source?.integration_mode||'direct'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [parameters,setParameters]=useState(()=>parameterDraft(source?.parameter_schema));
 const [command,setCommand]=useState(()=>JSON.stringify(source?.command||['python3','/app/business.py'],null,2));
 const [mounts,setMounts]=useState(()=>mountDraft(source?.runtime?.mounts));
 const [assets,setAssets]=useState<AssetRow[]>(()=>source?.execution_mode==='host'?(source.host_runtime?.assets||[]).map((name:string)=>({name,target:''})):Object.entries(source?.runtime?.assets||{}).map(([name,target])=>({name,target:String(target)})));
 const settings=(mode==='host'?source?.host_runtime:source?.runtime)||{};
 const [bindings,setBindings]=useState<PlatformBinding[]>(()=>structuredClone(source?.platform_bindings||[]) as PlatformBinding[]);
 const [workDir,setWorkDir]=useState(source?.runtime?.work_dir||'/tmp/mini-eval-work'),[outputDir,setOutputDir]=useState(source?.runtime?.output_dir||'/tmp/mini-eval-output');
 const errorRef=React.useRef<HTMLParagraphElement>(null);
 useEffect(()=>{if(error){errorRef.current?.focus({preventScroll:true});errorRef.current?.scrollIntoView({block:'center'});}},[error]);
 async function save(e:React.FormEvent<HTMLFormElement>){e.preventDefault();setBusy(true);setError('');try{const f=new FormData(e.currentTarget),environment=JSON.parse(String(f.get('environment'))),assetMap=assetValues(assets),overridable_env=String(f.get('overrides')).split(',').map(x=>x.trim()).filter(Boolean);
   const body:any={name:f.get('name'),version:f.get('version'),execution_mode:mode,integration_mode:kind==='client'?integration:'direct',command:JSON.parse(String(f.get('command'))),episode_reporting:f.has('episode_reporting')||integration==='sdk-command',endpoint_exclusive:source?.endpoint_exclusive??false,protocols:source?.protocols||['http','https','ws','wss']};
   body.parameter_schema=parameterSchema(parameters);
   validateMappings(bindings,body.parameter_schema,body.command,environment,overridable_env);
   body.platform_bindings=bindings;body.task_type=source?.task_type||'evaluation';
   if(mode==='docker'){body.image=f.get('image');body.runtime={...source?.runtime,interpreter:source?.runtime?.interpreter||'python3',environment,overridable_env,assets:assetMap,work_dir:f.get('work_dir'),output_dir:f.get('output_dir'),mounts:mountValues(mounts)};}
   else{body.host_runtime={...source?.host_runtime,environment_name:f.get('environment_name'),environment,overridable_env,assets:assets.map(a=>a.name)};}
   if(kind==='server')Object.assign(body,{port:Number(f.get('port')),protocol:f.get('protocol'),health_type:f.get('health_type'),health_path:f.get('health_path'),startup_timeout:Number(f.get('startup_timeout'))});
   await api('admin/'+kind+'-templates','POST',body);await onSave();
 }catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="panel"><h2>{source?.id.startsWith('example:')?'从样例注册模板':source?'复制为新版本':'注册运行模板'}</h2>{error&&<p ref={errorRef} tabIndex={-1} role="alert" className="error">{error}</p>}<TemplateHelp kind={kind} mode={mode} integration={integration}/><form onSubmit={save}>
 <fieldset><legend>基础信息</legend><p className="muted">名称说明用途，例如 robotwin-client；版本例如 v1。修改已注册配置时创建新版本，历史任务继续使用原配置。</p><div className="row"><Field label="模板名称"><input name="name" defaultValue={source?.name} required maxLength={100}/></Field><Field label="版本"><input name="version" defaultValue={source?.id.startsWith('example:')?source.version:undefined} required maxLength={100} placeholder="例如 v1"/></Field></div></fieldset>
 <fieldset><legend>平台参数</legend><p className="muted">执行身份、GPU、推理地址和回合身份由平台注入；宿主机目录由机器配置解析。业务程序通过 MINI_EVAL_INPUT 读取任务输入。</p>
 {mode==='docker'?<div className="row"><Field label="容器内工作目录"><input name="work_dir" required value={workDir} onChange={e=>setWorkDir(e.target.value)}/></Field><Field label="容器内输出目录"><input name="output_dir" required value={outputDir} onChange={e=>setOutputDir(e.target.value)}/></Field></div>:<p>工作目录和输出目录使用该节点工作区下的独立执行目录。</p>}
 </fieldset>
 <fieldset><legend>业务参数</legend><p className="muted">这里定义可填写的字段，具体取值在集合任务或服务部署时填写。例如 scene（字符串）绑定命令行 --scene，平台会追加该参数；不要在命令中重复写同一绑定。任务名、回合数和种子在任务集合中配置。</p><details><summary>业务参数如何传给程序？</summary><p>输入文件：从 MINI_EVAL_INPUT 指向的 JSON 读取 business 字段。命令行：绑定名称填 --scene。环境变量：绑定名称填 SCENE。需要在提交时修改的字段，勾选“允许运行时覆盖”；不要使用 endpoint 等平台保留名称。</p></details><ParameterEditor value={parameters} onChange={setParameters}/></fieldset>
 <fieldset><legend>运行参数</legend>
 <Field label="执行方式"><select value={mode} onChange={e=>setMode(e.target.value)}><option value="docker">Docker 镜像运行</option><option value="host">宿主机运行</option></select></Field>
 {kind==='client'&&<Field label="协议接入方式"><select value={integration} onChange={e=>setIntegration(e.target.value)}><option value="direct">业务程序直接实现评测协议</option><option value="sdk-command">SDK + 每回合业务命令</option></select></Field>}
 {mode==='docker'?<Field label="Docker 镜像"><input name="image" required defaultValue={source?.image} placeholder="registry/image:tag 或 digest"/></Field>:<><Field label="节点环境名称"><input name="environment_name" required pattern="[A-Za-z][A-Za-z0-9_]*" defaultValue={settings.environment_name} placeholder="例如 simulation"/></Field><p>节点需批准并探测此宿主机环境。宿主机模式适合可信业务程序，不提供容器安全隔离。</p></>}
 <CommandEditor value={command} onChange={setCommand}/>
 <CommandMappings kind={kind} bindings={bindings} onBindings={setBindings} parameters={parameters} onParameters={setParameters}/>{kind==='client'&&<><label><input type="checkbox" name="episode_reporting" defaultChecked={source?.episode_reporting??true}/>业务程序逐回合上报（SDK 命令模式自动启用）</label><p>SDK 命令每次执行一个回合，通过 MINI_EVAL_RESULT 写入结果；直接协议程序负责回合事件和最终汇总。参数支持 task_name、episode_id、episode_seed、endpoint、work_dir 等占位符。</p></>}
 <p className="muted">环境变量填写 JSON 对象，例如 {'{"LOG_LEVEL":"info"}'}；不需要时保留 {'{}'}。允许覆盖的变量名填写 LOG_LEVEL，多个名称用逗号分隔。平台身份、GPU 和地址等保留变量由平台管理。</p><Field label="环境变量（JSON 对象）"><textarea name="environment" rows={3} defaultValue={JSON.stringify(settings.environment||{},null,2)}/></Field>
 <Field label="允许提交时覆盖的变量名（逗号分隔）"><input name="overrides" defaultValue={(settings.overridable_env||[]).join(',')}/></Field>
 <TemplateDirectories mode={mode} assets={assets} onAssets={setAssets} mounts={mounts} onMounts={setMounts}/>
 {kind==='server'&&<><p className="muted">服务程序需使用平台传入的监听地址和端口。HTTP 健康检查要求程序提供相应路径；TCP 仅检查端口可连接。启动等待时间应覆盖模型加载耗时。</p><div className="row"><Field label="服务端口"><input name="port" type="number" min={1024} max={65535} required defaultValue={source?.port||8000}/></Field><Field label="服务协议"><select name="protocol" defaultValue={source?.protocol||'http'}>{['http','https','ws','wss'].map(x=><option key={x}>{x}</option>)}</select></Field><Field label="健康检查"><select name="health_type" defaultValue={source?.health_type||'http'}><option value="http">HTTP</option><option value="tcp">TCP</option></select></Field></div><Field label="健康检查路径"><input name="health_path" required defaultValue={source?.health_path||'/healthz'}/></Field><Field label="启动健康等待秒数"><input name="startup_timeout" type="number" min={5} max={1800} defaultValue={source?.startup_timeout||120} required/></Field></>}
 </fieldset><div className="row"><button disabled={busy} className="primary">注册此版本</button><button type="button" onClick={onClose}>取消</button></div></form></section>
}
