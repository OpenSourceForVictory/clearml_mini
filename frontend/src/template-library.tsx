import React,{useEffect,useState,useRef} from 'react';
import {api} from './api';
import {TemplateCopy,CopyTemplate} from './template-copy';
export function TemplateLibrary({onChange}:{onChange:()=>void}){
 const [kind,setKind]=useState<'client'|'server'>('client'),[rows,setRows]=useState<CopyTemplate[]>([]),[source,setSource]=useState<CopyTemplate|null>(null),[error,setError]=useState('');
 const sequence=useRef(0);
 async function refresh(){const seq=++sequence.current;try{const rows=await api<CopyTemplate[]>('runtime-templates/'+kind);if(seq===sequence.current){setRows(rows);setError('')}}catch(e){if(seq===sequence.current)setError(String(e))}}
 useEffect(()=>{setSource(null);setRows([]);void refresh();return()=>{sequence.current++}},[kind]);
 return <section className="panel" aria-label="我的模板版本"><h2>模板复用与草稿</h2><label>模板分类<select value={kind} onChange={e=>setKind(e.target.value as typeof kind)}><option value="client">客户端</option><option value="server">服务端</option></select></label><button onClick={()=>void refresh()}>刷新模板版本</button>{error&&<p role="alert">{error}</p>}
 {rows.map(t=><div key={t.id}><p>{t.name} / {t.version} · {t.status==='draft'?'草稿':'已发布'}</p><button onClick={()=>setSource(t)}>{t.status==='draft'?'继续编辑':'复制并修改'} {t.name} / {t.version}</button></div>)}
 {source&&<TemplateCopy key={source.id} kind={kind} source={source} onClose={()=>setSource(null)} onPublished={()=>{setSource(null);void refresh();onChange()}}/>}</section>;
}
