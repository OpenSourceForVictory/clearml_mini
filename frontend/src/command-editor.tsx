import React from 'react';
import {Field} from './form-fields';
export function CommandEditor({value,onChange}:{value:string;onChange:(v:string)=>void}){
 let args:string[]|null=null;try{const v=JSON.parse(value);if(Array.isArray(v)&&v.every(x=>typeof x==='string'))args=v}catch{}
 function update(index:number,text:string){if(args)onChange(JSON.stringify(args.map((v,i)=>i===index?text:v),null,2))}
 return <div><h3>程序与固定参数</h3><p>第一项填写程序或解释器，例如 python3；后续逐项填写脚本路径及固定参数。动态值在本区域的“动态参数映射”中选择。平台直接执行参数数组，不执行 shell 展开。</p>
 {args?args.map((v,i)=><div className="row" key={i}><Field label={i===0?'启动程序':`固定参数 ${i}`}><input value={v} required={i===0} onChange={e=>update(i,e.target.value)} placeholder={i===0?'python3':i===1?'/app/client.py':'例如 --verbose'}/></Field>{i>0&&<button type="button" onClick={()=>onChange(JSON.stringify(args!.filter((_,n)=>n!==i),null,2))}>移除固定参数 {i}</button>}</div>):<p role="alert">高级命令需为 JSON 字符串数组，请修正后使用可视化编辑。</p>}
 <button type="button" disabled={!args||args.length>=64} onClick={()=>onChange(JSON.stringify([...(args||[]),''],null,2))}>添加固定参数</button>
 <details><summary>高级命令 JSON</summary><Field label="业务命令（JSON argv 数组）"><textarea name="command" required rows={4} value={value} onChange={e=>onChange(e.target.value)}/></Field></details></div>;
}
