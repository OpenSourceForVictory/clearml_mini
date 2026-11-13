import React, {useEffect, useState} from 'react';
import {api} from './api';
import {Memberships} from './memberships';
import {StorageSettings} from './storage';
import {RuntimeSettings} from './image-runtime';
import {ExecutionDataSettings} from './retention';
import {ClientTemplates} from './cs';

type Machine={id:string;node_name:string;roles:string[];enabled:boolean;status:string;management_revision:number;revoked_at:string|null;max_concurrent:number;cpu_capacity:number;ram_gb_capacity:number;gpu_count:number;held_executions:number};
type Pool={id:string;name:string;enabled:boolean;gpu_model:string;allow_shared:boolean;max_tasks_per_gpu:number;vram_reserve_gb:number;max_gpu_utilization:number;min_vram_gb:number;cpu_per_job:number;ram_gb_per_job:number;member_count:number;pending_jobs:number;active_jobs:number};
type Account={id:string;username:string;role:string;active:boolean};
export function Field({label,children}:{label:string;children:React.ReactNode}) {return <label className="field">{label}{children}</label>}
const status:Record<string,string>={online:'在线',offline:'离线',waiting:'等待连接',disabled:'已停用',unmanaged:'已解除纳管'};
export function Admin() {
  const [tab,setTab]=useState('machines');
  return <><div className="page-title"><div><p className="eyebrow">ADMINISTRATION</p><h1>管理设置</h1><p className="muted">机器接入、资源组织、运行模板与团队账号。</p></div></div>
    <div className="tabs" role="tablist" aria-label="管理分类">{[['machines','机器管理'],['pools','资源池管理'],['templates','运行模板管理'],['accounts','账号管理']].map(([id,label])=><button key={id} role="tab" aria-selected={tab===id} onClick={()=>setTab(id)}>{label}</button>)}</div>
    {tab==='machines'&&<Machines/>}{tab==='pools'&&<Pools/>}{tab==='templates'&&<ClientTemplates/>}{tab==='accounts'&&<Accounts/>}</>;
}
export function Machines() {
  const [nodes,setNodes]=useState<Machine[]>([]),[role,setRole]=useState('client'),[selected,setSelected]=useState(''),[register,setRegister]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const refresh=()=>api<Machine[]>('admin/nodes').then(values=>setNodes(values.map(n=>({...n,management_revision:n.management_revision??0,roles:n.roles??['client'],gpu_count:n.gpu_count??0,held_executions:n.held_executions??0,status:n.status??(n.enabled?'waiting':'disabled')}))));
  useEffect(()=>{void refresh().catch(e=>setError(String(e)));const t=setInterval(()=>void refresh().catch(e=>setError(String(e))),5000);return()=>clearInterval(t)},[]);
  const node=nodes.find(n=>n.id===selected);
  async function act(path:string,method:string,body?:unknown) {setBusy(true);setError('');try{await api(path,method,body);setMessage('操作已完成');}catch(e){setError(String(e));}finally{await refresh().catch(e=>setError(String(e)));setBusy(false)}}
  return <><div className="tabs" role="tablist" aria-label="机器类型">{[['client','客户端机器'],['server','服务端机器']].map(([id,label])=><button role="tab" key={id} aria-selected={role===id} onClick={()=>{setRole(id);setSelected('');setRegister(false)}}>{label}</button>)}</div>
    {error&&<p role="alert" className="error">{error}</p>}{message&&<p role="status">{message}</p>}
    <div className="row"><button onClick={()=>{setRegister(!register);setSelected('')}}>{register?'返回机器列表':'注册新机器'}</button></div>
    {register?<ManualRegistration role={role} onChange={refresh}/>:<section className="panel"><h2>已注册{role==='client'?'客户端':'服务端'}机器</h2><p>同一机器可以兼任两种角色，资源额度按物理机器合计。</p>
      <table><thead><tr><th>机器</th><th>角色</th><th>GPU / 持有执行</th><th>状态</th><th>管理</th></tr></thead><tbody>{nodes.filter(n=>n.roles.includes(role)).map(n=><tr key={n.id}><td>{n.node_name}</td><td>{n.roles.map(r=>r==='client'?'客户端':'服务端').join('、')}</td><td>{n.gpu_count} / {n.held_executions}</td><td>{status[n.status]}</td><td><button onClick={()=>setSelected(n.id)}>查看配置</button></td></tr>)}</tbody></table>
      {!nodes.some(n=>n.roles.includes(role))&&<p>暂无此类型机器，可注册新机器或在已有机器详情中增加角色。</p>}</section>}
    {node&&!register&&<section className="panel"><div className="row"><h2>{node.node_name} · 配置详情</h2><button onClick={()=>setSelected('')}>关闭详情</button></div>
      <p>{status[node.status]} · 角色变更和解除纳管需要等待执行清理完成。</p>
      {!node.revoked_at&&<><form key={node.id+':'+node.management_revision} onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);void act('admin/nodes/'+node.id,'PATCH',{expected_revision:node.management_revision,roles:f.getAll('roles'),max_concurrent:Number(f.get('max_concurrent')),cpu_capacity:Number(f.get('cpu_capacity')),ram_gb_capacity:Number(f.get('ram_gb_capacity'))})}}>
        <fieldset><legend>机器角色</legend>{[['client','客户端'],['server','服务端']].map(([r,label])=><label key={r}><input type="checkbox" name="roles" value={r} defaultChecked={node.roles.includes(r)}/>{label}</label>)}</fieldset>
        <div className="row"><Field label="并发上限"><input name="max_concurrent" type="number" min={1} defaultValue={node.max_concurrent} required/></Field><Field label="CPU 核数"><input name="cpu_capacity" type="number" min={1} defaultValue={node.cpu_capacity} required/></Field><Field label="内存 GiB"><input name="ram_gb_capacity" type="number" min={1} defaultValue={node.ram_gb_capacity} required/></Field></div><button disabled={busy}>保存配置</button></form>
        <div className="row"><button disabled={busy} onClick={()=>void act('admin/nodes/'+node.id,'PATCH',{enabled:!node.enabled,expected_revision:node.management_revision})}>{node.enabled?'停用（停止接新任务）':'启用'}</button>
        <button disabled={busy} onClick={()=>{if(confirm('解除 '+node.node_name+' 纳管将撤销凭据。历史数据保留；持有执行时先停用并等待清理。继续？'))void act('admin/nodes/'+node.id+'/unmanage','POST',{expected_revision:node.management_revision})}}>解除纳管</button></div>
        <StorageSettings id={node.id} name={node.node_name}/><RuntimeSettings id={node.id} name={node.node_name}/><ExecutionDataSettings id={node.id} name={node.node_name}/></>}
    </section>}</>;
}
function ManualRegistration({role,onChange}:{role:string;onChange:()=>Promise<unknown>}) {
  const [credential,setCredential]=useState<{token:string;node_name:string}|null>(null),[error,setError]=useState('');
  return <section className="panel"><h2>手动接入配置</h2><p>生成一次性展示的 Worker 凭据，再通过安装器接入。新机器默认不加入资源池。</p>{error&&<p role="alert">{error}</p>}
    <form onSubmit={async e=>{e.preventDefault();try{const f=new FormData(e.currentTarget);setCredential(await api('admin/worker-credentials','POST',{node_name:f.get('name'),roles:[role]}));await onChange()}catch(e){setError(String(e))}}}><Field label="机器名称"><input name="name" pattern="[a-zA-Z0-9_.-]+" required/></Field><button>生成 Worker 凭据</button></form>
    {credential&&<form onSubmit={async e=>{e.preventDefault();const f=new FormData(e.currentTarget);try{const health=await api<{runner_image:string}>('admin/health');const url=URL.createObjectURL(new Blob([JSON.stringify({api_url:f.get('url'),token:credential.token,node_name:credential.node_name,pool_id:null,gpu_index:0,mock:false,runner_image:health.runner_image},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='worker-'+credential.node_name+'.json';a.click();URL.revokeObjectURL(url)}catch(e){setError(String(e))}}}><Field label="Worker 可访问的 API 地址"><input name="url" type="url" required placeholder="http://平台内网IP:18000"/></Field><p>浏览器使用 SSH 转发时，不要填浏览器的 localhost 地址。</p><button>下载接入配置</button></form>}
  </section>;
}
function PoolFields({pool}:{pool?:Pool}) {return <><Field label="名称"><input name="name" defaultValue={pool?.name} required maxLength={100}/></Field>{!pool&&<Field label="设备类型"><select name="gpu_model"><option value="*">物理 GPU（允许不同型号）</option><option value="virtual">模拟设备</option></select></Field>}
  <label><input name="allow_shared" type="checkbox" defaultChecked={pool?.allow_shared}/>允许 GPU 共享</label>
  <div className="row">{[['max_tasks_per_gpu','每卡任务上限',1,1,32],['vram_reserve_gb','显存预留 GiB',2,0,10000],['max_gpu_utilization','共享利用率上限 %',90,1,100],['min_vram_gb','最低显存 GiB',0,0,10000],['cpu_per_job','每任务 CPU 核',1,1,10000],['ram_gb_per_job','每任务内存 GiB',1,1,100000]].map(([key,label,value,min,max])=><Field key={key} label={String(label)}><input name={String(key)} type="number" required min={Number(min)} max={Number(max)} defaultValue={pool?Number(pool[key as keyof Pool]):Number(value)}/></Field>)}</div></>}
function Pools() {
  const [pools,setPools]=useState<Pool[]>([]),[selected,setSelected]=useState(''),[create,setCreate]=useState(false),[error,setError]=useState(''),[version,setVersion]=useState(0);
  const refresh=()=>api<Pool[]>('admin/pools').then(setPools);useEffect(()=>{void refresh().catch(e=>setError(String(e)))},[]);
  async function act(path:string,method:string,body?:unknown){try{setError('');await api(path,method,body);await refresh();setVersion(v=>v+1)}catch(e){setError(String(e))}}
  const p=pools.find(p=>p.id===selected);
  return <>{error&&<p role="alert" className="error">{error}</p>}<section className="panel"><h2>资源池</h2><button onClick={()=>{setCreate(!create);setSelected('')}}>新增资源池</button><table><thead><tr><th>名称</th><th>状态</th><th>GPU / 排队 / 活动任务</th><th>操作</th></tr></thead><tbody>{pools.map(p=><tr key={p.id}><td>{p.name}</td><td>{p.enabled?'启用':'停用'}</td><td>{p.member_count} / {p.pending_jobs} / {p.active_jobs}</td><td><button onClick={()=>{setSelected(p.id);setCreate(false)}}>编辑</button><button onClick={()=>void act('admin/pools/'+p.id,'PATCH',{enabled:!p.enabled})}>{p.enabled?'停用':'启用'}</button><button onClick={()=>{if(confirm('删除资源池 '+p.name+'？有成员或待处理任务时会被拒绝，历史记录保留。'))void act('admin/pools/'+p.id,'DELETE')}}>删除</button></td></tr>)}</tbody></table>
  {(create||p)&&<form key={p?.id||'new'} onSubmit={async e=>{e.preventDefault();const f=new FormData(e.currentTarget);const body:Record<string,unknown>={};for(const [k,v] of f.entries())body[k]=['name','gpu_model'].includes(k)?v:Number(v);body.allow_shared=f.has('allow_shared');await act('admin/pools'+(p?'/'+p.id:''),p?'PATCH':'POST',body)}}><h3>{p?'编辑 '+p.name:'新增资源池'}</h3><PoolFields pool={p}/><button>保存资源池</button></form>}</section><Memberships key={version}/></>;
}
function Accounts() {
  const [accounts,setAccounts]=useState<Account[]>([]),[error,setError]=useState(''),[message,setMessage]=useState('');
  const refresh=()=>api<Account[]>('admin/users').then(setAccounts);useEffect(()=>{void refresh().catch(e=>setError(String(e)))},[]);
  async function act(path:string,method:string,body:unknown){try{setError('');await api(path,method,body);await refresh();setMessage('账号已更新')}catch(e){setError(String(e))}}
  return <section className="panel"><h2>账号管理</h2>{error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    <form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);void act('admin/users','POST',Object.fromEntries(f.entries()))}}><h3>创建账号</h3><Field label="用户名"><input name="username" required/></Field><Field label="初始密码（至少12位）"><input name="password" type="password" minLength={12} required autoComplete="new-password"/></Field><Field label="角色"><select name="role"><option value="user">成员</option><option value="admin">管理员</option></select></Field><button>创建账号</button></form>
    {accounts.map(a=><form key={a.id+':'+a.role+':'+a.active} onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);void act('admin/users/'+a.id,'PATCH',{role:f.get('role'),active:f.has('active'),...(f.get('password')?{password:f.get('password')}:{})})}}><h3>{a.username}</h3><div className="row"><Field label="角色"><select name="role" defaultValue={a.role}><option value="user">成员</option><option value="admin">管理员</option></select></Field><label><input name="active" type="checkbox" defaultChecked={a.active}/>启用账号</label><Field label="重置密码（留空不修改）"><input name="password" type="password" minLength={12} autoComplete="new-password"/></Field><button>保存账号</button></div></form>)}
  </section>;
}
