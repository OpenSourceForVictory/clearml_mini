## Why
当前批次“运行参数”只展示单个任务的少数字段和 JSON，无法像提交向导一样核对集合、客户端、服务端和资源配置。用户需要查看本次实际提交的配置，而非后来修改过的模板。

## What Changes
- 将批次“运行参数”升级为“运行配置”，按向导分组提供只读详情：任务集合、客户端、服务端、资源与执行限制。
- 提供任务切换，显示各任务实际参数、回合、种子、模板快照、执行模式与环境来源。
- 展示托管服务定义（含实例数、参数、资源、就绪策略）及外部服务候选地址；实际分配另作标识。
- 不重跑预检，不把当前诊断冒充提交时结果；旧快照缺少字段明确标为未记录。

## Capabilities
### New Capabilities
- `batch-run-configuration`: 批次历史运行配置按提交向导结构可读地展示。
### Modified Capabilities
无。

## Impact
frontend/src/batch-parameters.tsx、batch-detail.tsx、样式与批次专项测试；复用 jobs.client_spec 与 orchestration.definition，按需补充只读响应。同步 docs/batch-monitoring.md、界面指南与 Skill 索引。预计不涉及数据库迁移、Worker 或调度行为变更。
