# Proposal

## Why

单个 Uvicorn 进程虽然有入口分类、线程许可和数据库池预算，普通请求与执行控制仍共用事件循环、Python 进程和线程池。日志、产物和 Web 负载可能间接拖慢续租及服务状态确认。需要提供可选的进程级部署隔离，同时保持现有外部地址和端口。

## What Changes

- 增加可选 Compose 分组部署：统一网关按服务端定义的控制路径转发到独立控制 API，其他流量进入普通 API；两个进程使用各自线程池。
- 只允许普通 API 运行后台调度器，控制 API 不重复启动调度器；各进程仍使用有界数据库池，合计预算按真实进程数校验。
- 对错误路由和网关故障采取明确失败行为，避免控制请求退化到普通 API；保留单进程部署作为默认。
- 补充路由、部署配置、健康检查和双进程混合负载专项测试及运维指南。

## Capabilities

### New Capabilities

- `split-control-api-process`: 可选的执行控制进程隔离与不变的外部入口。

### Modified Capabilities

无。执行语义、Worker 协议、数据库结构和单进程默认部署不变。

## Impact

影响部署 Compose、HTTP 网关、入口分类及 API 启动配置；不要求数据库迁移或节点升级。启用背压配置的前提仍须满足既有 Worker 兼容要求。与 `worker-control-plane-backpressure` 的进程隔离后续决策衔接，不能把短时模拟压测当作真实业务容量验收。
