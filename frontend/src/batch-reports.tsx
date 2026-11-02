import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';

type Case = {case_id:string;status:string;episodes:number;completed_episodes:number;success_rate:number|null;config:Record<string,unknown>};
type Report = {generated_at:string;batch:{id:string;title:string};summary:{completed_episodes:number;planned_episodes:number;success_rate:number|null;partial:boolean};cases:Case[]};
type Comparison = {left:Report;right:Report;comparable:boolean;delta_success_rate:number|null;cases:{case_id:string;left:Case|null;right:Case|null;reasons:string[];definition_differences:string[];config_differences:string[];delta_success_rate:number|null}[]};
const rate=(n:number|null)=>n===null?'未知':(n*100).toFixed(2)+'%';
const delta=(n:number|null)=>n===null?'不可比':(n*100).toFixed(2)+' 个百分点';
const reasons:Record<string,string>={missing_left:'左侧缺项',missing_right:'右侧缺项',definition_mismatch:'任务定义不同',unreported:'未声明回合上报',incomplete:'尚未完整成功执行'};

export function ReportDownloads({id}:{id:string}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function download(format:string) {
    if(busy)return;setBusy(true);setError('');
    try {
      const response=await fetch(`/api/v1/batches/${encodeURIComponent(id)}/report?format=${format}`,{headers:{Authorization:'Bearer '+sessionStorage.getItem('eval-token')}});
      if(!response.ok)throw new Error('报告下载失败，请检查权限或稍后重试');
      const url=URL.createObjectURL(await response.blob());
      const a=document.createElement('a');a.href=url;a.download=`batch-${id}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <div aria-label="报告下载"><button disabled={busy} onClick={()=>void download('json')}>下载 JSON 报告</button><button disabled={busy} onClick={()=>void download('csv')}>下载 CSV 报告</button>{error&&<p role="alert">{error}</p>}</div>;
}

export function BatchComparison({left,right,onBack}:{left:string;right:string;onBack:()=>void}) {
  const [data,setData]=useState<Comparison|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const generation=useRef(0),loading=useRef(false);
  async function refresh(epoch:number) {
    if(loading.current)return;loading.current=true;setBusy(true);setError('');
    try {const value=await api<Comparison>(`batch-comparisons?left_id=${encodeURIComponent(left)}&right_id=${encodeURIComponent(right)}`);if(epoch===generation.current)setData(value);}
    catch(e){if(epoch===generation.current)setError(String(e));}
    finally{if(epoch===generation.current){loading.current=false;setBusy(false);}}
  }
  useEffect(()=>{const epoch=++generation.current;loading.current=false;setData(null);void refresh(epoch);return()=>{generation.current++;};},[left,right]);
  return <section className="panel" aria-label="批次比较"><button onClick={onBack}>返回批次列表</button><h2>批次结果比较</h2>
    <p className="hint">外部模型版本未验证。地址相同不代表 checkpoint 相同；配置差异需要结合实验条件解释。</p>
    <button disabled={busy} onClick={()=>void refresh(generation.current)}>{busy?'正在读取快照…':'刷新比较快照'}</button>
    {error&&<p role="alert">{error}{data?'；保留上次成功读取的快照':''}</p>}
    {data&&<><p>快照时间：{data.left.generated_at}</p><p>整体差值（右 − 左）：{delta(data.delta_success_rate)}</p>
      <div className="submit-grid">{[data.left,data.right].map((r,i)=><div key={r.batch.id}><h3>{i===0?'左侧':'右侧'}：{r.batch.title}</h3><p>完成 {r.summary.completed_episodes}/{r.summary.planned_episodes} 回合 · 成功率 {rate(r.summary.success_rate)}{r.summary.partial?' · 部分结果':''}</p><ReportDownloads id={r.batch.id}/></div>)}</div>
      <table><thead><tr><th>任务</th><th>左侧结果</th><th>右侧结果</th><th>差值 / 可比性</th><th>定义与配置差异</th></tr></thead><tbody>{data.cases.map(row=><tr key={row.case_id}><td>{row.case_id}</td>{[row.left,row.right].map((c,i)=><td key={i}>{c?`${c.status} · ${c.completed_episodes}/${c.episodes} · ${rate(c.success_rate)}`:'缺项'}</td>)}<td>{delta(row.delta_success_rate)}{row.reasons.map(r=><p key={r}>{reasons[r]||r}</p>)}</td><td>{row.definition_differences.length>0&&<p>定义：{row.definition_differences.join(', ')}</p>}{row.config_differences.map(k=><p key={k}>{k}：{displayConfig(row.left?.config[k])} → {displayConfig(row.right?.config[k])}</p>)}{!row.definition_differences.length&&!row.config_differences.length&&'无差异'}</td></tr>)}</tbody></table>
    </>}
  </section>;
}

function displayConfig(value:unknown){return value==null?'—':typeof value==='object'?JSON.stringify(value):String(value);}
