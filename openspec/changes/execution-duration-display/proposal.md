# Proposal

## Why

当前批次和回合没有统一时间展示，任务详情仅展示部分业务上报的 runtime，失败、普通命令和重试下信息不完整。用户要求整任务启动统计整次任务时间，逐回合启动同时统计整次任务与每回合时间。

## What Changes

- 批次显示历时（含排队），任务按执行尝试显示等待及执行耗时；详情解释口径、起止时间及来源。
- 根据实际进程启动方式显示回合耗时：宿主机 per_episode、宿主机/Docker sdk-command 显示；整任务启动不新增回合执行耗时，即使有 SDK/直接协议回合结果。
- Runner 自动生成逐回合计时，正常、非零退出和结果协议错误均保留时间；不要求业务程序新增计时上报。
- 运行中显示更新中的耗时，失败/取消/超时明确终止及缺失状态。重试相互隔离，历史缺失不补零、不从日志猜测。
- 新增可选计时元数据及能力协商，保留原结果协议/退出码/日志和 Docker 启动语义。

## Capabilities

### New Capabilities
- `execution-timing`: 批次、执行尝试与独立回合的耗时口径、采集、查询和展示。

### Modified Capabilities
无。现有 platform 的资源准入与可恢复投递要求保持不变。

## Impact

- 后端：main.detail、monitoring、episodes、result_views 及报告导出；新增共享时间投影模块，不改变调度超时。
- Runner/Worker：episode_contract、sdk、host_episode、SDK command 适配器及事件转发/下载 SDK 构建清单、版本与能力协商。
- 前端：批次详情、任务详情、回合表格和报告；更新用户指南、操作文档及 Skill 参考索引。
- 复用 Run/Event 时间和 EpisodeRecord JSON，不新增业务表或列；为按执行查找等待起点增加 job_events 复合索引迁移。旧节点继续运行，新增回合计时需要兼容升级。
