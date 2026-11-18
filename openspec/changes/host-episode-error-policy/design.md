## Context
host_episode和command_sdk共用sdk.run，目前异常上报ERROR后重新抛出。详见proposal。
## Goals / Non-Goals
支持宿主机per_episode直接协议、普通命令和SDK命令；不自动重试错误回合，不改变Docker或整个任务模式。
## Decisions
host_runtime.episode_error_policy取stop/continue，只有per_episode可配置continue。SDK循环在continue时累积错误计数，输出异常至stderr后继续；循环结束存在错误则抛出汇总异常，不输出成功final。这样保留现有Worker非零退出=>FAILED与逐回合记录契约，无需新增终态或迁移。setup/teardown错误仍终止，BaseException及系统终止不吞掉。
模板编辑器条件显示策略，复制保留；切回whole_task时重置为stop。调度、预检及claim需协议17，Worker0.20.0。选择只提升软件版本而不门控会使旧Worker忽略新配置，故拒绝。
## Risks / Trade-offs
[错误回合后共享服务状态可能异常] → 用户显式选择continue，错误保留且最终失败，不承诺重置外部服务。
## Migration Plan
更新API和前端，再升级相关Worker；既有模板及任务不变。回滚前停止提交continue模板并排空相关任务。无需数据库迁移。

兼容补充：旧Worker的host_config拒绝未知字段，故stop在API保存时规范化为省略策略字段，只有continue保留并要求协议17。
