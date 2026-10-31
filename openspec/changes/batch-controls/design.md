# Design

## Context

动机见 proposal.md。当前 EvaluationBatch 只有并发上限；状态由各 Job 聚合。调度器、领取、单项取消和重试共用 PostgreSQL 事务锁。RESERVED 尚未获得执行权；claim 转为 STARTING 时标记 cleanup_confirmed=false。批次详情已支持安全轮询、历史尝试及带 expected_run_id 的补跑。本次必须同时覆盖准入和领取边界。

## Goals / Non-Goals

**Goals:** 控制操作在数据库中可串行解释；暂停不消耗 GPU 预留，已领取任务继续；控制和监控显示一致；保留单项操作兼容。

**Non-Goals:** 不挂起容器进程，不中断后自动恢复回合，不更改公平排序、Worker 协议或尝试统计口径。取消后的任务仍通过现有单项重试恢复，批量失败补跑仍不包含 CANCELLED。

## Decisions

1. **独立控制字段。** EvaluationBatch 增加 scheduling_paused=false、control_revision=0、control_updated_at 可空；batch_view/monitor 增加字段，现有 status 聚合保持不变。用独立开关而不增加 Job.PAUSED，以兼容租约、终态和历史接口。
2. **版本化统一入口。** POST `/api/v1/batches/{id}/control`，body `{action:"pause"|"resume"|"cancel",expected_revision:N}`。获取调度锁、检查归属与版本后一次事务完成。成功返回调度开关、版本以及受影响分类数量；接受任何操作都递增版本，包括已暂停再次暂停或全终态取消。拒绝旧版本，不用“幂等返回当前值”掩盖后续相反操作。409 后前端刷新显示，不自动更新版本再次执行。
3. **暂停撤回预留。** tick 跳过暂停批次；pause 将 RESERVED 转回 PENDING，同一 Run 不增加 attempt，清除 slot/queue/execution、预留时间/到期时间和 GPU/CPU/RAM 预留值，保持原提交顺序。claim 在 RESERVED 转 STARTING 前再次检查批次开关。暂停/claim 共用锁，先领取的 STARTING/RUNNING 不被撤回，重新连接的已领取执行不被暂停拦截。所有释放字段必须与原有预留/资源计数语义对齐。
4. **取消复用现有协议。** 提取单项取消内部函数，保留单项终态 409 契约；整批入口对终态跳过，对 CANCEL_REQUESTED 不重复产生状态事件，对 PENDING/RESERVED 直接取消，对 STARTING/RUNNING 请求取消。取消始终设置 scheduling_paused=true，防止控制过程中新准入。现有 lease/watchdog/finish/cleanup 决定实际停止。监控增加 cancel_requested_tasks、cleanup_pending_tasks；二者可能重叠，不能相加作为独立总数。
5. **重试与恢复。** 暂停不禁止创建重试尝试，但其仍排队。resume 不撤销 CANCEL_REQUESTED、不修改终态、不创建尝试。和重试的竞争按同一锁的提交顺序处理；成功取消之后创建的重试仍受暂停约束，旧取消请求因版本变化不能再次生效。
6. **可见且一致。** 新增 batch_paused 原因，不改变提交前预检。列表状态旁显示“调度已暂停”；详情同时显示仍运行、取消中及待清理数量。按钮文案“暂停后续调度”“恢复调度”“取消整批”；取消确认说明将停止正在运行的任务且保留已有结果。确认打开时捕获控制版本，轮询不替换。按钮请求互斥，成功更新控制结果并由轮询跟进，失败保留筛选/尝试和反馈。

## Risks / Trade-offs

- 暂停不阻止已领取但尚未启动的容器开始执行 → 把执行权领取作为明确边界，按钮说明和测试覆盖 STARTING。
- Worker 失联导致取消长时间未清理 → 展示清理数量及已有诊断，沿用隔离而不提前归还资源。
- 旧 API 不理解暂停字段，降级后可能恢复调度 → 降级前排空活动执行并确保不存在暂停批次中的排队任务；数据库 downgrade 对不安全状态拒绝执行。不能仅切换旧镜像绕过这一运维前提。
- 新增并发入口破坏重试或领取顺序 → PostgreSQL 独立库竞争测试，不能用串行 SQLite 结果替代。

## Migration Plan

在 f629a11 后新增单一迁移，默认值使旧批次继续正常调度；验证升级、旧数据默认值、安全降级及拒绝不安全降级。发布前备份数据库和旧镜像，在无活动执行的窗口迁移并更新 API/UI，无需重启 Worker。上线用专用 SDK 集合验证暂停持久化、继续已有执行、恢复和双机取消，保留原集合/节点配置。验收后取消测试剩余工作、确认清理、归档专用集合，并记录回退与 Git 提交。回退不自动恢复旧数据库快照，避免丢失新结果。
