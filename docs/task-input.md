# 用户程序输入与参数适配

参数契约基础模块已实现；向导、调度和执行器完整接入以OpenSpec任务进度为准，不能仅凭本地dry-run视为已上线。

普通程序可读取`MINI_EVAL_INPUT`指定的JSON文件，不需要平台账号或调度API：

```python
import json, os
with open(os.environ['MINI_EVAL_INPUT']) as f:
    config = json.load(f)
business = config['business']
task = config['task']
service = config['platform']['service']  # 无服务任务为None
output = config['platform']['paths']['output']
```

使用SDK时，`runner.task_input.read()`支持新版输入及旧CS输入转换；现有`EvaluationClient`可使用`context.business`和`context.service_endpoint`。不要自行选择机器或替换平台分配的服务地址。

模板作者可将平台字段显式映射到既有程序参数：

```json
[
  {"source":"platform.service.endpoint","target":"argv","name":"--server"},
  {"source":"task.task_name","target":"argv","name":"--task"},
  {"source":"platform.paths.output","target":"env","name":"BUSINESS_OUTPUT"}
]
```

业务字段继续使用`parameter_schema`声明类型、默认值、必填及`binding`。argv按数组传递，不经过shell；对象/数组需要显式JSON编码或repeat数组参数。平台与业务映射不能重复占用相同参数目标；环境变量不能覆盖平台保留字段及设备可见性变量。

客户端参数顺序为模板默认、集合默认、case参数、本次显式覆盖。只有`overridable=true`字段接受本次运行覆盖；对象整体替换。服务端独立解析，不受客户端同名参数影响。参数来源和有效值由解析器同时返回。

本地检查（只验证和打印，不执行程序、不连接平台）：

```sh
python -m runner.task_input input.json --dry-run
```

输入文件可使用现有CS格式，工具会转换为带`input_version=1`的platform/task/business结构。正式执行还要检查模板权限、节点资源和服务健康；本地检查不会预留机器。
