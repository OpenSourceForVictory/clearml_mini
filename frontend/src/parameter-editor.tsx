import React from 'react';
import {Field} from './form-fields';

type Definition=Record<string,any>;
export type ParameterRow={id:string;name:string;definition:Definition;defaultText:string;choicesText:string;structureText:string};
export type ParameterDraft={enabled:boolean;rows:ParameterRow[]};
let nextId=0;
const id=()=>`parameter-${++nextId}`;
const types=[['string','字符串'],['integer','整数'],['number','数值'],['boolean','布尔'],['enum','枚举'],['object','对象'],['array','数组']];
const reserved=new Set(['task_name','episodes','seed','endpoint','execution_id','input_path','work_dir','output_dir','episode_id','episode_seed','result_path','port','bind_host']);

export function parameterDraft(schema?:Record<string,any>|null):ParameterDraft{
 return {enabled:!!schema,rows:Object.entries(schema?.fields||{}).map(([name,value])=>{
  const d=value as Definition;
  return {id:id(),name,definition:structuredClone(d),defaultText:'default' in d?(d.type==='string'?String(d.default):JSON.stringify(d.default)):'',choicesText:JSON.stringify(d.enum||[],null,2),structureText:JSON.stringify(d.type==='array'?d.items||{type:'string'}:d.properties||{},null,2)};
 })};
}
function parsed(text:string,field:string){try{return JSON.parse(text)}catch{throw new Error(`${field}：请输入有效 JSON`);}}
export function parameterSchema(draft:ParameterDraft):Record<string,any>|null{
 if(!draft.enabled)return null;
 const fields:Record<string,any>={};const bindings=new Set<string>();
 for(const row of draft.rows){
  const name=row.name.trim();
  if(!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name)||reserved.has(name)||name.startsWith('MINI_EVAL_'))throw new Error(`业务参数 ${name||'（未命名）'}：名称非法或为平台保留字段`);
  if(Object.hasOwn(fields,name))throw new Error(`业务参数 ${name}：名称重复`);
  const d=structuredClone(row.definition);
  if('default' in d){
   d.default=d.type==='string'?row.defaultText:parsed(row.defaultText,`${name} 默认值`);
   const v=d.default;
   if((d.type==='integer'&&!Number.isSafeInteger(v))||(d.type==='number'&&(typeof v!=='number'||!Number.isFinite(v)))||(d.type==='boolean'&&typeof v!=='boolean')||(d.type==='array'&&!Array.isArray(v))||(d.type==='object'&&(v===null||Array.isArray(v)||typeof v!=='object')))throw new Error(`${name}：默认值与参数类型不符`);
   if(typeof v==='number'&&(('min' in d&&v<d.min)||('max' in d&&v>d.max)))throw new Error(`${name}：默认值超出范围`);
  }
  if(d.type==='enum'){
   d.enum=parsed(row.choicesText,`${name} 枚举选项`);
   if(!Array.isArray(d.enum)||!d.enum.length)throw new Error(`${name}：枚举选项不能为空`);
   if('default' in d&&!d.enum.some((v:unknown)=>typeof v===typeof d.default&&v===d.default))throw new Error(`${name}：默认值不在枚举选项中`);
  }
  if(d.type==='object')d.properties=parsed(row.structureText,`${name} 对象结构`);
  if(d.type==='array')d.items=parsed(row.structureText,`${name} 数组元素类型`);
  if('min' in d&&'max' in d&&d.min>d.max)throw new Error(`${name}：最小值不能大于最大值`);
  const binding=d.binding;
  if(binding&&binding.target!=='input'){
   if(!binding.name)throw new Error(`${name}：请填写绑定名称`);
   if(binding.target==='argv'&&!/^--[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(binding.name))throw new Error(`${name}：命令行绑定须使用 --name 形式`);
   if(binding.target==='env'&&(!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(binding.name)||/^(MINI_EVAL_|NVIDIA_)/.test(binding.name)||['CUDA_VISIBLE_DEVICES','PYTHONPATH','PYTHONHOME'].includes(binding.name)))throw new Error(`${name}：环境变量名称非法或为平台保留变量`);
   const key=binding.target+':'+binding.name;if(bindings.has(key))throw new Error(`${name}：绑定目标重复`);bindings.add(key);
  }
  Object.defineProperty(fields,name,{value:d,enumerable:true,writable:true,configurable:true});
 }
 return {version:1,fields};
}
export function ParameterEditor({value,onChange}:{value:ParameterDraft;onChange:(v:ParameterDraft)=>void}){
 function update(id:string,patch:Partial<ParameterRow>){onChange({...value,rows:value.rows.map(r=>r.id===id?{...r,...patch}:r)});}
 function field(row:ParameterRow,patch:Definition){update(row.id,{definition:{...row.definition,...patch}});}
 function optional(row:ParameterRow,key:string,v:unknown){const d={...row.definition};if(v===undefined)delete d[key];else d[key]=v;update(row.id,{definition:d});}
 return <div className="parameter-editor">
 <label className="check"><input type="checkbox" checked={value.enabled} onChange={e=>onChange({...value,enabled:e.target.checked})}/>启用类型化业务参数</label>
 <p className="muted">模板定义字段，集合任务或服务部署填写具体值。运行时只允许覆盖明确开放的字段。评测任务名、回合数和种子沿用集合中的任务计划。</p>
 {value.enabled&&<><p className="hint">需要 Worker v8。未声明的业务参数会被拒绝；关闭此选项将恢复旧版自由 JSON 参数。更改字段类型会重置该字段的默认值、约束和绑定。</p>
 {value.rows.map((row,index)=>{const d=row.definition,b=d.binding||{target:'input'};return <fieldset className="parameter-card" key={row.id}><legend>业务字段 {index+1}{row.name?` · ${row.name}`:''}</legend>
 <div className="row"><Field label="字段名称"><input value={row.name} onChange={e=>update(row.id,{name:e.target.value})}/></Field><Field label="显示名称"><input value={d.label||''} onChange={e=>optional(row,'label',e.target.value||undefined)}/></Field><Field label="参数类型"><select value={d.type} onChange={e=>{const type=e.target.value;update(row.id,{definition:{type,...(d.label?{label:d.label}:{}),...(d.description?{description:d.description}:{}),...(d.required?{required:true}:{}),...(d.overridable?{overridable:true}:{})},defaultText:'',choicesText:'[]',structureText:type==='array'?' {"type":"string"}':'{}'})}}>{types.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></Field></div>
 <Field label="参数说明"><input value={d.description||''} onChange={e=>optional(row,'description',e.target.value||undefined)}/></Field>
 <div className="row"><label><input type="checkbox" checked={!!d.required} onChange={e=>optional(row,'required',e.target.checked||undefined)}/>必填</label><label><input type="checkbox" checked={!!d.overridable} onChange={e=>optional(row,'overridable',e.target.checked||undefined)}/>允许运行时覆盖</label><label><input type="checkbox" checked={'default' in d} onChange={e=>optional(row,'default',e.target.checked?null:undefined)}/>设置默认值</label></div>
 {'default' in d&&<Field label={d.type==='string'?'默认值':'默认值（JSON）'}><input value={row.defaultText} placeholder={d.type==='string'?'允许空字符串':'例如 3、true 或 "easy"'} onChange={e=>update(row.id,{defaultText:e.target.value})}/></Field>}
 {['integer','number'].includes(d.type)&&<div className="row">{[['min','最小值'],['max','最大值']].map(([key,label])=><Field key={key} label={label}><input type="number" step={d.type==='integer'?'1':'any'} value={d[key]??''} onChange={e=>optional(row,key,e.target.value===''?undefined:Number(e.target.value))}/></Field>)}</div>}
 {['string','array'].includes(d.type)&&<Field label="最大长度"><input type="number" min={0} max={4096} value={d.max_length??''} placeholder="使用平台默认上限" onChange={e=>optional(row,'max_length',e.target.value===''?undefined:Number(e.target.value))}/></Field>}
 {d.type==='enum'&&<Field label="枚举选项（JSON 数组）"><textarea rows={3} value={row.choicesText} onChange={e=>update(row.id,{choicesText:e.target.value})}/></Field>}
 {['object','array'].includes(d.type)&&<Field label={d.type==='array'?'数组元素类型（JSON）':'对象字段结构（JSON）'}><textarea rows={4} value={row.structureText} onChange={e=>update(row.id,{structureText:e.target.value})}/></Field>}
 <div className="row"><Field label="传递方式"><select value={b.target} onChange={e=>field(row,{binding:e.target.value==='input'?{target:'input'}:{target:e.target.value,name:'',encoding:['object','array'].includes(d.type)?'json':'literal'}})}><option value="input">标准输入 JSON</option><option value="argv">命令行参数</option><option value="env">环境变量</option></select></Field>
 {b.target!=='input'&&<><Field label="绑定名称"><input value={b.name||''} placeholder={b.target==='argv'?'--scene':'SCENE'} onChange={e=>field(row,{binding:{...b,name:e.target.value}})}/></Field><Field label="传递编码"><select value={b.encoding||'literal'} onChange={e=>field(row,{binding:{...b,encoding:e.target.value}})}>{!['object','array'].includes(d.type)&&<option value="literal">单个值</option>}<option value="json">JSON 编码</option>{b.target==='argv'&&d.type==='boolean'&&<option value="flag">仅为 true 时添加开关</option>}{b.target==='argv'&&d.type==='array'&&<option value="repeat">每个元素重复一个参数</option>}</select></Field></>}</div>
 <button type="button" className="danger" onClick={()=>onChange({...value,rows:value.rows.filter(r=>r.id!==row.id)})}>删除字段 {index+1}</button>
 </fieldset>})}
 <button type="button" disabled={value.rows.length>=64} onClick={()=>onChange({...value,rows:[...value.rows,{id:id(),name:'',definition:{type:'string'},defaultText:'',choicesText:'[]',structureText:'{}'}]})}>添加业务字段</button>
 <details><summary>查看参数契约预览</summary><pre>{(()=>{try{return JSON.stringify(parameterSchema(value),null,2)}catch(e){return String(e)}})()}</pre></details>
 </>}
 </div>;
}
