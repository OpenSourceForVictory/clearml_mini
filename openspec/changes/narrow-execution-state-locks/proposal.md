# Why

用户要求统一审视心跳、恢复上报与调度器锁竞争，避免每处加等待。启动确认及单执行控制不分配/释放资源，却竞争全平台锁；恢复Run使用FOR UPDATE还可能阻塞日志外键检查。取消入口仅依赖全局锁，去掉上报全局锁前必须统一执行行的同步。

# What Changes

- 核查并记录各路径的锁目的、顺序与边界。
- 启动确认心跳、原执行启动许可采用当前Run行同步；取消共用函数锁定并刷新最新Run/Job后作决定。
- 恢复上报对单执行状态使用Run→grant行锁及NO KEY UPDATE；只在可能撤销服务就绪的过期恢复保留全局锁，并复查临界边界。
- 保留资源分配/清理释放、服务就绪失效与跨任务操作所需的互斥，不直接取消调度锁。
- 区分全局锁和执行行锁的繁忙原因，真实PG验证并发竞态和混合负载，文档与发布同步。

# Capabilities

## New Capabilities
- execution-state-locking: 单执行状态与资源互斥分离。

# Impact

execution、recovery_control、recovery_launch、batch_controls及锁诊断、相关测试/运维文档。无迁移，不改Worker协议字段、取消优先级或调度资源算法。
