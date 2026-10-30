# Why
用户要求通过 3 项 RoboTwin 集合、多批次提交，在两台真实 GPU Worker 上验证并发执行与排队。
# What Changes
- 提供明确标为无模型环境检查的 RoboTwin SDK 客户端和三任务集合。
- 分发客户端镜像与已校验的 demo_clean 所需资产，两台 Worker 登记同一镜像。
- 提交三个批次，收集并发、排队、接续调度与任务完成的真实证据。
# Capabilities
## New Capabilities
- `robotwin-queue-validation`: 两台 GPU 主机的真实仿真环境排队验收。
# Impact
新增样本与现场验证记录，复用现有调度与 SDK，不修改真实 Cosmos 推理模板互斥行为。
