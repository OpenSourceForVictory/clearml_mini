import React, {useEffect, useId, useState} from 'react';

/** Local function pages keep drafts in memory; lazy pages mount on first visit. */
export function FunctionPages({label,items,value,onChange,lazy=false}:{label:string;items:{id:string;label:string;description?:string;content:React.ReactNode}[];value:string;onChange:(id:string)=>void;lazy?:boolean}) {
  const prefix=useId();
  const [visited,setVisited]=useState<string[]>([value]);
  useEffect(()=>{setVisited(old=>old.includes(value)?old:[...old,value])},[value]);
  return <div className="function-pages">
    <div className="function-tabs" role="tablist" aria-label={label}>{items.map((item,index)=><button type="button" key={item.id} id={prefix+'-tab-'+item.id} role="tab" aria-controls={prefix+'-panel-'+item.id} aria-selected={value===item.id} tabIndex={value===item.id?0:-1} onClick={()=>onChange(item.id)} onKeyDown={e=>{
      const next=e.key==='ArrowRight'?(index+1)%items.length:e.key==='ArrowLeft'?(index+items.length-1)%items.length:e.key==='Home'?0:e.key==='End'?items.length-1:null;
      if(next!==null){e.preventDefault();onChange(items[next].id);document.getElementById(prefix+'-tab-'+items[next].id)?.focus()}
    }}>{item.label}</button>)}</div>
    {items.map(item=><div key={item.id} role="tabpanel" id={prefix+'-panel-'+item.id} aria-labelledby={prefix+'-tab-'+item.id} hidden={value!==item.id}>
      {item.description&&<p className="function-description">{item.description}</p>}
      {(!lazy||value===item.id||visited.includes(item.id))&&item.content}
    </div>)}
  </div>;
}
