## Why
计算资源页未区分客户端和服务端用途，统一 Worker 标签容易让用户误认为仅展示客户端。
## What Changes
- 增加全部、客户端、服务端资源分类及管理员角色明细。
- 普通用户保持汇总权限，双角色设备在全部中去重。
## Capabilities
### New Capabilities
- `resource-role-views`: 按机器角色查看计算设备。
### Modified Capabilities
## Impact
workers 查询参数、计算资源页、指南与回归测试；不改变调度或权限。
