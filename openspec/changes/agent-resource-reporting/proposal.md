# Proposal
## Why
整机资源被 Agent 和 Worker 重复探测，Worker 的 NFS 检查及 Agent 状态落盘位于心跳路径，慢设备可能影响纳管和调度。需要建立统一资源快照和独立执行控制接口。
## What Changes
- Agent 发布带版本、时间和设备身份的 CPU、内存、GPU 快照。
- Worker 心跳使用后台采集/控制缓存；Agent 事件确认异步持久化，保留落盘后确认语义。
- 后端协商资源来源，阻止旧 Worker 覆盖 Agent 数据；调度使用有效 CPU、内存快照保护准入。
- 增加慢探测、慢落盘、旧协议、多 GPU 和数据过期测试及迁移说明。
- 增加厂商中立设备清单与昇腾910B3只读采集适配器，明确采集就绪不等于NPU任务执行支持。
## Capabilities
### New Capabilities
- node-resource-reporting: 节点资源快照、来源协商与非阻塞上报。
### Modified Capabilities
无。
## Impact
node_agent、worker 控制循环、后端 Agent/执行协议和调度准入；不修改执行日志格式和现有每卡 Worker 单元布局。
