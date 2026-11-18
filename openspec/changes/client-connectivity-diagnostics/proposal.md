## Why
客户端启动前检查抹去了所有异常原因，服务端收到 HEAD 405 时用户仍无法确认具体检查结果。
## What Changes
- 记录安全目标、检查方式、状态码、耗时和失败类型；失败原因同步进入任务错误。
- HEAD 405 继续通过，不改变 WS 握手、默认 3 秒、禁用环境代理及启动保护。
## Capabilities
### New Capabilities
- `client-connectivity`: 客户端启动前连通性诊断。
### Modified Capabilities
无。
## Impact
worker/client_connectivity.py、相关回归测试、指南、操作文档及 Skill；已纳管节点需更新 Worker，无数据库迁移。
