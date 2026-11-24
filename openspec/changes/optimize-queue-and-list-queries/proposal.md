# Proposal

## Why

隔离 PostgreSQL 数据验证显示：1 万排队任务与 1 万历史批次在资源池不匹配时，100/500 节点的每轮调度约 0.58/2.42 秒；节点列表分别执行 201/1001 条 SQL。列表和详情轮询还会读取未展示的任务、回合与全平台资源，放大普通 API 负载。

## What Changes

- 按用户最新确认，本次仅优化界面读取；调度候选分组、排序、资源快照和预约全部推迟到独立变更。
- 节点列表批量查询卡位与占用数；批次列表增加兼容的摘要模式，详情仍按需读取。
- 详情页按需建立诊断上下文，服务状态提供轻量视图；隐藏页签停止轮询并避免重复订阅，保留界面状态。
- 补充查询增长断言、生命周期回归、浏览器请求次数测试和相同测试数据的前后性能对照。
- 本次不改变历史编排是否参与调度、不改变全局锁范围、租约或断联策略，不新增持久化摘要缓存。

## Capabilities

### New Capabilities

- `runtime-list-summaries`: 批次和服务状态的轻量查询契约、按可见内容查询的详情页行为，兼容原完整接口。

### Modified Capabilities

无。调度优化保持已有 batch-priority 和资源预约行为。

## Impact

涉及 backend/app 的 administration、execution、batches、monitoring、preflight、orchestrations、services，及 frontend/src 的 cs、batch-history、batch-detail、orchestration-status、poll 和对应测试。按实际改动同步 docs/gpu-scheduling.md、batch-monitoring.md、operations.md、界面指南及项目 Skill 引用索引。预期无需数据库迁移或 Worker/Runner 升级；不在本变更中部署未经验证的代码。
