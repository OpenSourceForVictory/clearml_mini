import {useDetailNavigation} from './detail-navigation';
import {GuideButton} from './user-guide';
import {ReadinessEditor,readinessDraft,Readiness} from './readiness-editor';
import {ClientRegistration} from './evaluation-setup';
import React,{useEffect,useState} from 'react';
import {mountDraft,mountValues} from './mount-editor';
import {ResourceRequirements,ResourceContract} from './resource-requirements';
import {api} from './api';
import {Field} from './form-fields';
import {TemplateExamples} from './template-examples';
import {EnvironmentEditor,environmentDraft,environmentValues,EnvironmentRow} from './environment-editor';
import {LaunchPreview} from './launch-preview';
import {CommandEditor} from './command-editor';
import {TemplateDirectories,assetValues,AssetRow} from './template-directories';
import {CommandMappings,validateMappings,PlatformBinding} from './command-mappings';
import {TemplateHelp,TemplateConfig} from './template-guidance';
import {ParameterEditor,parameterDraft,parameterSchema} from './parameter-editor';

export type Template={result_directory?:string|null;readiness?:Readiness|null;resource_requirements?:ResourceContract|null;owner_id?:string|null;revision?:number;available_to_members?:boolean;task_type?:string;platform_bindings?:unknown[];parameter_schema?:Record<string,unknown>|null;id:string;name:string;version:string;image:string;execution_mode?:string;integration_mode?:string;command:string[];enabled:boolean;episode_reporting:boolean;endpoint_exclusive?:boolean;protocols:string[];runtime:any;host_runtime:any;port?:number;protocol?:string;health_type?:string;health_path?:string;startup_timeout?:number};
export function Templates(){
 const [kind,setKind]=useState('client');
 return <><div className="tabs" role="tablist" aria-label="模板类型">{[['client','客户端模板'],['server','服务端模板'],['registration','镜像与代码接入']].map(([id,label])=><button role="tab" aria-selected={kind===id} key={id} onClick={()=>setKind(id)}>{label}</button>)}</div>
 <div hidden={kind!=='registration'}>{kind==='registration'&&<ClientRegistration/>}</div>{['client','server'].map(id=><div key={id} hidden={kind!==id}><TemplateWorkspace kind={id}/></div>)}</>;
}
function TemplateWorkspace({kind}:{kind:string}){
 const details=useDetailNavigation(),formTop=React.useRef<HTMLDivElement>(null);
 const [rows,setRows]=useState<Template[]>([]),[edit,setEdit]=useState<Template|null|undefined>(undefined),[error,setError]=useState('');
 const [tab,setTab]=useState('list'),[view,setView]=useState<Template|null>(null),[dirty,setDirty]=useState(false),[generation,setGeneration]=useState(0);
 const refresh=()=>api<Template[]>(kind==='client'?'client-templates':'admin/server-templates').then(setRows);
 useEffect(()=>{void refresh().catch(e=>setError(String(e)))},[kind]);
 function register(source?:Template){
  if(source&&dirty&&!confirm('复制将替换当前未提交的模板草稿，是否继续？'))return;
  if(source||edit===undefined){setEdit(source||null);setDirty(false);setGeneration(n=>n+1)}
  details.dismiss();setView(null);setTab('register');
  requestAnimationFrame(()=>{formTop.current?.focus({preventScroll:true});formTop.current?.scrollIntoView({block:'start'});});
 }
 async function act(t:Template,action:'state'|'archive'){try{setError('');await api('admin/'+kind+'-templates/'+t.id,action==='state'?'PATCH':'DELETE',action==='state'?{enabled:!t.enabled}:undefined);await refresh()}catch(e){setError(String(e))}}
 return <>
 <div hidden={details.open}><div className="tabs" role="tablist" aria-label={(kind==='client'?'客户端':'服务端')+'模板操作'}><button role="tab" aria-selected={tab==='list'} onClick={()=>setTab('list')}>已注册模板</button><button role="tab" aria-selected={tab==='register'} onClick={()=>register()}>注册新模板</button></div>
 <section className="panel" hidden={tab!=='list'}><h2>{kind==='client'?'客户端':'服务端'}运行模板</h2><p>模板版本固定执行配置；修改时复制为新版本，已提交任务保留原快照。</p>{error&&<p role="alert" className="error">{error}</p>}<button onClick={()=>register()}>注册模板</button>
 {!rows.length&&<p className="muted">暂无模板。进入“注册新模板”，根据操作说明准备运行环境并填写配置。</p>}
 <div className="template-table"><table><thead><tr><th>名称 / 版本</th><th>执行方式</th><th>接入方式</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows.map(t=><tr key={t.id}><td>{t.name} / {t.version}</td><td>{t.execution_mode==='host'?'宿主机':'Docker'}</td><td>{kind==='server'?'长期服务':t.integration_mode==='sdk-task-result'?'普通命令 + SDK 任务结果':t.integration_mode==='sdk-command'?'SDK + 业务命令':'直接评测协议'}</td><td>{t.enabled?'启用':'停用'}</td><td><button onClick={()=>{setView(t);details.enter()}}>查看配置</button><button onClick={()=>register(t)}>复制新版本</button>{kind==='server'&&!t.owner_id&&<button onClick={async()=>{try{await api('admin/server-templates/'+t.id+'/access','PATCH',{expected_revision:t.revision||0,available_to_members:!t.available_to_members});await refresh()}catch(e){setError(String(e))}}}>{t.available_to_members?'撤回成员使用':'允许成员使用'}</button>}<button onClick={()=>void act(t,'state')}>{t.enabled?'停用':'启用'}</button><button onClick={()=>{if(confirm('归档此模板？已有任务快照保留。'))void act(t,'archive')}}>归档</button></td></tr>)}</tbody></table></div>
 </section>
 <div hidden={tab!=='register'} ref={formTop} tabIndex={-1}><TemplateExamples kind={kind} onUse={register}/>{edit!==undefined&&<div onChange={()=>setDirty(true)} onClick={e=>{if((e.target as HTMLElement).closest('form button'))setDirty(true)}}><TemplateForm key={generation} kind={kind} source={edit} onClose={()=>setTab('list')} onSave={async()=>{await refresh();setEdit(undefined);setDirty(false);setTab('list')}}/></div>}</div></div>
 {view&&details.open&&<section className="panel management-detail"><div className="detail-heading" ref={details.heading} tabIndex={-1}><h2>{view.name} / {view.version}</h2><button onClick={details.back}>返回模板列表</button></div><TemplateConfig key={view.id} template={view} kind={kind} onClose={details.back} onCopy={()=>register(view)}/></section>}</>;
}
export function TemplateForm({kind,source,onSave,onClose,readOnly=false}:{kind:string;source:Template|null;onSave?:()=>Promise<void>;onClose?:()=>void;readOnly?:boolean}){
 const [mode,setMode]=useState(source?.execution_mode||'docker'),[integration,setIntegration]=useState(source?.integration_mode||'direct'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [taskType,setTaskType]=useState(kind==='client'?(source?.task_type||'evaluation'):'evaluation');
 const [taskResultReporting,setTaskResultReporting]=useState(source?.integration_mode==='sdk-task-result');
 const [customReadiness,setCustomReadiness]=useState(!!source?.readiness);
 const [readiness,setReadiness]=useState(()=>readinessDraft(source?.readiness));
 const ordinary=kind==='client'&&taskType==='command';
 const [reporting,setReporting]=useState(source?.episode_reporting??true);
 const [commandMode,setCommandMode]=useState((source?.host_runtime||source?.runtime)?.command_mode||(source?'legacy':'exec'));
 const [containerDirectory,setContainerDirectory]=useState(source?.runtime?.working_directory||'');
 const [platformPython,setPlatformPython]=useState(source?.runtime?.interpreter||'python3');
 const [hostDirectory,setHostDirectory]=useState(source?.host_runtime?.working_directory||'');
 const [hostEnvironmentSource,setHostEnvironmentSource]=useState(source?.host_runtime?.environment_source||'explicit');
 const [hostEpisodeErrorPolicy,setHostEpisodeErrorPolicy]=useState(source?.host_runtime?.episode_error_policy||'stop');
 const [hostEpisodeLaunch,setHostEpisodeLaunch]=useState(source?.host_runtime?.episode_launch||'whole_task');
 const [image,setImage]=useState(source?.image||''),[hostEnvironment,setHostEnvironment]=useState(source?.host_runtime?.environment_name||'');
 const [resources,setResources]=useState<ResourceContract|null>(source?.resource_requirements||null);
 const [parameters,setParameters]=useState(()=>parameterDraft(source?.parameter_schema));
 const [command,setCommand]=useState(()=>JSON.stringify(source?.command||['python3','/app/business.py'],null,2));
 const [mounts,setMounts]=useState(()=>mountDraft(source?.runtime?.mounts));
 const [assets,setAssets]=useState<AssetRow[]>(()=>source?.execution_mode==='host'?(source.host_runtime?.assets||[]).map((name:string)=>({name,target:''})):Object.entries(source?.runtime?.assets||{}).map(([name,target])=>({name,target:String(target)})));
 const settings=(mode==='host'?source?.host_runtime:source?.runtime)||{};
 const [environments,setEnvironments]=useState<Record<string,EnvironmentRow[]>>(()=>({docker:environmentDraft(source?.runtime),host:environmentDraft(source?.host_runtime)}));
 const [bindings,setBindings]=useState<PlatformBinding[]>(()=>structuredClone(source?.platform_bindings||[]) as PlatformBinding[]);
 const [workDir,setWorkDir]=useState(source?.runtime?.work_dir||'/tmp/mini-eval-work'),[outputDir,setOutputDir]=useState(source?.runtime?.output_dir||'/tmp/mini-eval-output');
 const errorRef=React.useRef<HTMLParagraphElement>(null);
 useEffect(()=>{if(error){errorRef.current?.focus({preventScroll:true});errorRef.current?.scrollIntoView({block:'center'});}},[error]);
 async function save(e:React.FormEvent<HTMLFormElement>){e.preventDefault();if(readOnly)return;setBusy(true);setError('');try{const f=new FormData(e.currentTarget),assetMap=assetValues(assets),{environment,overridable_env}=environmentValues(environments[mode]);
   const body:any={name:f.get('name'),version:f.get('version'),execution_mode:mode,integration_mode:kind==='client'&&ordinary?(taskResultReporting?'sdk-task-result':'direct'):kind==='client'?integration:'direct',command:JSON.parse(String(f.get('command'))),episode_reporting:kind==='client'&&!ordinary&&(reporting||integration==='sdk-command'),endpoint_exclusive:source?.endpoint_exclusive??false,protocols:source?.protocols||['http','https','ws','wss']};
   body.resource_requirements=resources;
   if(kind==='client')body.result_directory=String(f.get('result_directory')||'')||null;
   body.parameter_schema=parameterSchema(parameters);
   validateMappings(bindings,body.parameter_schema,body.command,environment,overridable_env);
   body.platform_bindings=bindings;body.task_type=taskType;
   if(mode==='docker'){body.image=f.get('image');body.runtime={...source?.runtime,interpreter:platformPython,command_mode:commandMode,environment,overridable_env,assets:assetMap,work_dir:f.get('work_dir'),output_dir:f.get('output_dir'),mounts:mountValues(mounts)};if(commandMode==='legacy')delete body.runtime.command_mode;delete body.runtime.working_directory;if(containerDirectory)body.runtime.working_directory=containerDirectory;}
   else{body.host_runtime={...source?.host_runtime,command_mode:commandMode,environment_name:f.get('environment_name'),environment_source:hostEnvironmentSource,episode_launch:kind==='client'?hostEpisodeLaunch:'whole_task',...(kind==='client'&&hostEpisodeLaunch==='per_episode'?{episode_error_policy:hostEpisodeErrorPolicy}:{episode_error_policy:'stop'}),environment,overridable_env,assets:assets.map(a=>a.name)};if(commandMode==='legacy')delete body.host_runtime.command_mode;delete body.host_runtime.working_directory;if(hostDirectory)body.host_runtime.working_directory=hostDirectory;}
   if(kind==='server')Object.assign(body,{port:Number(f.get('port')),protocol:f.get('protocol'),health_type:f.get('health_type'),health_path:f.get('health_path'),startup_timeout:Number(f.get('startup_timeout'))});
   if(kind==='server')body.readiness=customReadiness?readiness:null;
   await api('admin/'+kind+'-templates','POST',body);await onSave?.();
 }catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="panel template-form"><GuideButton topic="integration">业务如何接入：SDK 与样例</GuideButton><GuideButton topic="parameters">参数与目录配置说明</GuideButton>{!readOnly&&<h2>{source?.id.startsWith('example:')?'从样例注册模板':source?'复制为新版本':'注册运行模板'}</h2>}{error&&<p ref={errorRef} tabIndex={-1} role="alert" className="error">{error}</p>}{!readOnly&&<TemplateHelp kind={kind} mode={mode} integration={integration} taskType={taskType} taskResultReporting={taskResultReporting}/>}<form onSubmit={save}><fieldset disabled={readOnly} className="template-form-fields" aria-label={readOnly?'模板只读配置':'模板注册配置'}>
 <fieldset><legend>基础信息</legend><p className="muted">名称说明用途，例如 robotwin-client；版本例如 v1。修改已注册配置时创建新版本，历史任务继续使用原配置。</p><div className="row"><Field label="模板名称"><input name="name" defaultValue={source?.name} required maxLength={100}/></Field><Field label="版本"><input name="version" defaultValue={readOnly?source?.version:source?.id.startsWith('example:')?source.version:undefined} required maxLength={100} placeholder="例如 v1"/></Field></div></fieldset>
 <fieldset><legend>平台参数</legend><p className="muted">执行身份、GPU、推理地址和回合身份由平台注入；宿主机目录由机器配置解析。业务程序通过 MINI_EVAL_INPUT 读取任务输入。</p>
 <p>业务需要输出参数时，在动态参数映射选择“产物目录”，例如绑定命令行 --output-dir。也可读取 MINI_EVAL_OUTPUT_DIR，或在命令中使用 {'${output_dir}'}。每回合启动时为当前回合目录；整任务启动为公共目录。</p>
 {mode==='docker'?<div className="row"><Field label="容器内工作目录"><input name="work_dir" required value={workDir} onChange={e=>setWorkDir(e.target.value)}/></Field><Field label="容器内产物目录"><input name="output_dir" required value={outputDir} onChange={e=>setOutputDir(e.target.value)}/></Field></div>:<p>工作目录使用节点工作区；产物目录按节点存储配置生成，未配置独立产物根时沿用旧工作区。</p>}
 </fieldset>
 {kind==='client'&&<fieldset><legend>自定义结果浏览目录（可选）</legend><Field label="自定义结果浏览目录"><input name="result_directory" defaultValue={source?.result_directory||''} maxLength={2000} placeholder="例如 /shared/results/${business.scene}"/></Field><p>仅用于结果页只读浏览，不改变业务输出位置。可引用已声明业务字段如 {'${business.scene}'}；宿主机须在节点批准根内，容器须在持久挂载内，且 API 能访问同一共享存储。共享目录可能包含其他运行文件，不自动归属回合或清理。</p></fieldset>}
 <ResourceRequirements value={resources} onChange={setResources}/><fieldset><legend>业务参数</legend><p className="muted">这里定义可填写的字段，具体取值在集合任务或服务部署时填写。例如 scene（字符串）绑定命令行 --scene，平台会追加该参数；不要在命令中重复写同一绑定。任务名、回合数和种子在任务集合中配置。</p><details><summary>业务参数如何传给程序？</summary><p>输入文件：从 MINI_EVAL_INPUT 指向的 JSON 读取 business 字段。命令行：绑定名称填 --scene。环境变量：绑定名称填 SCENE。需要在提交时修改的字段，勾选“允许运行时覆盖”；不要使用 endpoint 等平台保留名称。</p></details><ParameterEditor value={parameters} onChange={setParameters}/></fieldset>
 <fieldset><legend>运行参数</legend>
 {kind==='client'&&<Field label="任务类型"><select value={taskType} onChange={e=>setTaskType(e.target.value)}><option value="evaluation">评测任务（回合与评测结果）</option><option value="command">普通命令（按退出码判定）</option></select></Field>}
 <Field label="执行方式"><select value={mode} onChange={e=>setMode(e.target.value)}><option value="docker">Docker 镜像运行</option><option value="host">宿主机运行</option></select></Field>
 {kind==='client'&&<Field label="协议接入方式"><select value={ordinary?'direct':integration} disabled={ordinary} onChange={e=>setIntegration(e.target.value)}><option value="direct">{ordinary?'直接执行业务命令':'业务程序直接实现评测协议'}</option>{!ordinary&&<option value="sdk-command">SDK + 每回合业务命令</option>}</select></Field>}
 {mode==='docker'?<Field label="Docker 镜像"><input name="image" required value={image} onChange={e=>setImage(e.target.value)} placeholder="registry/image:tag 或 digest"/></Field>:<><Field label="节点环境名称"><input name="environment_name" required pattern="[A-Za-z][A-Za-z0-9_]*" value={hostEnvironment} onChange={e=>setHostEnvironment(e.target.value)} placeholder="例如 simulation"/></Field><Field label="宿主机环境来源"><select value={hostEnvironmentSource} onChange={e=>setHostEnvironmentSource(e.target.value)}><option value="explicit">平台配置的显式环境（兼容旧模板）</option><option value="login">已批准业务账号的登录 shell 导出变量</option></select></Field>{kind==='client'&&<Field label="客户端回合启动"><select value={hostEpisodeLaunch} onChange={e=>{setHostEpisodeLaunch(e.target.value);if(e.target.value==='per_episode')setTaskResultReporting(false);}}><option value="whole_task">按原方式运行整个任务</option><option value="per_episode">每回合启动一次业务程序</option></select></Field>}{kind==='client'&&hostEpisodeLaunch==='per_episode'&&<><Field label="回合出错后的处理"><select value={hostEpisodeErrorPolicy} onChange={e=>setHostEpisodeErrorPolicy(e.target.value)}><option value="stop">立即停止（默认）</option><option value="continue">记录错误，继续后续回合</option></select></Field><p>继续模式保留每回合错误及退出码；存在执行错误时任务最终仍失败。需要 Worker 协议 17，取消与超时仍会停止任务。</p></>}<Field label="业务执行目录"><input value={hostDirectory} onChange={e=>setHostDirectory(e.target.value)} placeholder=".：批准代码目录；子目录：例如 eval；留空取决于命令模式" maxLength={2000}/></Field><p>执行环境中的 Python 与业务执行目录独立。目录相对于节点批准的代码根目录，也可填写其内部绝对路径；相对程序和配置路径从此目录解析。需要 Worker 协议 13。登录环境与逐回合启动需要协议 15。临时数据写工作目录，结果使用平台产物目录。</p><p>节点需批准并探测此宿主机环境。宿主机模式适合可信业务程序，不提供容器安全隔离。</p></>}
 <Field label="命令解析方式"><select value={commandMode} onChange={e=>setCommandMode(e.target.value)}><option value="exec">通用命令（推荐，需要 Worker 协议 14）</option><option value="legacy">兼容旧模板</option></select></Field>
 {mode==='docker'&&<><Field label="容器业务执行目录"><input value={containerDirectory} onChange={e=>setContainerDirectory(e.target.value)} placeholder="例如 /app；留空使用任务工作目录"/></Field><Field label="容器平台 Python"><input value={platformPython} onChange={e=>setPlatformPython(e.target.value)} placeholder="python3 或 /opt/platform/bin/python"/></Field><p>平台 Python 用于执行管理程序；业务可以使用其他 Python、Bash 或可执行文件。镜像需预装二者及其依赖。</p></>}
 <p>通用命令按“程序 + 参数”执行：支持绝对路径、./ 相对路径及 PATH 程序名。宿主机通用模式留空业务目录时使用批准代码目录，兼容模式使用任务目录。系统程序以业务账号权限运行；Shell 管道需显式使用 bash -lc。</p>
 <fieldset><legend>启动命令</legend><CommandEditor value={command} onChange={setCommand}/>
 <CommandMappings kind={kind} bindings={bindings} onBindings={setBindings} parameters={parameters} onParameters={setParameters}/><LaunchPreview command={command} bindings={bindings} parameters={parameters}/></fieldset>{ordinary?<><p className="hint">{mode==='host'&&hostEpisodeLaunch==='per_episode'?'普通命令每回合启动一次，按退出码记录完成进度；不计算评测目标成功率。':'普通命令每项执行一次，退出码 0 为程序执行成功，非零为失败。'}不适用于一直运行的服务。命令按程序和参数执行，不自动解释 Shell 管道。</p><label><input type="checkbox" checked={taskResultReporting} disabled={mode==='host'&&hostEpisodeLaunch==='per_episode'} onChange={e=>setTaskResultReporting(e.target.checked)}/>业务程序通过 SDK 上报一次任务结果</label><p>启用后，业务程序可调用 runner.sdk.report_task_result(success=True, metrics=metrics)；结果由 Worker 转发，业务程序不需要平台 Token。业务结果与程序退出码分别显示，未上报时按“未知”显示。</p></>:kind==='client'&&<><label><input type="checkbox" name="episode_reporting" checked={reporting||integration==='sdk-command'||(mode==='host'&&hostEpisodeLaunch==='per_episode')} disabled={integration==='sdk-command'||(mode==='host'&&hostEpisodeLaunch==='per_episode')} onChange={e=>setReporting(e.target.checked)}/>业务程序逐回合上报（SDK 命令或宿主机逐回合自动启用）</label><p>SDK 命令每次执行一个回合，通过 MINI_EVAL_RESULT 写入结果；直接协议选择宿主机逐回合时每次输出单回合 final，由平台汇总。参数支持 task_name、episode_id、episode_seed、endpoint、work_dir 等占位符。</p></>}
 <EnvironmentEditor rows={environments[mode]} onChange={rows=>setEnvironments({...environments,[mode]:rows})}/>
 <TemplateDirectories mode={mode} assets={assets} onAssets={setAssets} mounts={mounts} onMounts={setMounts}/>
 {kind==='server'&&<><fieldset><legend>服务监听与健康检查</legend><p>服务程序通过平台参数获取监听地址和端口。健康检查通过后，平台才会分配客户端。</p><div className="row"><Field label="服务端口"><input name="port" type="number" min={1024} max={65535} required defaultValue={source?.port||8000}/></Field><Field label="服务协议"><select name="protocol" defaultValue={source?.protocol||'http'}>{['http','https','ws','wss'].map(x=><option key={x}>{x}</option>)}</select></Field></div>
 <label><input type="checkbox" checked={customReadiness} onChange={e=>setCustomReadiness(e.target.checked)}/>自定义就绪检查（WebSocket、响应校验与独立端口）</label>
 <div hidden={customReadiness}><Field label="健康检查"><select name="health_type" defaultValue={source?.health_type||'http'}><option value="http">HTTP</option><option value="tcp">TCP</option></select></Field><Field label="健康检查路径"><input name="health_path" required defaultValue={source?.health_path||'/healthz'}/></Field><Field label="启动健康等待秒数"><input name="startup_timeout" type="number" min={5} max={1800} defaultValue={source?.startup_timeout||120} required/></Field><p>HTTP 检查要求响应 2xx；TCP 仅确认端口可连接。启动等待时间应覆盖模型加载耗时。</p></div>
 {customReadiness&&<ReadinessEditor value={readiness} onChange={setReadiness}/>}</fieldset></>}
 </fieldset></fieldset>{!readOnly&&<div className="row"><button disabled={busy} className="primary">注册此版本</button><button type="button" onClick={onClose}>取消</button></div>}</form></section>
}
