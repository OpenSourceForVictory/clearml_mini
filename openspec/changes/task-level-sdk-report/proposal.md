# Proposal

## Why
当前 Runner SDK 面向逐回合评测：SDK 自己执行回合循环并逐项上报。用户已有的整任务业务程序往往只会在结束时得到一个业务结果，不应为了接入平台而拆成虚构回合或自行实现 Worker/API 鉴权。

## What Changes
- 增加可选的 `sdk-task-result` 接入方式，普通整任务业务进程可调用 SDK 一次上报成功/失败及有限数值指标。
- 业务结果通过当前执行专属的本地 journal 传给 Worker；Worker 使用自身凭据转发，业务进程不接触平台 token。
- API 按执行尝试保存、校验及幂等处理任务级结果；任务退出码与业务结果分别保留和展示。
- 增加并发执行隔离、重复上报、缺报、错误负载及 Worker 重试覆盖，并更新接入指南和示例。

## Capabilities
### New Capabilities
- `task-level-sdk-report`: 不带回合明细的任务级业务结果契约。
### Modified Capabilities
- `cs-evaluation`: Worker journal 转发、执行结果完成校验及结果读取。

## Impact
涉及 Runner SDK、Worker、API、评测模板/结果界面、接入示例与用户指南。复用既有模板 `integration_mode` 字段与 Run JSON 结果字段，不新增数据库迁移；旧模板、逐回合 SDK、直接协议及 Docker/宿主机执行保持兼容。
