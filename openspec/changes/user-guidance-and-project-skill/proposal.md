## Why
用户缺少可以直接在平台阅读的操作指南与业务接入说明，Agent 也缺少可追溯的实现索引。用户不应为了理解平台而查找仓库文档。

## What Changes
- 增加界面内用户指南、搜索及当前功能操作说明，查看帮助不丢失表单草稿。
- 在指南内提供普通命令、SDK 和直接协议接入说明，以及可下载、可本地验证的 SDK 与样例包。
- 增加仓库项目 Skill 和模块索引；明确每次代码修改必须同步相关文档或 Skill。

## Capabilities
### New Capabilities
- `user-guidance`: 界面内操作帮助、业务协议接入与可运行样例获取。
### Modified Capabilities
无。

## Impact
前端导航与帮助组件、静态 SDK 分发、样例验证、文档和项目 Skill。不修改调度、鉴权、Worker 协议或数据库。
