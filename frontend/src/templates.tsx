import React,{useEffect,useState} from 'react';
import {api} from './api';
import {Field} from './form-fields';
import {ParameterEditor,parameterDraft,parameterSchema} from './parameter-editor';

type Template={parameter_schema?:Record<string,unknown>|null;id:string;name:string;version:string;image:string;execution_mode?:string;integration_mode?:string;command:string[];enabled:boolean;episode_reporting:boolean;protocols:string[];runtime:any;host_runtime:any;port?:number;protocol?:string;health_type?:string;health_path?:string;startup_timeout?:number};
export function Templates(){
 const [kind,setKind]=useState('client'),[rows,setRows]=useState<Template[]>([]),[edit,setEdit]=useState<Template|null|undefined>(undefined),[error,setError]=useState('');
 const refresh=()=>api<Template[]>(kind==='client'?'client-templates':'admin/server-templates').then(setRows);
 useEffect(()=>{setEdit(undefined);setError('');void refresh().catch(e=>setError(String(e)))},[kind]);
 async function act(t:Template,action:'state'|'archive'){try{setError('');await api('admin/'+kind+'-templates/'+t.id,action==='state'?'PATCH':'DELETE',action==='state'?{enabled:!t.enabled}:undefined);await refresh()}catch(e){setError(String(e))}}
 return <><div className="tabs" role="tablist" aria-label="模板类型">{[['client','客户端模板'],['server','服务端模板']].map(([id,label])=><button role="tab" aria-selected={kind===id} key={id} onClick={()=>setKind(id)}>{label}</button>)}</div>
 <section className="panel"><h2>{kind==='client'?'客户端':'服务端'}运行模板</h2><p>模板版本固定执行配置；修改时复制为新版本，已提交任务保留原快照。</p>{error&&<p role="alert" className="error">{error}</p>}<button onClick={()=>setEdit(null)}>注册模板</button>
 <table><thead><tr><th>名称 / 版本</th><th>执行方式</th><th>接入方式</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows.map(t=><tr key={t.id}><td>{t.name} / {t.version}</td><td>{t.execution_mode==='host'?'宿主机':'Docker'}</td><td>{kind==='server'?'长期服务':t.integration_mode==='sdk-command'?'SDK + 业务命令':'直接评测协议'}</td><td>{t.enabled?'启用':'停用'}</td><td><details><summary>查看配置</summary><pre>{JSON.stringify(t,null,2)}</pre></details><button onClick={()=>setEdit(t)}>复制新版本</button><button onClick={()=>void act(t,'state')}>{t.enabled?'停用':'启用'}</button><button onClick={()=>{if(confirm('归档此模板？已有任务快照保留。'))void act(t,'archive')}}>归档</button></td></tr>)}</tbody></table></section>
 {edit!==undefined&&<TemplateForm key={kind+':'+(edit?.id||'new')} kind={kind} source={edit} onClose={()=>setEdit(undefined)} onSave={async()=>{await refresh();setEdit(undefined)}}/>}</>;
}
function TemplateForm({kind,source,onSave,onClose}:{kind:string;source:Template|null;onSave:()=>Promise<void>;onClose:()=>void}){
 const [mode,setMode]=useState(source?.execution_mode||'docker'),[integration,setIntegration]=useState(source?.integration_mode||'direct'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [parameters,setParameters]=useState(()=>parameterDraft(source?.parameter_schema));
 const settings=source?.host_runtime||source?.runtime||{};
 const [workDir,setWorkDir]=useState(source?.runtime?.work_dir||'/tmp/mini-eval-work'),[outputDir,setOutputDir]=useState(source?.runtime?.output_dir||'/tmp/mini-eval-output');
 const errorRef=React.useRef<HTMLParagraphElement>(null);
 useEffect(()=>{if(error){errorRef.current?.focus({preventScroll:true});errorRef.current?.scrollIntoView({block:'center'});}},[error]);
 async function save(e:React.FormEvent<HTMLFormElement>){e.preventDefault();setBusy(true);setError('');try{const f=new FormData(e.currentTarget),environment=JSON.parse(String(f.get('environment'))),assets=JSON.parse(String(f.get('assets'))),overridable_env=String(f.get('overrides')).split(',').map(x=>x.trim()).filter(Boolean);
   const body:any={name:f.get('name'),version:f.get('version'),execution_mode:mode,integration_mode:kind==='client'?integration:'direct',command:JSON.parse(String(f.get('command'))),episode_reporting:f.has('episode_reporting')||integration==='sdk-command',protocols:['http','https','ws','wss']};
   body.parameter_schema=parameterSchema(parameters);
   if(mode==='docker'){body.image=f.get('image');body.runtime={interpreter:'python3',environment,overridable_env,assets,work_dir:f.get('work_dir'),output_dir:f.get('output_dir')};}
   else{body.host_runtime={environment_name:f.get('environment_name'),environment,overridable_env,assets};}
   if(kind==='server')Object.assign(body,{port:Number(f.get('port')),protocol:f.get('protocol'),health_type:f.get('health_type'),health_path:f.get('health_path'),startup_timeout:Number(f.get('startup_timeout'))});
   await api('admin/'+kind+'-templates','POST',body);await onSave();
 }catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="panel"><h2>{source?'复制为新版本':'注册运行模板'}</h2>{error&&<p ref={errorRef} tabIndex={-1} role="alert" className="error">{error}</p>}<form onSubmit={save}>
 <fieldset><legend>基础信息</legend><div className="row"><Field label="模板名称"><input name="name" defaultValue={source?.name} required maxLength={100}/></Field><Field label="版本"><input name="version" required maxLength={100} placeholder="例如 v1"/></Field></div></fieldset>
 <fieldset><legend>平台参数</legend><p className="muted">执行身份、GPU、推理地址和回合身份由平台注入；宿主机目录由机器配置解析。业务程序通过 MINI_EVAL_INPUT 读取任务输入。</p>
 {mode==='docker'?<div className="row"><Field label="容器内工作目录"><input name="work_dir" required value={workDir} onChange={e=>setWorkDir(e.target.value)}/></Field><Field label="容器内输出目录"><input name="output_dir" required value={outputDir} onChange={e=>setOutputDir(e.target.value)}/></Field></div>:<p>工作目录和输出目录使用该节点工作区下的独立执行目录。</p>}
 </fieldset>
 <fieldset><legend>业务参数</legend><ParameterEditor value={parameters} onChange={setParameters}/></fieldset>
 <fieldset><legend>运行参数</legend>
 <Field label="执行方式"><select value={mode} onChange={e=>setMode(e.target.value)}><option value="docker">Docker 镜像运行</option><option value="host">宿主机运行</option></select></Field>
 {kind==='client'&&<Field label="协议接入方式"><select value={integration} onChange={e=>setIntegration(e.target.value)}><option value="direct">业务程序直接实现评测协议</option><option value="sdk-command">SDK + 每回合业务命令</option></select></Field>}
 {mode==='docker'?<Field label="Docker 镜像"><input name="image" required defaultValue={source?.image} placeholder="registry/image:tag 或 digest"/></Field>:<><Field label="节点环境名称"><input name="environment_name" required pattern="[A-Za-z][A-Za-z0-9_]*" defaultValue={settings.environment_name} placeholder="例如 simulation"/></Field><p>节点需批准并探测此宿主机环境。宿主机模式适合可信业务程序，不提供容器安全隔离。</p></>}
 <Field label="业务命令（JSON argv 数组）"><textarea name="command" required rows={4} defaultValue={JSON.stringify(source?.command||['python3','business.py'],null,2)}/></Field>
 {kind==='client'&&<><label><input type="checkbox" name="episode_reporting" defaultChecked={source?.episode_reporting??true}/>业务程序逐回合上报（SDK 命令模式自动启用）</label><p>SDK 命令每次执行一个回合，通过 MINI_EVAL_RESULT 写入结果；直接协议程序负责回合事件和最终汇总。参数支持 task_name、episode_id、episode_seed、endpoint、work_dir 等占位符。</p></>}
 <Field label="环境变量（JSON 对象）"><textarea name="environment" rows={3} defaultValue={JSON.stringify(settings.environment||{},null,2)}/></Field>
 <Field label="允许提交时覆盖的变量名（逗号分隔）"><input name="overrides" defaultValue={(settings.overridable_env||[]).join(',')}/></Field>
 <Field label={mode==='docker'?'逻辑资产 → 容器路径（JSON 对象）':'逻辑资产名称（JSON 数组）'}><textarea key={mode} name="assets" rows={3} defaultValue={JSON.stringify(source?.execution_mode===mode?settings.assets||(mode==='host'?[]:{}):mode==='host'?[]:{},null,2)}/></Field>
 {kind==='server'&&<><div className="row"><Field label="服务端口"><input name="port" type="number" min={1024} max={65535} required defaultValue={source?.port||8000}/></Field><Field label="服务协议"><select name="protocol" defaultValue={source?.protocol||'http'}>{['http','https','ws','wss'].map(x=><option key={x}>{x}</option>)}</select></Field><Field label="健康检查"><select name="health_type" defaultValue={source?.health_type||'http'}><option value="http">HTTP</option><option value="tcp">TCP</option></select></Field></div><Field label="健康检查路径"><input name="health_path" required defaultValue={source?.health_path||'/healthz'}/></Field><Field label="启动健康等待秒数"><input name="startup_timeout" type="number" min={5} max={1800} defaultValue={source?.startup_timeout||120} required/></Field></>}
 </fieldset><div className="row"><button disabled={busy} className="primary">注册此版本</button><button type="button" onClick={onClose}>取消</button></div></form></section>
}
