# Why
普通命令任务后端已支持，但注册界面默认评测类型，用户无法便捷注册并执行已有程序。
# What Changes
客户端模板增加评测/普通命令类型，联动接入与回合上报；向导默认无需服务；集合和自定义清单按普通命令一次执行生成兼容字段，说明退出码完成语义。
# Capabilities
## New Capabilities
- `command-task-authoring`: 普通命令任务界面接入。
## Modified Capabilities
无。
# Impact
前端模板、帮助文案、集合和向导，以及浏览器测试；后端执行契约不变。
