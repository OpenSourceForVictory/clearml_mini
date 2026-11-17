import React,{useEffect,useState,useRef,useId} from 'react';
import {api} from './api';
import './suite-manager.css';
type Case={case_id:string;task_name:string;episodes:number;seed:number;parameters:Record<string,unknown>};
type Template={id:string;name:string;version:string;task_type?:string;execution_mode?:string;host_runtime?:{episode_launch?:string}|null};
export type ManagedSuite={client_defaults?:Record<string,unknown>;id:string;name:string;version:string;template_id:string;template_ids?:string[];cases:Case[];description:string;tags:string[];source:string;status:'draft'|'published';archived:boolean;revision:number;
  versions?:{id:string;version:string;status:string;archived:boolean}[]};
type Summary=Omit<ManagedSuite,'cases'> & {task_count:number;total_episodes:number;latest_run:null|{id:string;title:string;status:string}};
type Doc={schema_version:number;name:string;version:string;description:string;tags:string[];source:string;client?:{name:string;version:string};clients?:{name:string;version:string}[];cases:Case[]};
type Preview={valid:boolean;errors:{field:string;message:string}[];document?:Doc;task_count:number;total_episodes:number;template_id?:string;template_ids?:string[];unmatched_clients?:{name:string;version:string}[];duplicates?:{id:string;name:string;version:string}[]};
const empty=():Doc=>({schema_version:1,name:'',version:'1',description:'',tags:[],source:'',cases:[]});
function Field({label,children}:{label:string;children:React.ReactNode}){
  const id=React.useId();return <div className="field"><label htmlFor={id}>{label}</label>{React.cloneElement(children as React.ReactElement<{id:string}>,{id})}</div>;
}
function Parameters({value,change,label}:{value:Record<string,unknown>;change:(v:Record<string,unknown>)=>void;label:string}){
  const [text,setText]=useState(JSON.stringify(value)),[error,setError]=useState('');
  useEffect(()=>{setText(JSON.stringify(value));setError('');},[value]);
  return <><textarea aria-label={label} value={text} onChange={e=>{const v=e.target.value;setText(v);try{
    const data=JSON.parse(v);if(!data||Array.isArray(data)||typeof data!=='object')throw Error('参数必须为 JSON 对象');
    change(data);setError('');e.target.setCustomValidity('');
  }catch{setError('参数必须为 JSON 对象');e.target.setCustomValidity('参数必须为 JSON 对象');}}}/>{error&&<small role="alert">{error}</small>}</>;
}
export function SuiteManager({templates,onRun,onSaved,onJob}:{templates:Template[];onRun:(s:ManagedSuite)=>void;onSaved:()=>void;onJob:(id:string)=>void}){
  const [configOpen,setConfigOpen]=useState(false),[configStarted,setConfigStarted]=useState(false);
  const configRef=useRef<HTMLElement>(null),configId=useId();
  const [list,setList]=useState<Summary[]>([]),[total,setTotal]=useState(0),[page,setPage]=useState(1);
  const [q,setQ]=useState(''),[tag,setTag]=useState(''),[client,setClient]=useState(''),[state,setState]=useState('active');
  const [related,setRelated]=useState<string[]>([]);
  const [clientDefaults,setClientDefaults]=useState<Record<string,unknown>>({});
  const [doc,setDoc]=useState<Doc>(empty),[template,setTemplate]=useState(''),[editing,setEditing]=useState<ManagedSuite|null>(null);
  const [selected,setSelected]=useState<Set<number>>(new Set()),[taskQuery,setTaskQuery]=useState('');
  const [preview,setPreview]=useState<Preview|null>(null),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const [episodes,setEpisodes]=useState(3),[seed,setSeed]=useState(0),[params,setParams]=useState<Record<string,unknown>>({});
  const [runs,setRuns]=useState<{id:string;title:string;status:string;finished:number;total:number;cases:{job_id:string;task_name:string;status:string;episodes:number;progress:{completed:number;successes:number}}[]}[]>([]),[runPage,setRunPage]=useState(1);
  useEffect(()=>{if(configOpen&&configRef.current?.getClientRects().length)configRef.current.scrollIntoView({block:'start'});},[configOpen,editing?.id]);
  const selectedTemplate=templates.find(t=>t.id===template);
  const ordinary=selectedTemplate?.task_type==='command'&&!(selectedTemplate.execution_mode==='host'&&selectedTemplate.host_runtime?.episode_launch==='per_episode');
  const locked=!!editing&&(editing.status==='published'||editing.archived);
  const visible=doc.cases.map((c,i)=>({c,i})).filter(({c})=>(c.task_name+' '+c.case_id).toLowerCase().includes(taskQuery.toLowerCase()));
  async function refresh(){
    const result=await api<{items:Summary[];total:number}>('suites/manage?'+new URLSearchParams({page:String(page),q,tag,template_id:client,state}));
    setList(result.items);setTotal(result.total);
  }
  useEffect(()=>{let live=true;const load=async()=>{try{const r=await api<{items:Summary[];total:number}>('suites/manage?'+new URLSearchParams({page:String(page),q,tag,template_id:client,state}));if(live){setList(r.items);setTotal(r.total);}}catch(e){if(live)setError(String(e));}};
    void load();const t=setInterval(()=>void load(),5000);return()=>{live=false;clearInterval(t);};},[page,q,tag,client,state]);
  useEffect(()=>{let live=true;setRuns([]);if(editing)void api<typeof runs>('suites/'+editing.id+'/runs?page='+runPage).then(r=>{if(live)setRuns(r);}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[editing?.id,runPage]);
  function reset(value:Doc=empty(),t='',defaults:Record<string,unknown>={}){
    setConfigStarted(true);setConfigOpen(true);setClientDefaults(defaults);
    setDoc(value);setTemplate(t);setRelated(t?[t]:[]);setEditing(null);setSelected(new Set());setPreview(null);setMessage('');setError('');setTaskQuery('');setRunPage(1);
  }
  function setCases(cases:Case[]){setDoc(old=>({...old,cases}));setPreview(null);}
  async function load(id:string){
    try{const s=await api<ManagedSuite>('suites/'+id);reset({schema_version:1,name:s.name,version:s.version,description:s.description,tags:s.tags,source:s.source,cases:s.cases},s.template_id,s.client_defaults||{});setEditing(s);setRelated(s.template_ids||[s.template_id]);}
    catch(e){setError(String(e));}
  }
  async function check(value=doc){
    const result=await api<Preview>('suites/preview','POST',{content:JSON.stringify({...value,...(ordinary?{cases:value.cases.map(c=>({...c,episodes:1}))}:{}),tags:value.tags.map(t=>t.trim()).filter(Boolean)})});setPreview(result);return result;
  }
  async function importFile(file:File){
    setBusy(true);setError('');
    try{if(file.size>1048576)throw Error('文件不能超过 1 MiB');
      const result=await api<Preview>('suites/preview','POST',{content:await file.text()});setPreview(result);setConfigStarted(true);setConfigOpen(true);
      if(result.valid&&result.document){reset(result.document,result.template_id||result.template_ids?.[0]||'');setRelated(result.template_ids?.length?result.template_ids:result.template_id?[result.template_id]:[]);setPreview(result);setMessage('文件已载入预览，尚未保存。请确认客户端及任务。'+(result.unmatched_clients?.length?'未匹配模板：'+result.unmatched_clients.map(t=>t.name+'/'+t.version).join('、')+'，请手动选择可用版本。':''));}
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function preset(){
    setBusy(true);setError('');
    try{const catalogs=await api<(Doc&{id:string})[]>('suites/catalogs');const {id,...value}=catalogs[0];reset(value);setMessage('已载入 RoboTwin 50 项目录。勾选任务后点击“仅保留选中任务”，再保存子集。');}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function save(published:boolean,run:boolean){
    setBusy(true);setError('');setMessage('');
    try{
      const result=await check();if(!result.valid||!result.document)return;
      if(!template)throw Error('请选择当前账号可用的客户端模板');
      const {schema_version,client:hint,clients:hints,...values}=result.document;
      const body={...values,client_defaults:clientDefaults,template_id:template,template_ids:related.length?related:[template],status:published?'published':'draft',...(editing?{expected_revision:editing.revision}:{})};
      const saved=await api<ManagedSuite>('suites'+(editing?'/'+editing.id:''),editing?'PUT':'POST',body);
      setEditing(saved);setDoc({...result.document,cases:saved.cases});setMessage(published?'集合版本已发布。':'草稿已保存。');
      await refresh();onSaved();if(run)onRun(saved);
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function saveRelations(){
    if(!editing)return;setBusy(true);setError('');
    try{
      const ids=template&&related.includes(template)?[template,...related.filter(x=>x!==template)]:related;
      const saved=await api<ManagedSuite>('suites/'+editing.id+'/client-templates','PUT',{expected_revision:editing.revision,template_ids:ids});
      setEditing(saved);setTemplate(saved.template_id);setRelated(saved.template_ids||[saved.template_id]);setMessage('可用客户端模板已保存，任务清单和已有运行不变。');await refresh();onSaved();
    }catch(e){setError(String(e));}finally{setBusy(false)}
  }
  async function action(action:string){
    if(!editing)return;setBusy(true);setError('');
    try{const result=await api<ManagedSuite>('suites/'+editing.id+'/actions','POST',{action,expected_revision:editing.revision});
      if(action==='delete'){reset();setMessage('草稿已删除。');}else{setEditing(result);setMessage(action==='archive'?'已归档，已有运行不受影响。':'已恢复集合。');}
      await refresh();onSaved();
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function exportFile(){
    if(!editing)return;
    try{const value=await api<Doc>('suites/'+editing.id+'/export');const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download='evaluation-suite.json';a.click();URL.revokeObjectURL(url);
    }catch(e){setError(String(e));}
  }
  function copy(newVersion:boolean){
    const old=editing;reset({...doc,name:newVersion?doc.name:doc.name+' 副本',version:newVersion?doc.version+'-next':'1'},template,clientDefaults);
    setRelated([...related]);
    setMessage(newVersion?'已复制为新版本草稿，请确认版本号。':'已复制到新集合草稿，尚未保存。');
    if(old)setSelected(new Set());
  }
  return <div className="suite-manager">
    {error&&<p role="alert" className="error">{error}</p>}
    <section className="panel">
      <h2>评测集合管理</h2>
      <p className="hint">从预置目录、JSON/YAML 文件或已有集合创建。集合定义评测内容；服务地址、资源池和并发在运行时配置。</p>
      <div className="suite-overview" aria-label="集合概览"><div><span>筛选结果 · 集合版本</span><strong>{total}</strong></div><div><span>当前页已发布</span><strong>{list.filter(s=>s.status==='published'&&!s.archived).length}</strong></div><div><span>当前页草稿</span><strong>{list.filter(s=>s.status==='draft'&&!s.archived).length}</strong></div></div>
      <div className="row suite-create"><button disabled={busy} onClick={()=>reset()}>新建集合</button><button disabled={busy} onClick={()=>void preset()}>从 RoboTwin 预置创建</button>
        <label className="field">导入集合 JSON / YAML<input aria-label="导入集合 JSON / YAML" type="file" accept=".json,.yaml,.yml" disabled={busy} onChange={e=>{const f=e.target.files?.[0];if(f)void importFile(f);e.target.value='';}}/></label></div>
      <div className="suite-filters">
        <Field label="搜索集合"><input value={q} onChange={e=>{setQ(e.target.value);setPage(1);}}/></Field>
        <Field label="筛选标签"><input value={tag} onChange={e=>{setTag(e.target.value);setPage(1);}}/></Field>
        <Field label="筛选客户端"><select value={client} onChange={e=>{setClient(e.target.value);setPage(1);}}><option value="">全部客户端</option>{templates.map(t=><option key={t.id} value={t.id}>{t.name} / {t.version}</option>)}</select></Field>
        <Field label="集合状态"><select value={state} onChange={e=>{setState(e.target.value);setPage(1);}}><option value="active">未归档</option><option value="draft">草稿</option><option value="published">已发布</option><option value="archived">已归档</option><option value="all">全部</option></select></Field>
      </div>
      <div className="suite-list-scroll" role="region" aria-label="集合列表，可横向滚动" tabIndex={0}><table className="suite-list"><thead><tr><th scope="col">集合名称 / 版本</th><th scope="col">状态</th><th scope="col">客户端模板</th><th scope="col">任务 / 回合</th><th scope="col">最近运行</th><th scope="col">操作</th></tr></thead>
        <tbody>{list.map(s=><tr key={s.id} aria-label={'集合 '+s.name+' / '+s.version}><th scope="row"><span className="suite-ellipsis suite-name" title={s.name}>{s.name}</span><small className="suite-ellipsis" title={s.version+' · '+s.tags.join('、')}>{s.version}{s.tags.length?' · '+s.tags.join('、'):''}</small></th><td><span className={'suite-status '+(s.archived?'archived':s.status)}>{s.archived?'已归档':s.status==='draft'?'草稿':'已发布'}</span></td>
          <td><span className="suite-ellipsis" title={templates.find(t=>t.id===s.template_id)?.name||s.template_id}>{(s.template_ids||[s.template_id]).map(id=>{const t=templates.find(t=>t.id===id);return t?t.name+' / '+t.version:'模板不可用'}).join('、')}</span><small>{(s.template_ids||[s.template_id]).length} 个可用模板</small></td>
          <td>{s.task_count} 项 / {s.total_episodes} 回合</td><td><span className="suite-ellipsis" title={s.latest_run?.title}>{s.latest_run?.title||'尚未运行'}</span>{s.latest_run&&<small>{({PENDING:'排队中',RUNNING:'运行中',SUCCEEDED:'成功',FAILED:'失败',CANCELLED:'已取消'} as Record<string,string>)[s.latest_run.status]||s.latest_run.status}</small>}</td>
          <td><button aria-label={'查看 '+s.name+' / '+s.version} disabled={busy} onClick={()=>{if(editing?.id===s.id){setConfigOpen(true);setConfigStarted(true);}else void load(s.id);}}>查看配置</button></td></tr>)}</tbody></table></div>
      {!list.length&&<p>没有符合条件的集合。</p>}
      <div className="pagination"><button disabled={page<=1} onClick={()=>setPage(p=>p-1)}>集合上一页</button><span>第 {page} 页，共 {total} 个版本</span><button disabled={page*20>=total} onClick={()=>setPage(p=>p+1)}>集合下一页</button></div>
    </section>
    <section ref={configRef} className="panel suite-config" hidden={!configStarted}>
      <div className="suite-config-heading"><h2>{editing?editing.name+' / '+editing.version:'新建集合草稿'}</h2><button type="button" aria-expanded={configOpen} aria-controls={configId} onClick={()=>setConfigOpen(v=>!v)}>{configOpen?'收起配置':'展开配置'}</button></div>
      <div id={configId} hidden={!configOpen}>
      {message&&<p role="status" className="success">{message}</p>}
      {editing&&<><p>版本状态：{editing.archived?'已归档':editing.status==='draft'?'草稿':'已发布'} · 编辑版本 {editing.revision}</p>
        <div className="row"><button disabled={busy} onClick={()=>copy(false)}>复制为新集合</button><button disabled={busy} onClick={()=>copy(true)}>创建新版本</button>
          <button onClick={()=>void exportFile()}>导出集合</button>
          <button disabled={busy} onClick={()=>void action(editing.archived?'restore':'archive')}>{editing.archived?'恢复集合':'归档集合'}</button>
          {editing.status==='draft'&&<button disabled={busy} onClick={()=>{if(window.confirm('删除此草稿？此操作不可恢复。'))void action('delete');}}>删除草稿</button>}
          <button disabled={busy} onClick={()=>{if(window.confirm('重新载入将放弃本页未保存修改，继续？'))void load(editing.id);}}>重新载入最新版本</button>
          {editing.status==='published'&&!editing.archived&&<button className="primary" disabled={!templates.some(t=>(editing.template_ids||[editing.template_id]).includes(t.id))} onClick={()=>onRun(editing)}>运行此集合</button>}
        </div>
        {!!editing.versions?.length&&<p>同名版本：{editing.versions.map(v=><button key={v.id} onClick={()=>void load(v.id)}>{v.version}{v.archived?'（归档）':''}</button>)}</p>}
      </>}
      {locked&&<p className="hint">已发布或归档的清单只读。编辑内容请创建新版本；归档草稿可以恢复后修改。</p>}
      <section aria-label="可用客户端模板" className="panel">
        <h3>可用客户端模板</h3><p>勾选此集合允许使用的客户端模板，运行时选择其中一个。至少保留一个；保存时检查参数兼容性，业务是否实现任务名仍需试跑确认。</p>
        <fieldset disabled={busy||!!editing?.archived}>{[...templates,...related.filter(id=>!templates.some(t=>t.id===id)).map(id=>({id,name:'不可用模板',version:id}))].map(t=><label key={t.id} style={{display:'block',margin:'8px 0'}}><input data-testid={'suite-client-'+t.id} type="checkbox" checked={related.includes(t.id)} onChange={e=>{const next=e.target.checked?[...related,t.id]:related.filter(id=>id!==t.id);setRelated(next);if(!next.includes(template))setTemplate(next[0]||'')}}/> {t.name} / {t.version}</label>)}</fieldset>
        {editing&&<><button type="button" disabled={busy||editing.archived||!related.length} onClick={()=>void saveRelations()}>保存可用客户端模板</button><p className="hint">已发布集合也可修改可用模板。此按钮仅保存模板选择，不保存下方任务清单或其他草稿修改。</p></>}
      </section>
      <form onSubmit={e=>{e.preventDefault();void save(false,false);}}>
        <fieldset disabled={locked||busy}>
          <div className="submit-grid">
            <Field label="集合名称"><input required maxLength={200} value={doc.name} onChange={e=>setDoc({...doc,name:e.target.value})}/></Field>
            <Field label="集合版本"><input required maxLength={100} value={doc.version} onChange={e=>setDoc({...doc,version:e.target.value})}/></Field>
            <Field label="集合标签（逗号分隔）"><input value={doc.tags.join(',')} onChange={e=>setDoc({...doc,tags:e.target.value.split(',')})}/></Field>
          </div>
          {selectedTemplate?.task_type==='command'&&<p className="hint">{ordinary?'普通命令集合：每项任务执行一次，回合兼容字段固定为 1。':'宿主机逐回合普通命令：每回合启动一次，按退出码记录完成进度。'}业务参数与种子会传递；不计算评测目标成功率。</p>}
          {doc.client&&<p className="hint">导入客户端提示：{doc.client.name} / {doc.client.version}。无法自动匹配时请手动选择。</p>}
          <Field label="集合说明"><textarea maxLength={2000} value={doc.description} onChange={e=>setDoc({...doc,description:e.target.value})}/></Field>
          <label>集合默认业务参数</label><Parameters value={clientDefaults} change={setClientDefaults} label="集合默认业务参数"/>
          <Field label="来源版本"><input maxLength={1000} value={doc.source} onChange={e=>setDoc({...doc,source:e.target.value})}/></Field>
        </fieldset>
        <Field label="搜索清单任务"><input value={taskQuery} onChange={e=>setTaskQuery(e.target.value)}/></Field>
        <fieldset disabled={locked||busy}>
          <div className="row"><button type="button" onClick={()=>setSelected(new Set(visible.map(x=>x.i)))}>选中当前显示任务</button><button type="button" onClick={()=>setSelected(new Set())}>清空任务选择</button>
            <button type="button" disabled={!selected.size} onClick={()=>{setCases(doc.cases.filter((_,i)=>selected.has(i)));setSelected(new Set());}}>仅保留选中任务</button>
            <button type="button" disabled={!selected.size} onClick={()=>{setCases(doc.cases.filter((_,i)=>!selected.has(i)));setSelected(new Set());}}>移除选中任务</button></div>
          <div className="submit-grid">
            <Field label="批量回合数"><input type="number" disabled={ordinary} min={1} max={10000} value={ordinary?1:episodes} onChange={e=>setEpisodes(Number(e.target.value))}/></Field>
            <Field label="批量种子"><input type="number" min={0} max={2147483647} value={seed} onChange={e=>setSeed(Number(e.target.value))}/></Field>
            <div><label>批量参数（合并并覆盖同名键）</label><Parameters value={params} change={setParams} label="批量参数"/></div>
          </div>
          <button type="button" disabled={!selected.size} onClick={()=>setCases(doc.cases.map((c,i)=>selected.has(i)?{...c,episodes:ordinary?1:episodes,seed,parameters:{...c.parameters,...params}}:c))}>应用到选中 {selected.size} 项</button>
          <div className="table-scroll suite-task-table"><table><thead><tr><th>选择</th><th>标识 / 任务</th><th>回合 / 种子</th><th>参数</th></tr></thead><tbody>
            {visible.map(({c,i})=><tr key={i}><td><input type="checkbox" aria-label={'选择任务 '+c.task_name} checked={selected.has(i)} onChange={e=>setSelected(old=>{const n=new Set(old);if(e.target.checked)n.add(i);else n.delete(i);return n;})}/></td>
              <td><input aria-label={'任务标识 '+i} value={c.case_id} onChange={e=>setCases(doc.cases.map((x,n)=>n===i?{...x,case_id:e.target.value}:x))}/>
                <input aria-label={'任务名称 '+i} value={c.task_name} onChange={e=>setCases(doc.cases.map((x,n)=>n===i?{...x,task_name:e.target.value}:x))}/></td>
              <td><input aria-label={'任务回合 '+i} type="number" disabled={ordinary} min={1} max={10000} value={ordinary?1:c.episodes} onChange={e=>setCases(doc.cases.map((x,n)=>n===i?{...x,episodes:Number(e.target.value)}:x))}/>
                <input aria-label={'任务种子 '+i} type="number" min={0} max={2147483647} value={c.seed} onChange={e=>setCases(doc.cases.map((x,n)=>n===i?{...x,seed:Number(e.target.value)}:x))}/></td>
              <td><Parameters value={c.parameters} label={'任务参数 '+i} change={v=>setCases(doc.cases.map((x,n)=>n===i?{...x,parameters:v}:x))}/></td></tr>)}
          </tbody></table></div>
          <button type="button" disabled={doc.cases.length>=100} onClick={()=>setCases([...doc.cases,{case_id:'case-'+Date.now(),task_name:'',episodes:ordinary?1:3,seed:0,parameters:{}}])}>添加任务</button>
        </fieldset>
        <p>共 {doc.cases.length} 项、{ordinary?0:doc.cases.reduce((n,c)=>n+c.episodes,0)} 个回合。导入保存不检查资产或推理服务；运行时另行准备。</p>
        {!locked&&<div className="row"><button type="button" disabled={busy} onClick={()=>void check().catch(e=>setError(String(e)))}>校验预览</button>
          <button disabled={busy}>保存草稿</button>
          <button type="button" disabled={busy} onClick={e=>{if(e.currentTarget.form?.reportValidity())void save(true,false);}}>发布版本</button>
          <button type="button" className="primary" disabled={busy} onClick={e=>{if(e.currentTarget.form?.reportValidity())void save(true,true);}}>发布并运行</button></div>}
      </form>
      {preview&&<div aria-label="导入校验结果">{preview.valid?<p>校验通过：{preview.task_count} 项 / {preview.total_episodes} 回合。</p>:
        <ul role="alert">{preview.errors.map((e,i)=><li key={i}>{e.field}：{e.message}</li>)}</ul>}
        {!!preview.duplicates?.length&&<p>发现相同任务内容：{preview.duplicates.map(x=>x.name+' / '+x.version).join('、')}。可以查看已有集合，或明确使用新名称/版本保存。</p>}</div>}
      {editing&&<div><h3>关联运行</h3>{!runs.length?<p>尚无关联运行。</p>:runs.map(r=><details key={r.id}><summary>{r.title} · {r.status} · 已结束 {r.finished}/{r.total}</summary>
          <table><thead><tr><th>任务</th><th>状态</th><th>回合进度</th><th>结果</th></tr></thead><tbody>{r.cases.map(c=><tr key={c.job_id}>
            <td>{c.task_name}</td><td>{c.status}</td><td>{c.progress?.completed||0}/{c.episodes} · 成功 {c.progress?.successes||0}</td><td><button onClick={()=>onJob(c.job_id)}>任务详情与结果</button></td>
          </tr>)}</tbody></table></details>)}
        <button disabled={runPage<=1} onClick={()=>setRunPage(p=>p-1)}>运行上一页</button><span>第 {runPage} 页</span><button disabled={runs.length<20} onClick={()=>setRunPage(p=>p+1)}>运行下一页</button></div>}
      </div>
    </section>
  </div>;
}
