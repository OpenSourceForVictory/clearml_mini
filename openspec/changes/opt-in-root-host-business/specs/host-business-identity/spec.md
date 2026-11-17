## ADDED Requirements
### Requirement: Explicit node-approved root host business identity
平台 SHALL 仅在节点本地明确批准时以 root 运行宿主机业务，不允许任务或模板选择 root。

#### Scenario: Explicit root approval
- **WHEN** root 系统 Worker 的 host_execution 同时批准 root 身份与 allow_root_business
- **THEN** 宿主机探测和业务 systemd 单元使用 root，仍应用任务资源与设备限制

#### Scenario: Missing or conflicting approval
- **WHEN** root 用户缺少显式标志、普通用户误设 root 标志，或 NPU 身份与 host_execution 不一致
- **THEN** 环境探测及业务启动拒绝该配置

#### Scenario: Existing node
- **WHEN** 节点没有 allow_root_business 字段
- **THEN** 保留普通账号行为，重新纳管不自动切换身份
