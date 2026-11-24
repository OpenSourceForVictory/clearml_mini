## Why
恢复服务在提交后查询完整详情，可能返回 request_budget 而实际已经成功。重新评测仍与日志共享普通连接池，批量重试还加载整个批次的历史。

## What Changes
- 重新评测路由与网关同步进入控制通道。
- **BREAKING** 服务控制返回简短确认（id、revision、current_job_id、action、accepted），详情通过现有 GET 获取；内置界面不依赖原响应详情。
- 批量重试仅查询选中任务，保留完整尝试关系和清理、权限、配额与重放保护。

## Capabilities
### New Capabilities
- control-operation-acknowledgement: 控制操作隔离及事务确认。
### Modified Capabilities

## Impact
后端路由、网关配置、服务控制及批量重试；无数据库迁移，无 Worker 升级。保持全局调度锁，不改变分配算法、租约、容量或请求预算。监控 GET 优化单独跟踪。
