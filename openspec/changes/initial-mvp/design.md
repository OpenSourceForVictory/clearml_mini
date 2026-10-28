# Design

## Context

项目目前有数据库模型、部分 API、mock Runner、Compose 配置和容器构建验证；真实 ClearML、PostgreSQL 集成及 RoboTwin 仍未完成。需求和验收基线见 `IMPLEMENTATION_SPEC.md` 与 proposal.md。

## Goals / Non-Goals

**Goals:** 按 P0 → P1 → P2 交付可独立验证的功能增量；每次实现对照规范、任务与测试。真实 GPU 验收在软件功能完成后单独执行。

**Non-Goals:** V1 不支持单任务多 GPU、任意用户镜像或任意宿主机挂载。

## Decisions

1. **数据库先行，调度器独占投递。** API 只保存 Job/Run；调度器持有 Postgres advisory lock 并通过状态和稳定 attempt 标记恢复跨系统操作。直接在 API 中投递较简单，但无法可靠实施公平准入。
2. **每张物理 GPU 一个槽位、一个队列和一个 Agent。** 多卡机器逐卡注册；资源池表示能力匹配。共享队列无法保证任务落到兼容 GPU，因此 V1 不采用。
3. **真实适配器与 Fake 适配器分离。** CI 通过 Fake 测试状态和调度；真实适配器仅使用已验证的 ClearML SDK 方法。Fake 不得作为生产调度器默认实现。
4. **固定 Runner 来源。** ClearML Task 指向 Agent 可访问的 Git repo/commit 和经验证的镜像；API 只接收白名单参数。直接引用 API 容器内文件无法保证远程 Agent 可执行。
5. **国内构建源可覆盖。** 默认从已验证可访问的 DaoCloud 镜像代理和清华 PyPI 镜像构建；生产环境建议同步到自有镜像仓库并固定 digest。Docker daemon 全局配置不属于项目变更范围。

## Risks / Trade-offs

- [数据库提交与 ClearML 操作间崩溃] → 持久化 intent、稳定标记、先对账后重试，并用故障注入测试证明不重复执行。
- [同机多任务耗尽 CPU/内存] → 除 GPU 槽位外加节点并发上限与资源准入。
- [Worker 断联但任务仍运行] → TTL 只阻止新准入；运行任务按宽限期和执行超时处理。
- [RoboTwin 仿真环境难复现] → 固定上游版本、镜像、资产和 checkpoint 契约；真实 GPU 验收单列。

## Migration Plan

P0 完成迁移、认证/提交/只读 API、mock 和控制节点启动；取消/重试/日志/指标及其完整合同测试属于 P2。P1 接入真实调度与对账后才启用 scheduler 服务；P2 完成 UI 和运维功能。部署前备份 PostgreSQL；若新版本失败，停止新准入、回退 API/调度器镜像并运行兼容迁移回滚。不得通过清空数据库恢复。
