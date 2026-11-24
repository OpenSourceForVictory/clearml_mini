## Why
批次监控频繁失败，固定两秒重试进一步放大普通 API 压力；大量托管任务的相同候选查询在单次诊断中重复执行。
## What Changes
- 批次监控正常五秒刷新，失败指数退避至30秒，尊重 Retry-After；后台标签页暂停通用轮询，恢复可见后立即读取，保留已有数据并显示过期提示。
- 仅在单次诊断上下文复用同批次同服务集合的候选与占用计算，下一请求重新检查。
## Capabilities
### New Capabilities
- monitoring-backpressure: 监控读取降载、失败退避与请求内候选复用。
### Modified Capabilities
## Impact
frontend api/poll/batch-detail；backend preflight 只读诊断。无迁移，无 Worker 更新；不改变调度算法、锁、心跳预算或连接池。
