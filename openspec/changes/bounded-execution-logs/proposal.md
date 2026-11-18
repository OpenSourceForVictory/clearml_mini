# Proposal

## Why
当前宿主机只保留每个输出流最前面的 256 KiB，后续故障日志会丢失；Worker 日志上传与运行心跳串行，慢日志请求可能消耗租约时间。日志写入还持有全局调度锁，任务增多时会放大平台压力。

## What Changes
- 宿主机及 Docker 日志采用有界滚动保留，界面能看到最新错误并明确提示已淘汰或采集缺口。
- 日志增量、合并、限速上传；日志网络等待不阻塞执行心跳与取消。
- 日志专用状态与单执行锁，避免每个日志块争抢全局调度锁；保留鉴权及重放幂等。
- 客户端和服务实例统一游标读取，关闭日志面板后停止轮询。
- 执行结果、回合事件与展示日志保留策略分离；兼容旧 Worker 和历史日志。

## Capabilities
### New Capabilities
- `execution-log-streaming`: 有界执行日志采集、上传、读取与故障隔离。
### Modified Capabilities
无（当前主规范未提供独立执行日志能力）。

## Impact
Worker client/host supervisor/backend、Docker 输出采集、Runner 结果持久化、API 日志存储与删除清理、前端两种日志面板及相应测试、运维指南和 Skill。新增日志协议及数据迁移，先更新 API 再更新 Worker/Runner；不改变业务成功判定、执行时限或资源释放条件。
