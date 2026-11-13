# 模板业务参数契约：第一阶段

当前交付纯校验/绑定模块 `runner.parameter_contract`。尚未接入模板 API、集合提交、Worker 或管理界面；不能把下例直接提交给现有模板 API。参数契约第 1 版与现有 CS 协议版本独立。

## 调用边界

- `validate_schema(schema)`：校验并返回独立副本，检查字段类型、默认值、绑定及保留字段。
- `resolve_values(schema, values, overrides)`：模板默认值 → 集合任务取值 → 本次运行覆盖。只有标记 overridable 的字段允许本次覆盖。
- `bind_values(schema, values, command, env)`：返回独立 argv 与用户环境变量字典，不执行程序、不连接平台、不读取节点配置。调用方随后注入平台保留环境；不能把平台环境传入本函数当成用户配置。

业务取值的标准 JSON 输入是数据来源，argv/env 仅为传递形式。调用方必须保存解析后的快照，再由后续执行阶段使用；不能在任务重试时重新查询模板默认值。

```python
from runner.parameter_contract import resolve_values, bind_values

schema = {
    'version': 1,
    'fields': {
        'scene': {
            'type': 'string', 'required': True,
            'binding': {'target': 'argv', 'name': '--scene'},
        },
        'difficulty': {
            'type': 'enum', 'enum': ['easy', 'hard'],
            'default': 'easy', 'overridable': True,
        },
        'render': {
            'type': 'boolean', 'default': False,
            'binding': {'target': 'argv', 'name': '--render', 'encoding': 'flag'},
        },
    },
}
values = resolve_values(schema, {'scene': 'room one'}, {'difficulty': 'hard'})
argv, env = bind_values(schema, values, ['python3', 'simulation.py'])
# argv: ['python3', 'simulation.py', '--scene', 'room one']
# values 中仍包含 difficulty 和 render，供标准输入 JSON 使用。
```

## 已实现约束

支持 string/integer/number/boolean/enum/object/array；object 必须声明 properties，array 必须声明 items，嵌套深度有界。数值拒绝 NaN/Infinity，布尔不能冒充整数。字段提供 label/description、required/default、min/max、max_length、overridable。

缺失可选值保持缺失，空字符串保持空字符串，null 不等同缺失并被拒绝。类型及编码不存在隐式转换，未知字段拒绝。业务数据整体不超过 16 KiB，校验错误指出字段路径但不回显业务值。

绑定默认为 input；argv 支持 literal/json/boolean flag/scalar-array repeat；env 支持 literal/json。结构化数据必须明确编码，重复绑定目标和与已有用户环境变量冲突均拒绝。参数值原样放入 argv，不拼接 shell。

`task_name/seed/episodes` 等既有任务计划字段暂保留在标准任务输入中，不在自定义 parameters 中重复定义。工作目录、执行身份、平台保留变量和 GPU 可见性禁止由自定义业务字段覆盖。

## 未完成的关联工作

模板数据库/API 持久化、契约版本迁移、旧模板兼容入口、集合的逐项校验与快照、运行时能力门槛、资源需求映射、容器挂载及界面仍由 OpenSpec 9.1—9.5 跟踪。此模块不单独部署或声称新模板已可运行。
