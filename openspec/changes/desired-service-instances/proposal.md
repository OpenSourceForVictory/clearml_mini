## Why
实例上限不代表启动数量，共享实例可被持续复用导致用户设置 2 却只启动 1。
## What Changes
新请求 instance_count 表示目标实例数；保留旧 max_instances 的兼容语义。调度补足未创建数量，资源不足显示缺口与诊断。详情逐实例展示和计数。
## Capabilities
### New Capabilities
- `desired-service-instances`: 服务目标数量与实际调度状态。
### Modified Capabilities
无。
## Impact
API 请求与 JSON 快照、调度、向导、详情和测试文档；服务数量本身不需迁移；追加实际退出码展示需要迁移 x407a89，Worker/Runner 增加可选退出码上报（Worker 0.18.2，兼容协议 15）。
