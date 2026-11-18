## Why
根路径 WS 被转换为 HEAD，现场返回 unsupported http method: HEAD / ClientResponseError 400，导致可达服务被阻止启动客户端。
## What Changes
- WS 全部路径仅检查 TCP，WSS 检查 TCP/TLS；不发 HEAD 或业务握手。
- 保留总超时、地址校验、不使用代理和诊断日志；HTTP(S) 行为不变。
## Capabilities
### New Capabilities
- `ws-transport-reachability`: WS 客户端传输连通性检查，与业务就绪分离。
### Modified Capabilities
无（已有 client-connectivity 变更尚未同步到主规范）。
## Impact
worker/client_connectivity.py、专项测试、指南、运维文档及 Skill；更新客户端 Worker，无迁移。
