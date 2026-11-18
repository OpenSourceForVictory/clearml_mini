## Why
类型化参数与环境变量拒绝 task-config，导致业务现有名称无法直接接入。
## What Changes
业务参数及对象属性允许中划线，环境变量配置和绑定也允许中划线；名称不自动改写，平台保留变量保护不变。
## Capabilities
### New Capabilities
- `hyphenated-business-parameters`: 中划线参数及环境变量端到端传递。
### Modified Capabilities
无。
## Impact
前端编辑器、共享 Runner 契约、专项测试和操作说明。不迁移数据库；新名称需要节点使用新版共享契约。已有配置继续兼容。
