# Proposal

## Why

现场三个业务回合以 0 退出，却因未得到唯一 final 结果被统一显示为执行异常。用户无法区分程序运行、协议校验与业务成功；重复堆栈也没有给出可操作的修复提示。

## What Changes

- 明确分类缺失 final、重复 final、final 结构或字段不合法，给出固定、可操作且不包含业务输出的诊断。
- 分开统计程序执行与平台协议回合结果；退出码 0 计入程序正常结束，但不替代协议结果。普通命令的协议统计显示不适用。
- 运行批次结果页、批次列表、任务行和回合详情统一展示两种结果；批次按各任务最新尝试汇总，不混入重试历史。
- 保留逐回合继续/停止策略及普通命令退出码语义，修正已运行回合被称为 startup failed 的误导性提示。
- 增加真实本地子进程、事件/API 和界面回归测试，保留现场失败证据。

## Capabilities

### New Capabilities
- `episode-result-diagnostics`: 逐回合程序退出与协议结果错误的分类、传递和展示。

### Modified Capabilities
无。

## Impact

runner/host_episode.py、runner/sdk.py、客户端启动错误包装、frontend/src/cs.tsx、batch-detail.tsx、batch-history.tsx 及后端批次汇总接口；检查事件透传与存储，无数据库结构迁移计划。同步宿主机运行文档、界面指南及项目 Skill 参考索引。需更新实际执行节点的 Worker/Runner 和平台界面；发布不属于本次设计授权。
