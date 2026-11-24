# Proposal

## Why

已在内存数据库复现：本轮新预约后模拟后续调度耗时 31 秒，默认 30 秒预约提交时已过期 1 秒。领取窗口不应被提交前的调度计算耗尽。

## What Changes

- 在调度事务提交前刷新本轮新建预约及本轮服务就绪后新开启的领取窗口，保留每种窗口原有时长。
- 不为既有预约续期，不改变排序、准入、锁、运行租约或执行超时。
- 增加时钟故障注入、历史预约及事务回滚测试，更新运维说明与 Skill 索引。

## Capabilities

### New Capabilities
- `reservation-lifecycle`: 明确未领取任务的预约期限及提交前等待窗口，不涉及已运行任务租约。

### Modified Capabilities
无。

## Impact

仅 API 调度模块、服务编排预约辅助逻辑及测试。无需数据库迁移或 Worker 协议升级。独立于 scheduler-equivalence-and-performance，不实施其其他优化。
