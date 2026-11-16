import React from 'react';
import {ResourceSummary} from './resource-requirements';
import type {Template} from './templates';

export function TemplateHelp({kind,mode,integration}:{kind:string;mode:string;integration:string}){
 const server=kind==='server';
 const command=server?['python3','/app/server.py','--host','${bind_host}','--port','${port}']:
 integration==='sdk-command'?['python3','/app/episode.py','--task','${task_name}','--endpoint','${endpoint}','--seed','${episode_seed}']:
 ['python3','/app/client.py','--input','${input_path}'];
 return <section className="template-help" aria-label="注册操作说明"><h3>如何注册{server?'服务端':'客户端'}模板</h3>
 <ol><li>准备程序和运行环境，再填写名称与版本。</li><li>定义业务字段，配置启动命令、环境变量与目录映射。</li><li>注册后在{server?'服务部署或任务提交向导':'任务提交向导'}中选择此模板，填写具体参数并检查环境。</li></ol>
 <p>{mode==='docker'?'Docker：镜像须包含业务代码及依赖，并能被执行节点获取。填写镜像名不会自动构建或安装环境。':'宿主机：先在机器管理中配置并批准同名运行环境，确保程序、解释器及依赖已安装；此处填写环境名称，不是主机路径。'}</p>
 <p>{server?'服务端持续运行并接受客户端请求，平台根据健康检查判断是否就绪；注册模板本身不会启动服务。':integration==='sdk-command'?'SDK 命令模式：平台每回合启动一次业务命令，程序须向 MINI_EVAL_RESULT 指定文件写入真实结果。':'直接协议模式：程序一次处理完整任务，负责回合事件和最终汇总；只打印日志不能统计已完成回合。'}</p>
 <details><summary>查看当前模式填写示例</summary><p>以下是配置写法示例，请替换成自己的程序；/app 下的脚本不会由注册操作自动生成。</p><pre>{JSON.stringify(command,null,2)}</pre>
 {server?<p>程序需解析 --host 和 --port；HTTP 健康路径例如 /healthz，须与程序实际提供的接口一致。</p>:integration==='sdk-command'?<><p>每回合完成后写入结果文件（success 由业务判断）：</p><pre>{`import json, os\nfrom pathlib import Path\n# success、reward 来自本回合的实际评测\nPath(os.environ['MINI_EVAL_RESULT']).write_text(\n    json.dumps({'success': success, 'metrics': {'reward': reward}, 'artifacts': []})\n)`}</pre><p>退出码为零不能代替结果文件。结果缺失或格式不正确会判为执行失败。</p></>:<p>程序从输入文件读取任务信息，按评测协议报告回合与最终结果。已接入 runner.sdk 的 ClientClass 可通过 SDK 入口运行。</p>}
 <p>任务名、回合和推理地址在提交及调度时确定；模板中使用上述占位符，不固定写某台机器的地址。</p></details>
 </section>;
}
const text=(value:unknown)=>value===undefined||value===null?'未配置':typeof value==='string'?value:JSON.stringify(value,null,2);
function Value({label,value}:{label:string;value:unknown}){return <div className="template-value"><dt>{label}</dt><dd><pre>{text(value)}</pre></dd></div>}
export function TemplateConfig({template:t,kind,onClose,onCopy}:{template:Template;kind:string;onClose:()=>void;onCopy:()=>void}){
 const host=t.execution_mode==='host',r=(host?t.host_runtime:t.runtime)||{};
 const fields=(t.parameter_schema?.fields||{}) as Record<string,any>;
 return <section className="template-config" aria-label="模板配置详情"><h3>{t.name} / {t.version} · 配置详情（只读）</h3><p>按注册时的分组查看配置。需要修改时复制为新版本，已有任务的快照保持不变。</p>
 <div className="row"><button type="button" onClick={onClose}>关闭配置</button><button type="button" onClick={onCopy}>以此配置创建新版本</button></div>
 <fieldset><legend>基础信息</legend><dl><Value label="模板名称" value={t.name}/><Value label="版本" value={t.version}/><Value label="状态" value={t.enabled?'启用':'停用'}/></dl></fieldset>
 <fieldset><legend>平台参数</legend><p>执行身份、资源与服务地址在任务运行时分配，以下为模板声明。</p><dl><Value label="工作目录" value={host?'节点工作区下的独立执行目录':r.work_dir}/><Value label="输出目录" value={host?'节点工作区下的独立输出目录':r.output_dir}/><Value label="平台参数绑定" value={t.platform_bindings}/></dl></fieldset>
 <fieldset><legend>资源要求</legend><ResourceSummary value={t.resource_requirements}/></fieldset><fieldset><legend>业务参数</legend>{!t.parameter_schema?<p>未启用类型化参数，使用自由 JSON 参数。</p>:!Object.keys(fields).length?<p>已启用类型化参数，未声明业务字段。</p>:Object.entries(fields).map(([name,f])=><section key={name}><h4>{name}</h4><dl><Value label="参数类型" value={f.type}/><Value label="必填" value={f.required?'是':'否'}/><Value label="默认值" value={f.default}/><Value label="约束与说明" value={Object.fromEntries(Object.entries(f).filter(([k])=>!['type','required','default','binding','overridable'].includes(k)))}/><Value label="传递方式" value={f.binding||{target:'input'}}/><Value label="允许运行时覆盖" value={f.overridable?'是':'否'}/></dl></section>)}</fieldset>
 <fieldset><legend>运行参数</legend><dl><Value label="执行方式" value={host?'宿主机运行':t.execution_mode?'Docker 镜像运行':'Docker 镜像运行（默认）'}/><Value label={host?'节点环境名称':'Docker 镜像'} value={host?r.environment_name:t.image}/><Value label="接入方式" value={kind==='server'?'长期服务':t.integration_mode==='sdk-command'?'SDK + 每回合业务命令':'直接评测协议'}/><Value label="业务命令（JSON argv 数组）" value={t.command}/><Value label="环境变量" value={r.environment}/><Value label="允许提交时覆盖的变量名" value={r.overridable_env}/><Value label="逻辑资产" value={r.assets}/></dl>
 {kind==='server'&&<dl><Value label="服务端口" value={t.port}/><Value label="服务协议" value={t.protocol}/><Value label="健康检查" value={t.health_type}/><Value label="健康检查路径" value={t.health_path}/><Value label="启动健康等待秒数" value={t.startup_timeout}/></dl>}</fieldset>
 {!host&&<fieldset><legend>挂载配置</legend>{Object.entries(r.mounts||{}).map(([name,item])=>{const m=item as any;return <section key={name}><h4>{name}</h4><dl><Value label="逻辑来源" value={m.source}/><Value label="容器路径" value={m.target}/><Value label="读写权限" value={m.read_only===false?'可写':'只读'}/><Value label="是否必需" value={m.required===false?'可选':'必需'}/></dl></section>})}{!Object.keys(r.mounts||{}).length&&<p>未声明额外目录挂载。</p>}<Value label="设备声明" value={r.devices}/></fieldset>}
 <details><summary>补充运行配置</summary><pre>{JSON.stringify(r,null,2)}</pre></details><details><summary>原始配置 JSON</summary><pre>{JSON.stringify(t,null,2)}</pre></details></section>;
}
