import React from 'react';
import {TemplateForm} from './templates';
import type {Template} from './templates';

export function TemplateHelp({kind,mode,integration,taskType='evaluation',taskResultReporting=false}:{kind:string;mode:string;integration:string;taskType?:string;taskResultReporting?:boolean}){
 const server=kind==='server',ordinary=kind==='client'&&taskType==='command';
 const command=server?['python3','/app/server.py','--host','${bind_host}','--port','${port}']:
 ordinary?['python3','/app/process.py','--input','${input_path}']:integration==='sdk-command'?['python3','/app/episode.py','--task','${task_name}','--endpoint','${endpoint}','--seed','${episode_seed}']:
 ['python3','/app/client.py','--input','${input_path}'];
 return <section className="template-help" aria-label="注册操作说明"><h3>如何注册{server?'服务端':'客户端'}模板</h3>
 <ol><li>准备程序和运行环境，再填写名称与版本。</li><li>定义业务字段，配置启动命令、环境变量与目录映射。</li><li>注册后在{server?'服务部署或任务提交向导':'任务提交向导'}中选择此模板，填写具体参数并检查环境。</li></ol>
 <p>{mode==='docker'?'Docker：镜像须包含业务代码及依赖，并能被执行节点获取。填写镜像名不会自动构建或安装环境。':'宿主机：先在机器管理中配置并批准同名运行环境，确保程序、解释器及依赖已安装；此处填写环境名称，不是主机路径。'}</p>
 <p>{server?'服务端持续运行并接受客户端请求，平台根据健康检查判断是否就绪；注册模板本身不会启动服务。':ordinary&&taskResultReporting?'普通命令 + 任务级 SDK：程序按退出码报告执行状态，并可额外调用 SDK 报告一次业务结果；两个结果分开展示。':ordinary?'普通命令：按退出码判定执行状态，不要求回合事件、评测汇总或结果文件。已有程序可通过参数和环境变量直接接入。':integration==='sdk-command'?'SDK 命令模式：平台每回合启动一次业务命令，程序须向 MINI_EVAL_RESULT 指定文件写入真实结果。':'直接协议模式：程序一次处理完整任务，负责回合事件和最终汇总；只打印日志不能统计已完成回合。'}</p>
 <details><summary>通用命令示例：Python、Bash、可执行文件</summary><pre>{JSON.stringify([['python3','-u','app.py'],['/opt/venv/bin/python','-m','my_app'],['/bin/bash','run.sh'],['./bin/evaluator','--config','config.json'],['bash','-lc','./prepare && exec ./bin/server']],null,2)}</pre><p>每一行数组是一种启动方式，选择其中一种。设置业务执行目录，预先准备程序与依赖。相对脚本和配置由业务程序从该目录读取。业务参数通过下方映射追加，不要拼进 Shell 字符串。普通命令按退出码完成；SDK 逐回合模式仍须写结果 JSON，服务端仍须通过就绪检查。</p></details>
 <details><summary>查看当前模式填写示例</summary><p>以下是配置写法示例，请替换成自己的程序；/app 下的脚本不会由注册操作自动生成。</p><pre>{JSON.stringify(command,null,2)}</pre>
 {server?<p>程序需解析 --host 和 --port；HTTP 健康路径例如 /healthz，须与程序实际提供的接口一致。</p>:ordinary&&taskResultReporting?<><p>程序处理整项任务后可以调用：</p><pre>{`from runner.sdk import report_task_result\nreport_task_result(success=passed, metrics={'score': score}, summary='可选说明')`}</pre><p>success 必须是业务布尔结果；指标只能是有限数值。调用不上传平台令牌。未调用时仍按进程退出码判定程序执行状态，并显示业务结果未上报。</p></>:ordinary?<p>程序处理一项任务后退出，正常完成返回 0，执行失败返回非零。依赖与代码须提前准备，服务来源可选无需服务。</p>:integration==='sdk-command'?<><p>每回合完成后写入结果文件（success 由业务判断）：</p><pre>{`import json, os\nfrom pathlib import Path\n# success、reward 来自本回合的实际评测\nPath(os.environ['MINI_EVAL_RESULT']).write_text(\n    json.dumps({'success': success, 'metrics': {'reward': reward}, 'artifacts': []})\n)`}</pre><p>退出码为零不能代替结果文件。结果缺失或格式不正确会判为执行失败。</p></>:<p>程序从输入文件读取任务信息，按评测协议报告回合与最终结果。已接入 runner.sdk 的 ClientClass 可通过 SDK 入口运行。</p>}
 <p>任务名、回合和推理地址在提交及调度时确定；模板中使用上述占位符，不固定写某台机器的地址。</p></details>
 </section>;
}
export function TemplateConfig({template,kind,onClose,onCopy}:{template:Template;kind:string;onClose:()=>void;onCopy:()=>void}){
 return <section className="template-config" aria-label="模板配置详情">
 <h3>{template.name} / {template.version} · 配置详情（只读）</h3>
 <p>使用注册模板的相同布局查看配置。修改时请复制为新版本。</p>
 <div className="row"><button type="button" onClick={onClose}>关闭配置</button><button type="button" onClick={onCopy}>以此配置创建新版本</button></div>
 <TemplateForm kind={kind} source={template} readOnly/>
 <details><summary>原始配置 JSON</summary><pre>{JSON.stringify(template,null,2)}</pre></details>
 </section>;
}
