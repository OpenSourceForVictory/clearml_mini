## Why
客户端/服务端编排预检把不同的结构性容量冲突压成一条英文错误，无法区分服务端池选择、单卡独占、同机并发和 CPU/内存/显存额度。
## What Changes
保持原有准入判定，返回具体配置原因和两侧资源池名称；界面按原有错误通道展示，指南给出对应操作。
## Capabilities
### New Capabilities
- `capacity-diagnostics`: explain structural client/server pair rejection
### Modified Capabilities

## Impact
编排预检、提交错误、操作指南、Skill 参考与回归测试。
