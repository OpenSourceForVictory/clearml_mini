import React from 'react';
import './result-outcomes.css';

export type Outcomes={execution:{normal:number;abnormal:number;running:number;unknown:number;not_started:number};protocol:{planned:number;success:number;failure:number;invalid:number;errors:number;unreported:number;not_applicable_tasks:number;valid:number;success_rate:number|null};detail_mode?:'execution'|'episodes';process_scope?:string;wrapper_exit_code?:number|null};
export const executionLabels:Record<string,string>={normal:'正常结束',abnormal:'异常退出',running:'运行中',unknown:'退出码未上报',not_started:'未启动',not_applicable:'不适用（无独立回合进程）'};
export const protocolLabels:Record<string,string>={success:'业务成功',failure:'业务失败',invalid:'结果校验异常',error:'回合异常（未分类）',unreported:'结果未上报',not_applicable:'不适用'};

function ExecutionDetails({value}:{value?:Outcomes}){
 if(!value)return <span>程序退出统计未上报</span>;
 const e=value.execution;
 return <span>正常结束 {e.normal} 次 · 异常退出 {e.abnormal} 次 · 运行中 {e.running} 次 · 退出码未上报 {e.unknown} 次 · 未启动 {e.not_started} 次</span>;
}
function ProtocolDetails({value}:{value?:Outcomes}){
 if(!value)return <span>协议结果统计未上报</span>;
 const p=value.protocol;
 if(!p.planned&&p.not_applicable_tasks)return <span>不适用（普通命令）</span>;
 return <span>业务成功 {p.success} · 业务失败 {p.failure} · 结果校验异常 {p.invalid} · 未分类异常 {p.errors} · 未上报 {p.unreported} 回合<br/>有效结果 {p.valid}/{p.planned} 回合 · 有效结果成功率 {p.success_rate===null?'暂无数据':`${(p.success_rate*100).toFixed(1)}%（${p.success}/${p.valid}）`}{p.not_applicable_tasks>0&&` · 普通命令 ${p.not_applicable_tasks} 项不适用`}</span>;
}
const rate=(success:number,total:number)=>`${(success/total*100).toFixed(1)}%（${success}/${total}）`;
export function ExecutionOutcome({value}:{value?:Outcomes}){
 if(!value)return <span>退出码待上报</span>;
 const e=value.execution,known=e.normal+e.abnormal,total=known+e.running+e.unknown+e.not_started;
 return <span className="outcome-summary"><strong>正常退出率 {known?rate(e.normal,known):'暂无数据'}</strong>
 <small>{total?`退出码已知 ${known}/${total} 次`:'无独立程序退出数据'}{e.running>0?' · 运行中':''}{e.unknown>0?' · 退出码未上报':''}{!known&&e.not_started>0?' · 未启动':''}</small></span>;
}
export function ProtocolOutcome({value}:{value?:Outcomes}){
 if(!value)return <span>回合结果待上报</span>;
 const p=value.protocol;
 if(!p.planned&&p.not_applicable_tasks)return <span>未接入（普通命令）</span>;
 return <span className="outcome-summary"><strong>评测成功率 {p.valid?rate(p.success,p.valid):p.invalid+p.errors>0?'暂无有效结果':'待上报'}</strong>
 <small>有效结果 {p.valid}/{p.planned} 回合{p.invalid+p.errors>0?' · 有异常结果':''}{p.not_applicable_tasks>0?` · ${p.not_applicable_tasks} 项未接入`:''}</small></span>;
}
export function ResultOutcomes({value}:{value?:Outcomes}){
 return <div className="result-outcomes" aria-label="程序执行与回合结果"><section aria-label="程序执行统计"><h4>程序执行</h4><ExecutionOutcome value={value}/>{value&&<details><summary>查看程序执行详情</summary><ExecutionDetails value={value}/></details>}</section><section aria-label="平台协议回合结果统计"><h4>回合结果（平台协议）</h4><ProtocolOutcome value={value}/>{value&&<details><summary>查看回合结果详情</summary><ProtocolDetails value={value}/></details>}</section></div>;
}
