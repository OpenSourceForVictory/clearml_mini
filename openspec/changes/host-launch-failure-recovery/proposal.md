# Proposal

## Why
用户反馈同节点服务实例数为 2 时第二个宿主机实例出现 missing unit。当前双实例 API 与真实 systemd 运行不能复现现场，但已复现首次启动失败、失败结果上报暂时不可用后，恢复把原始错误覆盖为 missing unit 的缺陷。

## What Changes
- 失败完成请求先持久化，恢复时只重试原失败完成及清理，不重新创建业务。
- 宿主机 missing unit 错误附带安全身份信息和判定来源，任务下发携带服务实例 ID。
- 补充同节点双实例、失败结果重放与独立清理测试，明确现场根因尚未确认。

## Capabilities
### New Capabilities
- `execution-failure-recovery`: 终态失败报告的持久恢复与执行身份诊断。
### Modified Capabilities
无。

## Impact
Worker 执行 journal、API claim 元数据、宿主机运维说明、测试。无需数据库迁移，不删除防重复执行保护，不改资源限制或服务实例数语义。
