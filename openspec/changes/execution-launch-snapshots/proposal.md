## Why
运行配置只能显示提交快照与映射规则，无法证明节点完成映射、逐回合改写、路径解析后究竟启动了什么程序。需要独立保存实际启动快照，排查 taskname、episodes、seed、运行账号与环境差异，同时避免把 Runner 包装命令误认为业务命令。

## What Changes
- 在业务进程创建边界采集最终 argv、cwd、UID/GID 与账号、环境来源、允许展示的环境摘要、采集时间及版本；脱敏后持久保存。
- 按 execution_id、scope、episode_id、launch_id 区分任务进程、逐回合进程与服务实例，保留准备/创建成功/创建失败证据。
- Worker 以独立、有界且可重试通道上报，平台保存元数据，节点离线及日志淘汰后仍可查看已上报快照。
- 运行配置保留原提交快照；提供实际启动快照入口，按执行尝试/回合/服务实例查看，历史无记录与未启动不同。
- 同步修正映射展示中总回合/基础 seed 与逐回合启动值的解释，以及已知任务 ID 被标为启动时解析的问题。

## Capabilities
### New Capabilities
- `execution-launch-snapshots`: 真实业务进程启动参数采集、上报、历史持久化、鉴权与查询展示。
### Modified Capabilities
无。现有 platform 主规范未覆盖此能力；与活动 batch-run-configuration 变更保持提交快照/实际启动信息边界一致。

## Impact
Runner 的 cs_entry/service_entry/host_episode/command_sdk 与进程创建边界；Worker 上报及能力协商；API、独立数据库表和 Alembic 迁移；前端运行配置与执行详情、回合和服务实例入口。需要 API/前端部署和 Worker/平台 Runner 更新；旧式自带 Runner 的镜像需重建或明确不支持。用户指南、升级迁移文档、Skill 与覆盖率测试同步。此次只是规划，不宣称功能已上线。
