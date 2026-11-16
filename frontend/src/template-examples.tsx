import React,{useEffect,useState} from 'react';
import {Field} from './form-fields';
import type {Template} from './templates';
type Example={id:string;kind:string;title:string;definition:Template};
export function TemplateExamples({kind,onUse}:{kind:string;onUse:(t:Template)=>void}){
 const [examples,setExamples]=useState<Example[]>([]),[selected,setSelected]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let active=true;fetch('/template-examples/catalog.json').then(r=>{if(!r.ok)throw new Error('样例暂不可用');return r.json()}).then(v=>{if(active)setExamples(v)}).catch(()=>{if(active)setError('样例暂不可用，可继续手动填写。')});return()=>{active=false}},[]);
 const example=examples.find(e=>e.id===selected&&e.kind===kind);
 return <details className="template-help"><summary>从完整样例开始</summary><p>样例包含程序、模板和运行步骤，使用 HTTP 回显验证接入，不是真实模型评测。先准备镜像或宿主机环境，再载入并修改。</p>{error&&<p role="status">{error}</p>}
 <Field label="运行样例"><select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">选择样例</option>{examples.filter(e=>e.kind===kind).map(e=><option key={e.id} value={e.id}>{e.title}</option>)}</select></Field>
 {example&&<><pre>{JSON.stringify(example.definition,null,2)}</pre><button type="button" onClick={()=>onUse({...example.definition,id:'example:'+example.id})}>载入样例草稿</button></>}
 <p><a href="/template-examples/README.md" download>下载准备与运行说明</a> · <a href="/template-examples/catalog.json" download>下载全部模板</a></p><p>{['client.py','episode.py','server.py','Dockerfile'].map(file=><React.Fragment key={file}><a href={'/template-examples/'+file} download>{file}</a>{' '}</React.Fragment>)}</p></details>;
}
