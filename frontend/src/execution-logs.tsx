import React,{useEffect,useState} from 'react';
import {api} from './api';
type Page={run_id?:string;content?:string;chunks?:{content:string;stream?:string;gap?:boolean}[];next_after?:number;gap?:boolean;truncated?:boolean;complete?:boolean};
function tail(text:string){
 const bytes=new TextEncoder().encode(text);let start=Math.max(0,bytes.length-262144);
 while(start<bytes.length&&(bytes[start]&0xc0)===0x80)start++;
 return new TextDecoder().decode(bytes.subarray(start));
}
export function ExecutionLogs({path}:{path:string}){
 const [logs,setLogs]=useState(''),[error,setError]=useState(''),[gap,setGap]=useState(false),[refresh,setRefresh]=useState(0);
 useEffect(()=>{let active=true,cursor=-1,run='',delay=2000,timer:ReturnType<typeof setTimeout>;
  setLogs('');setError('');setGap(false);
  async function load(){let complete=false;try{
   const result=await api<Page>(path+(path.includes('?')?'&':'?')+`after=${cursor}&limit=100&cursor_run=${encodeURIComponent(run)}`);
   if(!active)return;
   const changed=Boolean(run&&result.run_id&&run!==result.run_id);run=result.run_id||run;
   cursor=result.next_after??cursor;
   const text=result.chunks?result.chunks.map(x=>(x.gap?'[部分日志未保留]\n':'')+(x.stream&&x.stream!=='stdout'?`[${x.stream}] `:'')+x.content).join(''):result.content||'';
   setLogs(old=>tail(changed||!result.chunks?text:old+text));
   setGap(old=>(changed?false:old)||Boolean(result.gap||result.truncated));setError('');delay=2000;complete=Boolean(result.complete);
  }catch(e){if(active)setError(String(e));delay=Math.min(30000,delay*2);}
  finally{if(active&&!complete)timer=setTimeout(load,delay);}}
  void load();return()=>{active=false;clearTimeout(timer);};
 },[path,refresh]);
 return <><button onClick={()=>setRefresh(x=>x+1)}>刷新日志</button>{gap&&<p role="status">部分日志已淘汰或未完整上传，当前显示可用日志。</p>}{error&&<p role="alert">日志读取失败：{error}</p>}<pre className="logs">{logs||'暂无日志'}</pre></>;
}
