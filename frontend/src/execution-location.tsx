import React,{useState} from 'react';
import {usePoll} from './poll';
export function CopyValue({label,value}:{label:string;value:string}){
 const [message,setMessage]=useState('');
 return <span><code>{value}</code> <button onClick={async()=>{try{await navigator.clipboard.writeText(value);setMessage('已复制');}catch{setMessage('复制失败，请选择文字手动复制');}}}>复制{label}</button><span role="status">{message}</span></span>;
}
type Location={run_id:string;execution_id:string|null;node_name:string|null;directories:{id:string;path:string|null;scan_status:string;cleanup_status:string|null;scanned_at:string}[]};
const states:Record<string,string>={complete:'已扫描',partial:'部分扫描',blocked:'受保护',missing:'数据已不存在',succeeded:'已清理',failed:'清理失败',skipped:'跳过清理'};
export function ExecutionLocation({jobId,runId}:{jobId:string;runId:string}){
 return <LocationDetails key={jobId+'/'+runId} jobId={jobId} runId={runId}/>;
}
function LocationDetails({jobId,runId}:{jobId:string;runId:string}){
 const {data,error}=usePoll<Location>(`jobs/${jobId}/attempts/${runId}/execution-location`);
 return <section aria-label="执行身份与目录"><h4>执行身份与目录</h4>
 {error?<p role="alert">执行目录暂不可用，请刷新或确认平台版本。</p>:!data?<p>正在读取执行目录…</p>:<>
 <p>执行 ID：{data.execution_id?<CopyValue label="执行 ID" value={data.execution_id}/>: '尚未分配'}</p>
 <p>节点：{data.node_name||'尚未分配或未记录'}</p>
 {data.execution_id&&!data.directories.length&&<p>目录尚未上报，不根据节点当前配置推测历史路径。</p>}
 {data.directories.map(d=><div key={d.id}>{d.path?<CopyValue label="目录" value={d.path}/>:<p>目录记录不可用</p>}<p>{states[d.scan_status]||'状态未知'}{d.cleanup_status&&` · ${states[d.cleanup_status]||'清理状态未知'}`} · 上报时间：{d.scanned_at}</p></div>)}
 <p className="muted">这里是节点宿主机数据目录，上报记录不保证文件仍存在。回合 work/episode-N 与 output/episode-N 为输入和产物目录，不代表已独立保存回合日志。</p></>}
 </section>;
}
