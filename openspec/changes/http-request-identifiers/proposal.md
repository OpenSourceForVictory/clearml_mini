## Why
SSH 注册直接调用 crypto.randomUUID，在 HTTP IP 访问时不可用，导致请求未发出。
## What Changes
统一前端请求 ID 生成，原生 randomUUID 不可用时用 getRandomValues 构造 UUID v4，覆盖纳管与运行向导。
## Capabilities
### New Capabilities
- `request-identifiers`: 浏览器环境兼容请求标识。
### Modified Capabilities
无。
## Impact
仅前端及关联测试、指南，无数据库或节点协议变更。
