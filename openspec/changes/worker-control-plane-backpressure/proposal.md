# Proposal

## Why

Worker 领取、旧日志、回合和产物接口会持有数据库连接阻塞等待全局调度锁；同机心跳与 Agent 上报也竞争行锁。隔离 PostgreSQL 已复现两个阻塞请求填满两连接池后，心跳无法获取连接；当前无法据此宣称任何生产 Worker 容量上限。

## What Changes

- 对高频请求提供有限数据库等待与明确的可重试响应，减少占连接排队。
- 为续租及必要控制流量保留独立连接预算，对日志和产物等流量实施有界并发。
- 审查心跳行锁和调度事务，保持配置一致性、租约、清理和幂等语义。
- Worker 区分可合并状态与可靠事件；有界退避、随机延迟和租约感知重试。
- 提供真实 PostgreSQL 混合负载压测，公开容量测试条件和失败证据。

## Capabilities

### New Capabilities

- `worker-control-plane-backpressure`: 控制面上报的过载隔离、可靠重试和容量验证。

### Modified Capabilities

无。

## Impact

API 数据库 Session、调度锁、execution、Agent、服务探测、日志、产物及 Worker HTTP 调用链；同步运维、节点升级、日志/产物说明和 Skill 参考。预计无需表结构迁移；需要更新 API 和 Worker，旧 Worker 仍须可用，Docker 与宿主机执行协议不变。不以扩容连接池替代消除锁等待，不增加消息队列依赖。
