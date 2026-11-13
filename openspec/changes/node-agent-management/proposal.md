# Proposal

## Why
现有逐 GPU Worker 同时负责主机监测、配置同步和任务执行，缺少节点级在线状态、统一程序管理和恢复边界。需要按机器建立 Agent，并在保留现有任务租约、SDK、调度账本的前提下逐步接管节点管理。

## What Changes
- 新增每节点唯一 Agent，模块化身份连接、监测、配置协调、受管程序、事件和本地恢复。
- 平台提供独立 Agent 状态、配置修订和受控程序启停接口；在线与可调度分别展示。
- 兼容现有逐 GPU Worker 作为受管执行组件，继续使用现有 Docker/host、SDK、服务实例、工作区及升级恢复协议，避免双重调度和重试。
- 安装包包含 Agent；本地状态不能依赖共享 NFS；纳管与升级需验证节点管理组件。
- 建立功能、接口集成、跨模块回归、真实进程及故障恢复测试矩阵。

## Capabilities

### New Capabilities
- `node-agent`: 节点级身份、状态、配置、受管执行组件及恢复。

### Modified Capabilities
无；平台通用要求保持兼容，节点级能力以新增协议引入。

## Impact
新增 node_agent 包、平台 Agent 协议及界面，调整安装/发布包和调度准入。现有 Worker、资产目录、GPU 身份、服务实例及回合协议保留。独立于尚未完成的网页 SSH 接入变更；generic-machine-runtime-management 任务 13 的详细工作由本变更承接。用户已授权规划及实施，完成规划后继续 apply。
