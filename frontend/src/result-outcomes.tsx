import React from 'react';
import './result-outcomes.css';

export type Outcomes={execution:{normal:number;abnormal:number;running:number;unknown:number;not_started:number};protocol:{planned:number;success:number;failure:number;invalid:number;errors:number;unreported:number;not_applicable_tasks:number;valid:number;success_rate:number|null};detail_mode?:'execution'|'episodes';process_scope?:string;wrapper_exit_code?:number|null};
export const executionLabels:Record<string,string>={normal:'正常结束',abnormal:'异常退出',running:'运行中',unknown:'退出码未上报',not_started:'未启动',not_applicable:'不适用（无独立回合进程）'};
export const protocolLabels:Record<string,string>={success:'业务成功',failure:'业务失败',invalid:'结果校验异常',error:'回合异常（未分类）',unreported:'结果未上报',not_applicable:'不适用'};

export function ExecutionOutcome({value}:{value?:Outcomes}){
 if(!value)return <span>程序退出统计未上报</span>;
 const e=value.execution;
 return <span>正常结束 {e.normal} 次 · 异常退出 {e.abnormal} 次 · 运行中 {e.running} 次 · 退出码未上报 {e.unknown} 次 · 未启动 {e.not_started} 次</span>;
}
export function ProtocolOutcome({value}:{value?:Outcomes}){
 if(!value)return <span>协议结果统计未上报</span>;
 const p=value.protocol;
 if(!p.planned&&p.not_applicable_tasks)return <span>不适用（普通命令）</span>;
 return <span>业务成功 {p.success} · 业务失败 {p.failure} · 结果校验异常 {p.invalid} · 未分类异常 {p.errors} · 未上报 {p.unreported} 回合<br/>有效结果 {p.valid}/{p.planned} 回合 · 有效结果成功率 {p.success_rate===null?'暂无数据':`${(p.success_rate*100).toFixed(1)}%（${p.success}/${p.valid}）`}{p.not_applicable_tasks>0&&` · 普通命令 ${p.not_applicable_tasks} 项不适用`}</span>;
}
export function ResultOutcomes({value}:{value?:Outcomes}){
 return <div className="result-outcomes" aria-label="程序执行与回合结果"><section aria-label="程序执行统计"><h4>程序执行</h4><ExecutionOutcome value={value}/></section><section aria-label="平台协议回合结果统计"><h4>回合结果（平台协议）</h4><ProtocolOutcome value={value}/></section></div>;
}
