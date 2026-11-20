# Proposal
## Why
单一产物总目录目前仍需 JSON 映射、节点枚举与手工存储标识，部署繁琐。用户已确认仅设置 SHARED_ARTIFACT_HOST_ROOT 的方案并要求实施。
## What Changes
- 增加同路径总目录简化配置，原有主 Compose 传入变量并只读挂载，无需追加部署命令参数。
- 无高级映射时自动适用各节点；仍以执行登记和用户鉴权限定浏览。
- 高级映射优先，历史映射与标识校验保持不变。
- 来源选择不再自动跳回历史上传；无论文件读取是否可用，独立展示本次执行记录的输出目录。
## Capabilities
### New Capabilities
- shared-artifact-root-config: 单变量总目录部署和高级映射兼容。
### Modified Capabilities
无。
## Impact
API 共享文件映射、文件读取、领取任务、Compose、指南和测试；无数据库迁移，无 Worker 协议修改。
