# 模板业务参数契约：第一阶段

当前代码已将 `runner.parameter_contract` 接入客户端模板 API、集合提交、任务快照和客户端执行入口。新增模板字段 `parameter_schema`，任务字段 `parameters` 与 `parameter_overrides`；参数契约第 1 版与现有 CS 协议版本独立。此阶段尚未部署，管理界面已提供客户端类型化编辑器，整体尚未部署。

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

已实现：客户端模板 parameter_schema 可选持久化（迁移 m285b79）、API 注册和克隆、集合逐项校验、批次全部校验后入队、最终参数快照，以及 Docker/host 客户端入口和 SDK 命令适配器的绑定。旧模板 schema 为 null，继续使用原 parameters 规则。类型化任务只能由 Worker v8 领取；API 同时保留 v1—v7 注册兼容。前端可编辑 schema，复制模板也保留其定义。

服务端模板同样支持 parameter_schema。部署时校验 parameters 和 parameter_overrides，保存有效参数及模板快照；重启沿用快照，Worker 8 才能执行类型化服务。资源需求映射、运行参数分层、通用目录/设备挂载仍由 OpenSpec 9.1—9.5 跟踪，不能视为整体完成。

注册客户端模板时将上述 schema 作为 parameter_schema 字段传入；业务取值放在 client.parameters 或集合 cases[].parameters，运行覆盖放在 client.parameter_overrides。类型化模板自动启用平台运行适配器注入，防止用户镜像内旧 SDK 忽略新绑定。模板环境变量/允许覆盖变量不能与业务 env 绑定重名。

验证：隔离 PostgreSQL 从 l285a67 升级到 m285b79，历史模板 schema 保持 null，新 schema 正常读写；接口集成和真实业务子进程测试验证批次原子性、快照、版本门槛、SDK 每回合取值、直接协议 argv/env 传递。尚未在五台生产 Worker 部署 v8，不宣称完成真实两节点挂载验收。

本轮验证记录：87 项后端测试通过（参数契约、模板/集合/调度、SDK、预检和执行恢复），2 项模板界面测试通过，TypeScript/Vite 构建及 OpenSpec strict 通过。为避免与工作区尚未提交的服务实例代码混淆，另对暂存区导出的独立代码快照执行模板与参数链路回归。数据库迁移仅在一次性测试库执行，生产未升级。

## 类型化编辑界面

模板注册分基础信息、平台参数、业务参数和运行参数。客户端可添加/删除字段，配置七种类型、必填、默认、范围、长度、覆盖权限和 input/argv/env 绑定；对象 properties 与数组 items 暂采用 JSON 结构编辑。关闭类型化模式保留编辑草稿，但提交使用旧版自由参数模式。改变字段类型会清除不兼容的默认值、约束和绑定，界面已提示。

支持契约预览、重复名称/保留字段/绑定错误/默认值类型的提交前反馈。容器工作和输出目录可配置，Docker/host 切换保留业务参数和目录草稿。公共 Field 组件独立，避免模板/编辑器反向依赖整个管理模块。

本轮隔离环境验证真实浏览器创建模板 → API 保存 → 两任务批次 → 两份不同参数快照；未写入生产数据，截图在 `.runtime/typed-editor/editor.png`。该轮验证时服务端业务参数尚未接入；后续已补服务端参数回归。通用设备挂载、资源映射和五节点升级仍未完成。
