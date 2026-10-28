## MODIFIED Requirements

### Requirement: 异构 GPU 槽位
系统 SHALL 允许单卡或多卡机器逐卡注册 Worker 槽位，以 GPU UUID 防止重复注册，并为无 GPU 的 mock 评测提供虚拟槽位。

#### Scenario: 多卡机器
- **WHEN** Worker 在同一机器上报告两张不同物理 GPU
- **THEN** 系统保存两个独立槽位，可并行运行两个单卡任务

### Requirement: 可恢复投递
系统 SHALL 使用 PostgreSQL 持久化任务分配、attempt、execution ID 和租约，并拒绝失效执行令牌提交的结果。

#### Scenario: Worker 在运行中断联
- **WHEN** 心跳和租约过期
- **THEN** 系统停止对该槽位准入新任务，并在宽限期后对账或结束原尝试

#### Scenario: 投递期间重启
- **WHEN** 调度器在持久化分配后重启
- **THEN** 调度器按 execution ID 和租约恢复原尝试，不重复分配

## ADDED Requirements

### Requirement: 无 ClearML Server 的执行链路
系统 SHALL 仅依赖 API 与 PostgreSQL 控制任务队列；Worker SHALL 从 API 拉取任务，并在管理员固定的 Runner 镜像中执行。

#### Scenario: mock 评测
- **WHEN** 用户提交 mock 任务且有在线兼容槽位
- **THEN** Worker 认领任务、运行 mock Runner，并回报确定性的结果和有限日志

### Requirement: Worker 身份隔离
系统 SHALL 使用独立 Worker 凭据认证心跳、认领和结果上报；普通用户 JWT 不得调用 Worker 端点。

#### Scenario: 用户尝试伪造结果
- **WHEN** 普通用户令牌调用 Worker 完成接口
- **THEN** API 拒绝请求且任务状态不变
