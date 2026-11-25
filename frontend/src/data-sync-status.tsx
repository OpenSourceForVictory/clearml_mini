export type DataSync = {state:'legacy'|'in_progress'|'pending'|'complete'|'failed'|'unknown';delayed:boolean;log_truncated:boolean;error?:string|null};

export function DataSyncStatus({value}:{value?:DataSync}){
  if(!value)return <span>同步状态未上报</span>;
  const label={legacy:'历史执行（无独立同步状态）',in_progress:'执行中，结果持续上报',pending:'结果待同步',complete:'结果同步完成',failed:'结果同步失败',unknown:'同步状态待确认'}[value.state];
  return <span>{label}{value.delayed&&' · 同步延迟'}{value.log_truncated&&' · 诊断日志有缺口'}{value.error&&<small>{value.error}</small>}</span>;
}
