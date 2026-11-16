## Why
宿主机业务常使用独立 Python 环境并依赖代码目录中的相对配置文件，当前 cwd 固定为平台任务目录，无法直接表达此运行方式。
## What Changes
- 客户端和服务端 host_runtime 增加可选 working_directory，独立于解释器和任务工作/输出目录。
- 支持批准代码根目录及其子目录，绝对路径须位于批准根目录内；默认保持原行为。
- 注册、复制和只读配置展示新字段；协议门槛阻止旧 Worker 静默忽略。
- 实际进程测试不同解释器路径、cwd、相对文件，更新用户指南、Skill 和 SDK 包。
## Capabilities
### New Capabilities
- `host-working-directory`: 宿主机业务执行目录与解释器独立配置及兼容准入。
### Modified Capabilities
无。
## Impact
模板契约、宿主机 Worker、三个 runner 入口、调度准入及模板 UI。不新增数据库列。
