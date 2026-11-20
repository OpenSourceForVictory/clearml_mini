import React,{createContext,useContext,useEffect,useRef,useState} from 'react';
import {guideTopics} from './guide-content';
import './user-guide.css';
const GuideContext=createContext<(id?:string)=>void>(()=>{});
export function GuideButton({topic='start',children='用户指南'}:{topic?:string;children?:React.ReactNode}){
 const open=useContext(GuideContext);
 return <button type="button" className="guide-link" onClick={()=>open(topic)}>{children}</button>;
}
export function PageHelp({topic}:{topic:string}){
 const item=guideTopics.find(t=>t.id===topic);
 if(!item)return null;
 return <details className="page-help"><summary>操作说明 · {item.title}</summary><p>{item.intro}</p><ol>{item.steps.map(s=><li key={s}>{s}</li>)}</ol><GuideButton topic={topic}>查看相关指南与常见问题</GuideButton></details>;
}
export function GuideProvider({admin,children}:{admin:boolean;children:React.ReactNode}){
 const [selected,setSelected]=useState('start'),[search,setSearch]=useState('');
 const dialog=useRef<HTMLDialogElement>(null),trigger=useRef<HTMLElement|null>(null);
 const topics=guideTopics.filter(t=>admin||!t.admin);
 const topic=topics.find(t=>t.id===selected)||topics[0];
 const query=search.trim().toLocaleLowerCase();
 const matches=topics.filter(t=>[t.title,t.intro,...t.steps,...t.notes,t.example||''].join(' ').toLocaleLowerCase().includes(query));
 function open(id='start'){trigger.current=document.activeElement as HTMLElement;setSelected(id);setSearch('');dialog.current?.showModal()}
 function close(){dialog.current?.close()}
 useEffect(()=>{const logout=()=>dialog.current?.close();window.addEventListener('eval-logout',logout);return()=>window.removeEventListener('eval-logout',logout)},[]);
 return <GuideContext.Provider value={open}>{children}<dialog ref={dialog} className="user-guide" aria-labelledby="user-guide-title" onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();close()}}} onClose={()=>trigger.current?.focus()}>
 <div className="guide-heading"><div><h2 id="user-guide-title">用户指南</h2><p>操作步骤、业务接入与故障排查</p></div><button type="button" onClick={close} aria-label="关闭用户指南">关闭</button></div>
 <label className="guide-search">搜索指南<input autoFocus type="search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="例如：SDK、端口、资产、排队"/></label>
 <div className="guide-layout"><nav aria-label="指南主题">{matches.map(t=><button type="button" key={t.id} aria-current={topic.id===t.id?'page':undefined} onClick={()=>setSelected(t.id)}>{t.title}</button>)}{!matches.length&&<p role="status">未找到相关主题，请尝试“参数”“SDK”或“资源”。</p>}</nav>
 <article aria-label="指南内容"><h3>{topic.title}</h3><p>{topic.intro}</p><h4>如何操作</h4><ol>{topic.steps.map(s=><li key={s}>{s}</li>)}</ol><h4>结果与常见问题</h4><ul>{topic.notes.map(s=><li key={s}>{s}</li>)}</ul>{topic.example&&<><h4>示例</h4><pre>{topic.example}</pre></>}
 {['integration','sdk','protocol','templates','builds','services'].includes(topic.id)&&<section className="guide-downloads" aria-label="SDK 与样例下载"><h4>接入材料</h4><p>解压 SDK 包后在目录中运行 <code>python self_test.py</code>。仅验证协议，不执行真实模型评测。</p><a href="/integration/mini-eval-sdk.zip" download>下载 SDK 与四种客户端接入样例（ZIP）</a><p><a href="/integration/README.md" download>下载业务接入完整指南（含模板操作和协议样例）</a></p><p><a href="/template-examples/catalog.json" download>下载客户端与服务端模板 JSON</a></p><p>{['client.py','episode.py','server.py','Dockerfile'].map(f=><a key={f} href={'/template-examples/'+f} download>{f} </a>)}</p><p><a href="/template-examples/README.md" download>下载完整模板运行说明</a></p></section>}
 <div className="guide-related"><h4>继续了解</h4>{['start','integration','parameters','services','wizard'].filter(id=>id!==topic.id).map(id=><button type="button" key={id} onClick={()=>{setSelected(id);setSearch('')}}>{topics.find(t=>t.id===id)?.title}</button>)}</div>
 </article></div></dialog></GuideContext.Provider>;
}
