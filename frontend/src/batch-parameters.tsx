import React,{useEffect,useId,useRef,useState} from 'react';
import {api} from './api';

type Snapshot=Record<string,any>;
type Case={job_id:string;case_id:string;task_name:string;episodes?:number;seed?:number};
const missing='未记录';
const text=(v:unknown):string=>v===null||v===undefined?missing:typeof v==='string'?v:JSON.stringify(v);
const label=(v:unknown,labels:Record<string,string>)=>v===null||v===undefined?missing:labels[String(v)]||String(v);
const seconds=(v:unknown)=>v===0?'不限制':v===null||v===undefined?missing:`${v} 秒`;
const sources:Record<string,string>={template:'模板默认',suite:'集合公共参数',batch:'本次向导公共参数',case:'任务单独配置',override:'运行时覆盖',submission:'提交配置',pool:'资源池'};
function Fields({items}:{items:[string,unknown][]}){return <dl className="run-config-fields">{items.map(([name,value])=><React.Fragment key={name}><dt>{name}</dt><dd>{text(value)}</dd></React.Fragment>)}</dl>}
function JsonDetails({title,value}:{title:string;value:unknown}){return <details><summary>{title}</summary><pre>{value==null?missing:JSON.stringify(value,null,2)}</pre></details>}
function Parameters({values,origins}:{values?:Snapshot;origins?:Snapshot}){
 if(values==null)return <p>业务参数未记录。</p>;
 const entries=Object.entries(values);
 return entries.length?<div className="run-config-table"><table aria-label="业务参数最终值与来源"><thead><tr><th>参数</th><th>本任务最终值</th><th>来源</th></tr></thead><tbody>{entries.map(([key,value])=><tr key={key}><th scope="row">{key}</th><td><code>{text(value)}</code></td><td>{label(origins?.[key],sources)}</td></tr>)}</tbody></table></div>:<p>未配置业务参数。</p>;
}
function Mappings({template,parameters,task}:{template:Snapshot;parameters?:Snapshot;task?:Snapshot}){
 const rows=[
  ...(template.platform_bindings??[]).map((b:Snapshot)=>({...b,value:b.source?.startsWith('task.')?task?.[b.source.slice(5)]:undefined,business:false})),
  ...Object.entries(template.parameter_schema?.fields??{}).map(([name,field])=>({...((field as Snapshot).binding??{target:'input'}),source:'business.'+name,value:parameters?.[name],business:true})),
 ];
 if(!rows.length)return <p>{template.platform_bindings==null&&template.parameter_schema==null?'动态参数映射未记录。':'未配置动态参数映射。'}</p>;
 return <><h5>动态参数映射</h5><p>启动时按下表追加命令行参数或设置环境变量；输入 JSON 字段不追加到命令。下方值来自本次快照，基础命令不代表节点最终执行命令。</p>
 <div className="run-config-table"><table aria-label="动态参数映射"><thead><tr><th>来源</th><th>传递方式</th><th>程序接收名称</th><th>编码</th><th>快照值</th></tr></thead><tbody>{rows.map((b,i)=><tr key={i}>
 <th scope="row"><code>{b.source}</code></th><td>{label(b.target,{argv:'命令行参数',env:'环境变量',input:'输入 JSON'})}</td><td><code>{b.target==='input'?'—':text(b.name)}</code></td>
 <td>{b.target==='input'?'JSON':label(b.encoding??'literal',{literal:'单个值',json:'JSON 编码',flag:'为 true 时添加开关',repeat:'逐项重复'})}</td>
 <td><code>{b.value===undefined?(b.business?'未提供值（可选参数不追加）':b.source?.startsWith('task.')?'未记录':'启动时解析，未记录实际值'):text(b.value)}</code></td>
 </tr>)}</tbody></table></div></>;
}
function Runtime({template,environment,parameters,task}:{template?:Snapshot;environment?:Snapshot;parameters?:Snapshot;task?:Snapshot}){
 if(!template)return <p>模板快照未记录。</p>;
 const host=template.execution_mode==='host',runtime=host?template.host_runtime:template.runtime;
 return <><Fields items={[
  ['模板名称 / 版本',`${text(template.name)} / ${text(template.version)}`],
  ['执行方式',label(template.execution_mode,{host:'宿主机',docker:'容器'})],
  ['接入方式',template.task_type==='command'?'普通命令（按退出码判断）':label(template.integration_mode,{direct:'平台协议',sdk:'SDK'})],
  [host?'批准的宿主机环境':'业务镜像',host?runtime?.environment_name:template.image],
  ['业务工作目录',runtime?.working_directory||'未指定，按模板模式及节点批准目录解析'],
  ...(host?[
   ['环境来源',runtime?label(runtime.environment_source??'explicit',{login:'业务账号登录 Shell',explicit:'平台显式环境'}):missing],
   ['业务账号 / Python 绝对路径','未记录节点运行快照，不从当前节点配置推断'],
  ] as [string,unknown][]:[]),
 ]}/><h5>启动命令（模板快照，参数映射前）</h5><pre>{template.command==null?missing:JSON.stringify(template.command,null,2)}</pre>
 <JsonDetails title="模板环境变量（展开查看）" value={runtime?.environment??template.environment}/>
 <JsonDetails title="本次启动变量覆盖（展开查看）" value={environment}/>
 <Mappings template={template} parameters={parameters} task={task}/>
 <JsonDetails title="工作目录、资产与挂载配置" value={runtime?{work_dir:runtime.work_dir,output_dir:runtime.output_dir,assets:runtime.assets,mounts:runtime.mounts}:undefined}/></>;
}
function Resources({value,pool}:{value?:Snapshot;pool?:unknown}){
 const r=value||{};
 const hard=(unlimited:unknown,v:unknown,unit:string)=>unlimited===true?'不限制':v==null?missing:`${v} ${unit}${unlimited==null?'（旧配置默认限制）':''}`;
 return <Fields items={[
  ['资源池 ID',pool],['设备类型',label(r.accelerator_type,{gpu:'GPU',npu:'NPU'})],
  ['设备型号',r.required_gpu_model===null?'不限型号':r.required_gpu_model],
  ['设备分配方式',label(r.gpu_mode,{shared:'共享',exclusive:'独占'})],
  ['显存预约',r.requested_vram_gb==null?missing:`${r.requested_vram_gb} GiB`],
  ['CPU 调度预约',r.cpus==null?missing:`${r.cpus} 核`],['CPU 执行硬上限',hard(r.cpu_unlimited,r.cpus,'核')],
  ['内存调度预约',r.ram_gb==null?missing:`${r.ram_gb} GiB`],['内存执行硬上限',hard(r.memory_unlimited,r.ram_gb,'GiB')],
 ]}/>;
}
function useSnapshot(path:string|null){
 const [reload,setReload]=useState(0),[state,setState]=useState<{path:string;data?:Snapshot;error?:string}|null>(null);
 useEffect(()=>{let active=true;setState(null);if(path)void api<Snapshot>(path).then(data=>{if(active)setState({path,data})}).catch(e=>{if(active)setState({path,error:String(e)})});return()=>{active=false}},[path,reload]);
 return {data:state?.path===path?state?.data:undefined,error:state?.path===path?state?.error:undefined,retry:()=>setReload(v=>v+1)};
}
function Services({batchId,orchestrated,spec,onServices}:{batchId:string;orchestrated?:boolean;spec:Snapshot;onServices:()=>void}){
 const {data,error,retry}=useSnapshot(orchestrated&&spec.service_mode==='managed'?'orchestrations/'+batchId:null);
 const mode=spec.service_mode,orch=data?.orchestration;
 return <><Fields items={[["服务来源",label(mode,{none:'无需服务',external:'外部服务',managed:'平台托管'})],['本任务关联服务',spec.service_keys?.join(', ')||'未指定']]} />
 {mode==='none'&&<p>本任务无需服务端。</p>}
 {mode==='external'&&<><h5>提交时的候选地址</h5>{spec.endpoints?.length?<ul>{spec.endpoints.map((e:Snapshot,i:number)=><li key={i}>{e.url} · {label(e.mode,{shared:'共享访问',exclusive:'独占访问'})}</li>)}</ul>:spec.endpoint?<p>{spec.endpoint} · {label(spec.endpoint_mode,{shared:'共享访问',exclusive:'独占访问'})}</p>:<p>候选地址未记录。</p>}</>}
 {mode==='managed'&&<>{!orchestrated?<p>服务编排快照未记录。</p>:error?<p role="alert">服务配置读取失败：{error} <button onClick={retry}>重试服务配置</button></p>:!orch?<p role="status">正在读取服务配置…</p>:<>
 <Fields items={[["批次结束后的服务",orch.stop_after_batch===true?'自动停止':orch.stop_after_batch===false?'保留运行':missing]]}/>
 {!orch.definitions?.length&&<p>服务定义未记录。</p>}
 {orch.definitions?.map((d:Snapshot,i:number)=><article className="run-config-service" aria-label={'服务配置 '+(d.key??i)} key={d.key??i}>
 <h5>{d.key} · {text(d.template?.name)} / {text(d.template?.version)}</h5>
 <Fields items={[
  ['服务实例数',d.instance_count??(d.max_instances!=null?`旧配置：上限 ${d.max_instances}，按需启动`:missing)],
  ['客户端访问方式',label(d.mode,{shared:'共享访问（不等于设备共享）',exclusive:'独占访问'})],
  ['固定设备槽位',d.slot_id===null?'自动分配':d.slot_id],
  ['服务协议',d.template?.protocol],['模板业务端口',d.template?.container_port??d.template?.port],
 ]}/><Runtime template={d.template} environment={d.environment} parameters={d.parameters}/>
 <h5>服务业务参数</h5><Parameters values={d.parameters} origins={d.parameter_sources}/>
 <h5>服务资源预约与限制</h5><Resources value={d.resources} pool={d.pool_id}/>
 <h5>就绪检查配置</h5>{d.template?.readiness?<><Fields items={[
 ['探测方式',d.template.readiness.kind],['探测端口',d.template.readiness.port==='business'?'使用分配的业务端口':d.template.readiness.port],
 ['探测路径',d.template.readiness.path],['单次超时',seconds(d.template.readiness.timeout)],['探测间隔',seconds(d.template.readiness.interval)],
 ['启动就绪时限',seconds(d.template.readiness.startup_timeout)],['探测过期时限',seconds(d.template.readiness.stale_after)],
 ['连续成功阈值',d.template.readiness.success_threshold],['连续失败阈值',d.template.readiness.failure_threshold],
 ]}/><JsonDetails title="完整就绪检查配置（展开查看）" value={d.template.readiness}/></>:<><p>旧版健康检查或未记录就绪模板。</p><JsonDetails title="旧版健康检查配置" value={d.template?{health_type:d.template.health_type,health_path:d.template.health_path,startup_timeout:d.template.startup_timeout}:undefined}/></>}
 </article>)}
 </>}</>}
 {mode==='managed'&&<button onClick={onServices}>查看实际服务实例、节点、加速卡与日志</button>}
 </>;
}
export function BatchParameters({batchId,suiteId,cases,maxConcurrent,orchestrated,onServices}:{batchId:string;suiteId?:string|null;cases:Case[];maxConcurrent:number;orchestrated?:boolean;onServices:()=>void}) {
 const [choice,setChoice]=useState('');
 const selected=cases.some(c=>c.job_id===choice)?choice:cases[0]?.job_id||'';
 const {data,error,retry}=useSnapshot(selected?'jobs/'+selected:null);
 const spec=data?.client_spec,template=spec?.template,host=template?.host_runtime;
 const sections=useRef<(HTMLElement|null)[]>([]),prefix=useId();
 const names=['任务集合','客户端','服务端','资源与执行限制'];
 const current=cases.find(c=>c.job_id===selected);
 return <section className="batch-parameters" aria-label="本次运行配置"><h3>本次运行配置</h3>
 <p>按提交向导查看已保存的运行快照。模板后续修改不会改变本次配置；不同任务可能有不同参数。</p>
 <nav className="run-config-nav" aria-label="运行配置分组">{names.map((name,i)=><button key={name} onClick={()=>{sections.current[i]?.scrollIntoView({block:'start'});sections.current[i]?.focus()}}>{i+1}. {name}</button>)}</nav>
 {!cases.length?<p>此批次没有任务。</p>:<label>查看任务配置<select value={selected} onChange={e=>setChoice(e.target.value)}>{cases.map(c=><option key={c.job_id} value={c.job_id}>{c.case_id} · {c.task_name}</option>)}</select></label>}
 <section className="run-config-section" ref={el=>{sections.current[0]=el}} tabIndex={-1} aria-labelledby={prefix+'-0'}><h4 id={prefix+'-0'}>1. 任务集合</h4>
 <Fields items={[
 ['集合 ID',suiteId===null?'自定义任务清单':suiteId],['集合名称 / 版本','未记录历史快照'],
 ['本批次任务数',cases.length],['当前任务',current?`${current.case_id} · ${current.task_name}`:missing],
 ['回合数',data?.episodes??current?.episodes],['种子',spec?.seed??current?.seed],
 ]}/></section>
 {selected&&!data&&!error&&<p role="status">正在读取任务配置…</p>}
 {error&&<p role="alert">任务配置读取失败：{error} <button onClick={retry}>重试任务配置</button></p>}
 {data&&!spec&&<p>此历史任务未保存客户端运行快照。</p>}
 {spec&&<><section className="run-config-section" ref={el=>{sections.current[1]=el}} tabIndex={-1} aria-labelledby={prefix+'-1'}><h4 id={prefix+'-1'}>2. 客户端</h4>
 <Runtime template={template} environment={spec.environment} parameters={spec.parameters} task={{...spec,episodes:data?.episodes}}/>
 <Fields items={[
 ['回合启动方式',template?.execution_mode==='host'&&host?label(host.episode_launch??'whole_task',{whole_task:'整任务启动（SDK 可在内部逐回合执行）',per_episode:'每回合启动'}):'按模板入口执行'],
 ['回合失败策略',template?.execution_mode==='host'&&host?.episode_launch==='per_episode'?label(host.episode_error_policy??'stop',{stop:'出错立即停止',continue:'记录错误继续；最终仍标记失败'}):'由接入协议与业务程序决定'],
 ]}/><h5>业务参数最终值与来源</h5><Parameters values={spec.parameters} origins={spec.parameter_sources}/>
 <p className="hint">普通参数优先级：模板默认 → 集合公共参数 → 向导公共参数 → 任务单独配置。来源未记录时不推断。</p>
 </section>
 <section className="run-config-section" ref={el=>{sections.current[2]=el}} tabIndex={-1} aria-labelledby={prefix+'-2'}><h4 id={prefix+'-2'}>3. 服务端</h4>
 <Services batchId={batchId} orchestrated={orchestrated} spec={spec} onServices={onServices}/>
 <h5>当前任务实际分配</h5><p>最近执行尝试的服务地址：{data?.attempts?.at(-1)?.endpoint||'未分配或未记录'}。候选地址不代表已分配。</p>
 </section>
 <section className="run-config-section" ref={el=>{sections.current[3]=el}} tabIndex={-1} aria-labelledby={prefix+'-3'}><h4 id={prefix+'-3'}>4. 资源与执行限制</h4>
 <Resources value={spec.resources??{gpu_mode:data?.gpu_mode,requested_vram_gb:data?.requested_vram_gb}} pool={data?.pool_id}/>
 <Fields items={[
 ['单任务总执行时限',seconds(spec.timeout_seconds)],['客户端请求超时',seconds(spec.request_timeout_seconds)],['批次最大并发任务数',maxConcurrent],
 ]}/><p>总执行时限由当前任务全部回合共用；其他任务分别计时。不限制执行时长仍需心跳续租。CPU/内存硬上限不限时，调度仍按预约量分配。</p>
 </section><JsonDetails title="完整客户端运行快照" value={spec}/></>}
 <p className="hint">提交时预检结果未保存，不能作为历史报告回放。实时等待原因请查看“任务与结果”；服务运行情况请查看“服务端与关联”。</p>
 </section>;
}
