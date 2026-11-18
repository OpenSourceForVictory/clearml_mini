## Why
客户端总时限 UI/模型硬编码一小时且未展示平台上限，执行时又静默收紧，三种超时混用提示。
## What Changes
- 认证接口公开客户端执行上限；提交向导读取并校验，失败可重试，不假定上限。
- 移除模型/UI 的 3600 秒常量限制，提交仍按平台上限拒绝，任务按已保存时限执行。
- 分开执行总时限、启动和心跳租约超时提示。
## Capabilities
### New Capabilities
- `client-execution-limits`: 客户端总时限的配置、准入与诊断。
### Modified Capabilities
无。
## Impact
API、调度、提交向导、测试及操作文档。无迁移，不扩大启动/租约默认值，不支持无限执行。
