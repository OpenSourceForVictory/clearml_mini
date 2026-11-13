# Design

## Context

现有 React Admin 将凭据创建和所有机器配置堆叠；Worker 与物理机器一一对应，Slot 按 GPU 建立。backend execution/memberships 已有调度锁、租约、排空与恢复；clients 保存模板快照；runner.sdk/episode_contract 提供逐回合 JSONL。Worker 目前只支持 Docker，配置和回执也以镜像为中心。控制端 Docker 内不具备用户 SSH 配置，不能直接假定可执行本机 ssh。

## Goals / Non-Goals

Goals: 通用管理、可执行两类模板、真实 SSH 纳管与服务实例闭环，兼容旧 API 和当前五节点部署。
Non-Goals: 不做 Kubernetes、多节点分布式推理、自动安装任意业务依赖或通用镜像构建；不迁移既有数据库/日志到 NFS，不把宿主机执行宣称为强隔离，不引入推理地址池。

## Decisions

1. **ClearML 参考与差异**：参考官方 [Agent 仓库](https://github.com/clearml/clearml-agent)、[执行环境](https://clear.ml/docs/latest/docs/clearml_agent/clearml_agent_execution_env/) 的环境/任务分离和 [Workers and Queues](https://clear.ml/docs/latest/docs/fundamentals/agents_and_queues/) 的拉取队列模型。沿用当前数据库调度与 Agent 拉取，保持模板/实例快照可追溯。ClearML [services-mode](https://clear.ml/docs/latest/docs/clearml_agent/clearml_agent_services_mode/) 面向轻量后台控制服务且要求 Docker，官方明确不建议推理任务进其默认 services 队列；本项目 GPU 推理仍需真实资源预留，不能直接复制 launch-and-forget。
2. **兼容模型**：在 Worker 加 roles、纳管状态/撤销时间、SSH 非敏感元信息；旧数据默认 client。服务端与客户端可以兼任且共用 Slot，角色变更不得绕过运行资源。解除纳管用墓碑和令牌撤销，不物理删除历史外键。停用不让持有执行失去续租/结果上报权限。
3. **SSH 执行器**：独立控制端纳管进程负责 SSH，API 提供有界操作队列/状态；临时凭据只存在短生命周期内存，通过受控通道交接，不落日志/数据库。使用主机密钥校验和可见指纹，不关闭校验。先检查系统/Python/GPU/Docker或host能力/API连通性/目录，再明确执行安装；失败可重试，所有步骤幂等。既有五节点只探测，不能重复覆盖 NFS 系统单元。业务依赖缺失展示修复项，不自动 apt 升级驱动。
4. **模板层**：保留 ClientTemplate 表/API以兼容集合外键；新增 execution_mode、integration_mode、host 环境引用，Docker默认兼容。新增 ServerTemplate，复用纯配置校验器；相同 name/version 唯一，修改通过复制新版本。禁止运行时拼接 shell 字符串；命令为 argv，参数/环境变量独立解析，保留平台变量不可覆盖。逻辑资产与 host 环境按节点解析，不能把某台绝对路径写死为模板唯一身份。
5. **执行后端**：Docker 后端保留现有行为；宿主机后端使用受管理 systemd 执行单元/进程组，以 execution ID 命名，绑定 Worker 用户、GPU可见性、CPU/内存及超时，日志有界、本地 journal 与终止确认可恢复。禁止仅保存 PID 后直接 kill。每次执行的输入/工作/输出独立，宿主机环境需管理员配置和就绪探测。GPU显存仍为准入估算，不宣称硬隔离。
6. **SDK业务命令**：新增 SDK 命令适配器，按回合生成参数/环境与结果文件约定，校验 success/metrics/artifacts 后生成原协议事件；直接协议模式复用现有 JSONL/输出协议。业务 stdout 不推断成功。Docker和host共用同一输入和产物格式，Agent续租/重试去重仍由既有协议负责。
7. **推理实例**：复用 Job/Run 资源预留与清理生命周期，以独立服务类型与目标 Slot 约束加入统一调度；ServerInstance记录目标、端口、模板快照、期望状态和健康报告。持续运行不使用评测回合超时，启动健康有期限。端口按主机唯一预留，失败清理前不释放；停止触发取消，重新启动产生新执行记录。实例详情提供日志和地址复制，已有评测的地址列表独占/共享机制不变。
8. **界面与账号**：拆分 Admin 子组件，列表/注册/详情独立；资源池复用 memberships 修订检查，删除采用归档且拒绝活动引用；账号复用现有密码哈希与权限，增加账号列表、状态和重置，保护最后一个管理员。

## Risks / Trade-offs

- SSH 凭据泄漏 → 专用执行器、临时内存、日志脱敏、操作过期，不把 Web API 容器挂载整个 ~/.ssh。
- host 程序权限较大 → 管理员批准环境/模板，明确可信程序边界；系统级进程管理及资源限额，缺能力则拒绝准入。
- 兼任 GPU 超卖 → 服务与评测同一调度锁和资源账本，终止未确认保留占用。
- 升级失联 → 新 Agent 能力版本门槛、分步滚动、保留已有 journal、旧模板继续运行。
- NFS 中断 → 沿用挂载守卫，不把凭据/恢复记录迁到共享盘。

## Migration Plan

备份数据库及当前五节点配置；新增兼容字段/表并验证升级。先发布 API 与界面，新能力仅对新 Agent可用；在本机验证 Docker/host 客户端及服务双模式，再滚动升级五节点，验证身份/NFS/池和历史任务保留。SSH 纳管以隔离测试主机或明确新实例验收，不解除当前五节点纳管。回退前排空新增host/服务执行，回退代码保留新增字段和历史表，不做破坏式 downgrade。每个完成的功能提交 Git；记录真实验证与未覆盖边界。
