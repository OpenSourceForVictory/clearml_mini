## Why

宿主机任务很快退出时，systemd 可能已卸载 transient unit，但 Worker 本地仍有启动记录与退出结果。现有恢复逻辑把这种情况判为执行单元丢失，报“previously created container is missing”，既误报容器，也丢失可确认的业务退出结果。

## What Changes

- 宿主机执行单元不存在且本地启动记录与退出记录都匹配当前执行时，按已退出任务恢复并完成结果上报。
- 没有可信退出记录时继续拒绝重复启动；报错按 Docker 容器或宿主机单元区分。
- 增加快速退出、缺失退出记录和身份不匹配的回归，并同步宿主机运行说明。

## Impact

Worker 宿主机恢复与执行错误文案；不改 API、数据库、任务重试身份或 Docker 重建策略。
