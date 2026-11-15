import React from 'react';
import {Field} from './form-fields';
export type ParameterField={type:string;label?:string;description?:string;default?:unknown;required?:boolean;overridable?:boolean;enum?:unknown[];min?:number;max?:number;max_length?:number};
export type ParameterSchema={version:number;fields:Record<string,ParameterField>};
export type ValuesDraft=Record<string,string>;
export function parseValues(schema:ParameterSchema|null|undefined,draft:ValuesDraft):Record<string,unknown>{
 const result:Record<string,unknown>={};
 for(const [key,raw] of Object.entries(draft)){
  const field=schema?.fields[key];if(!field)throw Error('未知业务参数：'+key);
  if(field.type==='string'){result[key]=raw;continue;}
  let value:unknown;try{value=JSON.parse(raw)}catch{throw Error(`${key}：请输入有效的 ${field.type} 值`)}
  if(field.type==='integer'&&!Number.isInteger(value)||field.type==='number'&&typeof value!=='number'||field.type==='boolean'&&typeof value!=='boolean'||field.type==='object'&&(!value||Array.isArray(value)||typeof value!=='object')||field.type==='array'&&!Array.isArray(value))throw Error(key+'：参数类型不匹配');
  if(typeof value==='number'&&((field.min!==undefined&&value<field.min)||(field.max!==undefined&&value>field.max)))throw Error(key+'：超出允许范围');
  result[key]=value;
 }
 return result;
}
export function BusinessValues({schema,value,onChange,label}:{schema?:ParameterSchema|null;value:ValuesDraft;onChange:(v:ValuesDraft)=>void;label:string}){
 return <fieldset><legend>{label}</legend>{!schema||!Object.keys(schema.fields).length?<p className="hint">此模板未声明类型化业务参数。任务参数仍可在集合清单中设置。</p>:Object.entries(schema.fields).map(([name,f])=>{
  const explicit=Object.hasOwn(value,name),current=explicit?value[name]:f.default===undefined?'':f.type==='string'?String(f.default):JSON.stringify(f.default);
  const update=(v:string)=>onChange({...value,[name]:v});
  return <div key={name}><Field label={f.label?`${f.label} (${name})`:name}>{f.type==='boolean'||f.type==='enum'?<select value={current} onChange={e=>update(e.target.value)}><option value="">未设置</option>{(f.type==='boolean'?[true,false]:f.enum||[]).map((v,i)=><option key={i} value={JSON.stringify(v)}>{String(v)}</option>)}</select>:f.type==='object'||f.type==='array'?<textarea rows={3} value={current} onChange={e=>update(e.target.value)}/>:<input type={['number','integer'].includes(f.type)?'number':'text'} step={f.type==='integer'?1:'any'} min={f.min} max={f.max} maxLength={f.max_length} value={current} onChange={e=>update(e.target.value)}/>}</Field><small>{f.type} · {explicit?'本次输入':f.default!==undefined?'模板默认':'未设置'}{f.required?' · 必填':''} · {f.description}</small>{explicit&&<button type="button" onClick={()=>{const next={...value};delete next[name];onChange(next)}}>使用继承值</button>}</div>
 })}</fieldset>;
}
