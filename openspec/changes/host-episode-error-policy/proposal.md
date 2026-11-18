## Why
宿主机逐回合任务在首个进程退出非零时立即终止，无法完成其余独立回合。用户需要可选择记录错误并继续。
## What Changes
- 客户端宿主机逐回合模板新增回合错误策略，默认 stop，允许 continue。
- continue 保留 ERROR/退出码并执行后续回合，有错误时最终任务仍失败；正常业务 success=false 不属于执行错误。
- 旧模板及 Docker 行为不变，旧 Worker 拦截新策略。
## Capabilities
### New Capabilities
- `host-episode-error-policy`: 宿主机逐回合错误处理与最终失败语义。
### Modified Capabilities
无。
## Impact
共享 host_config、SDK 回合循环、模板界面及调度兼容检查。Worker 协议17/0.20.0，无数据库迁移。专项测试和文档同步。
