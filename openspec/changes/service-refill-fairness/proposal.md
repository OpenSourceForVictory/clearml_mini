## Why
补齐显式服务实例数目前按批次 ID 排序，并让同一批次连续补齐，可能绕过批次优先级及跨用户公平性。已有隔离复现证明优先级 0 抢先于 9。

## What Changes
客户端准入之后的补齐阶段独立进行持久用户轮转，每个用户每轮最多新增一个实例；用户内部按批次优先级降序、创建时间及 ID 升序尝试，无法满足的候选不阻塞后续候选。

## Capabilities
### New Capabilities
- `service-refill-fairness`: 服务补齐的公平排序与原子游标。
### Modified Capabilities

## Impact
仅修改 orchestration_scheduler 补齐流程及相关测试、指南。复用 SchedulerState，无数据库迁移或 Worker 协议更新。不改变客户端准入优先于补齐、全局锁、资源账本与清理规则。
