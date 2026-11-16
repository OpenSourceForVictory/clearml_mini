import React from 'react';
import type {PlatformBinding} from './command-mappings';
import type {ParameterDraft} from './parameter-editor';
export function LaunchPreview({command,bindings,parameters}:{command:string;bindings:PlatformBinding[];parameters:ParameterDraft}){
 let args:string[];
 try{args=JSON.parse(command);if(!Array.isArray(args)||!args.every(v=>typeof v==='string'))throw new Error()}catch{return <p role="status">修正命令 JSON 后显示启动预览。</p>}
 const mapped=[...(parameters.enabled?parameters.rows.flatMap(r=>r.definition.binding?[{...r.definition.binding,source:'business.'+r.name} as PlatformBinding]:[]):[]),...bindings];
 const argv=mapped.filter(b=>b.target==='argv'),env=mapped.filter(b=>b.target==='env');
 const format=(b:PlatformBinding)=>b.encoding==='flag'?`[${b.name}：仅当 ⟪${b.source}⟫ 为 true]`:b.encoding==='repeat'?`[${b.name} ⟪${b.source} 的元素⟫：逐项重复]`:`${b.name} ⟪${b.source}${b.encoding==='json'?' · JSON 编码':''}⟫`;
 return <section aria-label="实时启动预览" className="launch-preview"><h3>启动命令预览</h3><p>随编辑实时更新。⟪来源⟫ 在执行时填入；可选业务参数未提供值时不追加。每个带引号的固定项代表一个参数，平台直接执行 argv，不执行 shell。</p><pre aria-label="启动命令预览">{[...args.map(v=>JSON.stringify(v)),...argv.map(format)].join(' ')}</pre>{env.length>0&&<><h4>动态环境变量</h4><pre aria-label="动态环境变量预览">{env.map(b=>format(b)).join('\n')}</pre></>}</section>;
}
