import React, {useEffect, useRef, useState} from 'react';
import {api} from './api';

const example = {interpreter:'python3', environment:{TASK:'${task_name}', ASSETS:'${assets:robotwin}'}, overridable_env:['LOG_LEVEL'], work_dir:'/workspace/eval', output_dir:'/results', assets:{robotwin:'/data/robotwin'}};
export function ImageClients({onChange}:{onChange:()=>void}) {
  const [runtime,setRuntime]=useState(JSON.stringify(example,null,2)), [command,setCommand]=useState('["python3", "/app/evaluate.py", "--task", "${task_name}"]');
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setError('');setMessage('');setBusy(true);
    try {
      const f=new FormData(e.currentTarget);
      const t=await api<{name:string;version:string}>('client-templates','POST',{name:f.get('name'),version:f.get('version'),image:f.get('image'),
        command:JSON.parse(command),runtime:JSON.parse(runtime),protocols:[f.get('protocol')],episode_reporting:f.get('episodes')==='on'});
      setMessage(`客户端 ${t.name} / ${t.version} 已注册。可在集合管理中选择；节点需预置并批准镜像和资产。`);onChange();
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <section className="panel"><h2>注册自己的客户端镜像</h2>
    <p>镜像需包含 Python 3.11+。平台注入执行适配层；客户端仍需按评测协议输出指标和回合事件。模板版本不可修改，调整配置请注册新版本。</p>
    {error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    <form onSubmit={submit}><div className="submit-grid">
      <label>镜像客户端名称<input name="name" required maxLength={100}/></label>
      <label>镜像客户端版本<input name="version" required maxLength={100} defaultValue="1"/></label>
      <label>客户端镜像地址<input name="image" required maxLength={500} placeholder="registry/team/client:version"/></label>
      <label>推理协议<select name="protocol" aria-label="推理协议">{['http','https','ws','wss'].map(p=><option key={p}>{p}</option>)}</select></label>
    </div>
    <label>启动命令 JSON 数组<textarea aria-label="启动命令 JSON 数组" rows={3} value={command} onChange={e=>setCommand(e.target.value)} required/></label>
    <label>运行配置 JSON<textarea aria-label="运行配置 JSON" rows={12} value={runtime} onChange={e=>setRuntime(e.target.value)} required/></label>
    <p>environment 为默认变量，overridable_env 为运行时允许覆盖的键。assets 是“逻辑名 → 容器路径”；宿主路径由管理员在节点配置。变量以明文保存，请勿填写密码或令牌。</p>
    <p>可用占位符：{'${task_name}、${episodes}、${seed}、${endpoint}、${input_path}、${work_dir}、${output_dir}、${assets:逻辑名}'}。命令逐参数传递，不自动执行 shell。</p>
    <label><input type="checkbox" name="episodes"/>客户端按协议上报逐回合事件</label>
    <button disabled={busy}>{busy?'注册中…':'注册镜像客户端'}</button></form>
  </section>;
}

type RuntimeConfig={images:string[];assets:Record<string,string>};
type Report={applied_revision:number;effective:RuntimeConfig;images:Record<string,{ready:boolean;error:string|null}>;assets:Record<string,{ready:boolean;error:string|null}>};
type NodeRuntime={revision:number;config:RuntimeConfig|null;slots:{slot_id:string;gpu_index:number;ready:boolean;report:Report|null}[]};
export function RuntimeSettings({id,name}:{id:string;name:string}) {
  const [data,setData]=useState<NodeRuntime|null>(null),[images,setImages]=useState(''),[assets,setAssets]=useState('{}');
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const dirty=useRef(false),base=useRef<number|null>(null);
  const endpoint=`admin/nodes/${id}/runtime`;
  function receive(value:NodeRuntime,reset=false) {
    if(!Array.isArray(value.slots)) return;
    setData(value);
    if(reset||!dirty.current){const config=value.config||value.slots.find(x=>x.report)?.report?.effective||{images:[],assets:{}};
      setImages(config.images.join('\n'));setAssets(JSON.stringify(config.assets,null,2));base.current=value.revision;dirty.current=false;}
  }
  useEffect(()=>{let active=true;const load=()=>api<NodeRuntime>(endpoint).then(v=>{if(active)receive(v);}).catch(e=>{if(active)setError(String(e));});void load();const t=setInterval(load,5000);return()=>{active=false;clearInterval(t);};},[endpoint]);
  async function save(restore=false) {
    if(base.current===null)return;setBusy(true);setError('');setMessage('');
    try{const value=await api<NodeRuntime>(endpoint,'PUT',{expected_revision:base.current,config:restore?null:{images:images.split('\n').map(x=>x.trim()).filter(Boolean),assets:JSON.parse(assets)}});
      receive(value,true);setMessage(`版本 ${value.revision} 已保存，等待 Worker 确认和资源探测。`);
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <details className="panel"><summary>{name} 镜像与逻辑资产</summary>
    <p>预先在机器上安装镜像、准备资产目录。这里只批准镜像和配置映射，不会自动拉取镜像或下载资产。正在运行的任务保留旧配置；空闲后切换。</p>
    {error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    {data&&<><p>期望版本 {data.revision} · {data.config?'平台配置':'本机初始配置'}</p>
      <label>批准镜像（每行一个）<textarea aria-label={`${name} 批准镜像`} rows={4} value={images} onChange={e=>{dirty.current=true;setImages(e.target.value);}}/></label>
      <label>逻辑资产宿主映射 JSON<textarea aria-label={`${name} 逻辑资产宿主映射`} rows={5} value={assets} onChange={e=>{dirty.current=true;setAssets(e.target.value);}}/></label>
      <button disabled={busy} onClick={()=>void save()}>保存镜像与资产配置</button>{' '}
      <button disabled={busy} onClick={()=>void save(true)}>恢复本机运行配置</button>{' '}
      <button disabled={busy} onClick={()=>receive(data,true)}>重新载入运行配置（放弃草稿）</button>
      {data.slots.map(s=><article key={s.slot_id}><h4>GPU {s.gpu_index} · {s.ready?'配置已生效':'等待生效 / 需升级 / 报告过期'} · 已确认版本 {s.report?.applied_revision??'未知'}</h4>
        {(['images','assets'] as const).map(kind=><ul key={kind}>{Object.entries(s.report?.[kind]||{}).map(([key,item])=><li key={key}>{kind==='images'?'镜像':'资产'} {key}：{item.ready?'就绪':'未就绪'}{item.error&&` — ${item.error}`}</li>)}</ul>)}
      </article>)}
    </>}
  </details>;
}
export function RuntimeReadiness({id}:{id:string}) {
  const [rows,setRows]=useState<{slot_id:string;node_name:string;gpu_index:number;reasons:string[]}[]>([]),[error,setError]=useState('');
  useEffect(()=>{let active=true;const load=()=>api<typeof rows>(`jobs/${id}/runtime-readiness`).then(v=>{if(active){setRows(v);setError('');}}).catch(e=>{if(active)setError(String(e));});void load();const t=setInterval(load,5000);return()=>{active=false;clearInterval(t);};},[id]);
  return <details><summary>镜像与资产就绪情况</summary><p>这里只检查运行环境；并发配额、GPU 和磁盘等条件仍参与调度。</p>{error&&<p role="alert">{error}</p>}{!rows.length&&<p>资源池尚无节点。</p>}<ul>{rows.map(r=><li key={r.slot_id}>{r.node_name} / GPU {r.gpu_index}：{r.reasons.join('；')||'运行环境就绪'}</li>)}</ul></details>;
}
