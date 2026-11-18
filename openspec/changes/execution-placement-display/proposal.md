## Why
运行结果未明确展示服务端节点和加速卡，客户端位置不明显且 NPU 被标为 GPU。
## What Changes
- 批次客户端及服务实例返回最新尝试的实际分配位置，包含节点、设备类型、卡号和型号。
- 默认任务页、服务端摘要和关联详情明确显示两端位置；未分配不显示候选卡为实际位置。
## Capabilities
### New Capabilities
- `execution-placement`: 运行结果的分配位置可见性。
### Modified Capabilities
无。
## Impact
批次/服务只读接口、React 运行结果、相关测试与指南；无迁移、不改变调度。
