# Proposal

## Why
用户指定 Cosmos causal action / RoboTwin CS 评测作为平台首个具体客户端样本，需要将上游任务、实际 seed、回合结果映射到平台 SDK，避免把数据回放或模拟环境测试误认为真实闭环评测。

## What Changes
- 固定 cosmos-framework 上游 commit，提供 SDK 适配器、镜像配方和可导入任务清单。
- 每次 SDK 回合调用上游单任务、单配置、单控制模式入口，保存实际仿真 seed、跳过次数及逐回合结果。
- 模板可声明推理服务独占，跨用户/集合限制同一规范化服务端点同时运行的任务。
- 将大视频/轨迹留在 Worker 执行目录，上传有界摘要；请求超时和总超时明确生效。
- 用上游模拟环境测试和适配器测试验收接入；真实推理地址、EEF 配置具备后单独验收模型闭环。

- 按用户补充要求构建完整 RoboTwin 客户端镜像，内置固定版本的仿真环境和 CUDA 运动规划依赖；官方资产在宿主机后台下载、校验并按版本只读挂载，不阻塞镜像和流程交付。

## Capabilities

### New Capabilities
- `cosmos-robotwin`: Cosmos causal action 仿真客户端样本、结果映射与服务互斥。

### Modified Capabilities
无。

## Impact
依赖已实现的 CS SDK/工作目录。新增样本代码、文档、模板字段与调度约束。模型推理服务仍外部维护，不在本变更部署模型权重。
