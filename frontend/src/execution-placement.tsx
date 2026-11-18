import React from 'react';
export type Placement={id:string;node_name:string;accelerator_type:string;gpu_index:number;gpu_uuid?:string|null;gpu_model?:string|null};
export function ExecutionPlacement({value}:{value?:Placement|null}){
 if(value===undefined)return <span>位置信息不可用</span>;
 if(value===null)return <span>未分配或位置记录不可用</span>;
 const kind=value.accelerator_type==='npu'?'NPU':value.accelerator_type==='gpu'?'GPU':'设备';
 return <span title={value.gpu_uuid||undefined}>{value.node_name} / {kind} {value.gpu_index}{value.gpu_model&&<small>型号：{value.gpu_model}</small>}</span>;
}
