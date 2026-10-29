# Spec Delta

## Purpose
将 Cosmos causal action 与 RoboTwin 的闭环客户端接入平台任务、回合和结果管理，提供可复现的示例配置、服务并发约束及明确的测试与真实模型验收边界，减少新客户端的重复集成工作。

## ADDED Requirements

### Requirement: 单回合适配及实际种子
样本 SHALL 将每个平台回合映射为一次单任务/配置/控制模式的上游运行，校验一条正常完成结果，记录请求 seed、实际仿真 seed、跳过次数、目标成功和执行步数；未正常完成 SHALL 报错，不得以进程退出码替代仿真结果。

#### Scenario: 上游跳过不稳定种子
- **WHEN** 上游跳过初始 seed 并用后续 seed 完成
- **THEN** 平台结果包含实际 seed，下一回合从实际 seed 后继续，跳过记录不计完成数

### Requirement: 推理服务互斥
声明推理服务独占的模板 SHALL 阻止同一规范化端点上的重叠执行，包括不同用户和集合；未确认停止的执行 SHALL 继续持有端点。

#### Scenario: 多集合共享同一服务
- **WHEN** 两个集合指向同一独占服务
- **THEN** 同时最多一个子任务执行，其他任务保留排队

### Requirement: 可复现样本和诚实验收
样本 SHALL 提供固定上游版本、镜像配方、集合清单、EEF 配置要求和启动说明。完整轨迹 SHALL 保存在工作目录，有界摘要 SHALL 上传平台。缺少真实服务或仿真环境时 SHALL 明确尚未完成真实模型评测。

#### Scenario: 缺少仿真配置
- **WHEN** RoboTwin 辅助入口或 EEF 配置不存在
- **THEN** 预检明确失败，不启动伪造评测

### Requirement: 内置仿真环境
样本镜像 SHALL 内置固定版本 RoboTwin、SAPIEN、CUDA 运动规划依赖，无需用户预装 RoboTwin。官方机器人与场景资产 SHALL 在宿主机按固定版本后台下载，校验后原子发布，并只读挂载给容器；未完成版本 SHALL 不可用于评测。镜像构建 SHALL 不依赖资产下载完成。构建和不连接模型的 GPU 自检 SHALL 独立于真实推理服务与 EEF 配置。

#### Scenario: 新 Worker 使用样本镜像
- **WHEN** 具备兼容 NVIDIA 驱动和容器运行时的 Worker 拉取镜像
- **THEN** 可直接执行镜像依赖自检；宿主机资产就绪后执行 GPU 仿真初始化自检，真实模型评测另行提供服务地址和对应 EEF 配置
