import React,{useEffect,useState} from 'react';
import {api} from './api';
import {Field} from './form-fields';

type Credential={node_name:string;token:string;release_id?:string};
type Progress={state:string|null;revision:number;ready:boolean;updater_ready:boolean;slots:{id:string;gpu_index:number;ready:boolean}[]};
function save(data:BlobPart,name:string,type:string){const url=URL.createObjectURL(new Blob([data],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
export function NodePackage({credential,existing=false}:{credential?:Credential|null;existing?:boolean}){
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[digest,setDigest]=useState('');
 return <div><h3>完整节点部署</h3><p>安装包包含 Worker、runner/SDK、独立升级执行器和 SSH 部署脚本。下载后还需要执行安装，再检查回连状态。</p>
 {error&&<p role="alert">{error}</p>}
 <button disabled={busy} onClick={async()=>{setBusy(true);setError('');try{const b=await api<{archive:string;filename:string;sha256:string;release_id:string}>('admin/node-installation-bundle');if(credential?.release_id&&credential.release_id!==b.release_id)throw Error('平台版本已变化，请重新生成接入凭据和安装包');save(Uint8Array.from(atob(b.archive),c=>c.charCodeAt(0)),b.filename,'application/zip');setDigest(b.sha256)}catch(e){setError(String(e))}finally{setBusy(false)}}}>下载完整节点安装包</button>
 {credential&&<form onSubmit={async e=>{e.preventDefault();const f=new FormData(e.currentTarget);setError('');try{const health=await api<{runner_image:string}>('admin/health');save(JSON.stringify({api_url:f.get('url'),node_name:credential.node_name,token:credential.token,runner_image:health.runner_image,pool_id:null,gpu_index:0,mock:false},null,2),'node-credential.json','application/json')}catch(e){setError(String(e))}}}><Field label="节点可访问的 API 地址"><input name="url" type="url" required placeholder="http://平台内网IP:18000"/></Field><p>SSH 转发访问页面时，此处仍需填写节点能够访问的地址。凭据仅本次显示，请下载后妥善保管。</p><button>下载本次接入凭据</button></form>}
 <details><summary>通过 SSH 部署到节点</summary><p>解压安装包，在能 SSH 访问节点的电脑执行；替换用户名、主机及原服务名称。SSH 使用已验证的 known_hosts 和本机密钥。</p>
 <pre>{`python3 scripts/deploy_node.py --bundle mini-eval-node.zip --credential node-credential.json --sha256 ${digest||'<下载后显示摘要>'} --target USER@HOST${existing?' --existing --service user:mini-eval-worker@gpu0.service':''}`}</pre>
 {existing&&<p>已有 NFS 系统服务：使用 --sudo --home /home/原用户 --service system:mini-eval-worker-nfs.service，并去掉示例中的 user 服务。要求具备非交互 sudo 权限；否则将安装包拷贝到节点后，以 sudo 执行 scripts/install_node.py。多个原服务需重复填写 --service。安装保留工作目录、资产和原服务单元。</p>}
 <p>部署失败后机器仍不接任务。安装完成后检查全部 GPU 与升级执行器回连；重新纳管完成后，还需在资源池管理中分配 GPU。</p></details></div>;
}
export function ReEnrollment({id,revision,revoked,onChange}:{id:string;revision:number;revoked:boolean;onChange:()=>Promise<unknown>}){
 const [credential,setCredential]=useState<Credential|null>(null),[state,setState]=useState<Progress|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const load=async()=>{const value=await api<Progress>('admin/nodes/'+id+'/enrollment');if(Array.isArray(value.slots))setState(value)};
 useEffect(()=>{void load().catch(e=>setError(String(e)));const timer=setInterval(()=>void load().catch(e=>setError(String(e))),5000);return()=>clearInterval(timer)},[id]);
 async function start(){setBusy(true);setError('');try{setCredential(await api<Credential>('admin/nodes/'+id+'/reenroll','POST',{expected_revision:revision}));await onChange();await load()}catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section aria-label="重新纳管"><h3>重新纳管</h3><p>恢复原机器身份和配置，重新部署完整节点程序。安装和回连验收完成前保持停用。</p>{error&&<p role="alert">{error}</p>}
 <button disabled={busy} onClick={()=>{if(!credential||confirm('重新生成将使上一份接入凭据失效，继续？'))void start()}}>{revoked?'开始重新纳管':'重新生成接入凭据'}</button>
 {!revoked&&<><p role="status">等待完整安装与回连验收</p><ul>{state?.slots.map(s=><li key={s.id}>GPU {s.gpu_index}：{s.ready?'版本已验证':'等待目标版本回连'}</li>)}<li>升级执行器：{state?.updater_ready?'版本已验证':'等待目标版本回连'}</li></ul></>}
 <NodePackage credential={credential} existing/>
 <button disabled={busy||!state?.ready||revoked} onClick={async()=>{setBusy(true);setError('');try{await api('admin/nodes/'+id+'/enrollment/complete','POST',{expected_revision:state?.revision});setCredential(null);await onChange();await load()}catch(e){setError(String(e))}finally{setBusy(false)}}}>完成纳管并启用</button></section>;
}
