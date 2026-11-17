## Why
NPU 服务端机器计数沿用 GPU 标签，用户误以为设备类型识别错误。
## What Changes
机器管理客户端/服务端概览与列表统一使用加速设备名称，指南说明计数包含 GPU/NPU，数量不表示组件回连已通过。
## Capabilities
### New Capabilities
- `machine-device-labels`: 机器设备数量使用中性标签。
### Modified Capabilities
## Impact
仅前端文案与对应回归；保留已有 gpu_count 接口和计数语义。
