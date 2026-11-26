# 执行控制与调度锁范围

## 锁保护什么

调度全局锁不是表锁，而是 PostgreSQL `pg_advisory_xact_lock(82510401)` / `pg_try_advisory_xact_lock(82510401)`。固定编号是应用约定的全平台互斥标记，事务提交或回滚后释放；只有申请同一编号的路径互斥，普通 SELECT 不会被它直接阻挡。

API 四角色隔离请求与数据库连接，不会隔离数据库锁。两个进程修改同一执行仍须互斥；只有连接数或排队时间增加，不能消除事务锁竞争。

| 路径 | 互斥范围 | 原因与本次处理 |
| --- | --- | --- |
| 调度 tick、资源预约/实际领取、资源释放 | 调度全局 advisory 锁 | 跨执行检查设备、配额、服务地址和资源占用，暂不取消 |
| 执行心跳，包括 STARTING → RUNNING | Run → Slot 行锁 | 原执行已占有资源，启动不增加分配；移除启动前全局锁 |
| 原领取的启动许可 prepare | Run → recovery grant 行锁 | 只确认原执行身份与许可，不重新分配；移除全局锁 |
| 客户端恢复 reconcile、未过期服务恢复 | Run → recovery grant 行锁 | 原身份续租、启动、退出观察；不释放资源、不重派；移除全局锁 |
| 过期服务恢复 | 调度全局锁 → Run → grant | 会清除服务就绪与探测 epoch，必须与地址分配协调；保留 |
| 用户取消 | 调度全局锁 → 最新 attempt 的 Run 行锁，再刷新 Job | 与无全局锁的启动串行，取消优先；不凭旧 Job 快照覆盖状态 |
| 调度器超时检查 | 全局锁下 Run SKIP LOCKED，并刷新重查 | 忙执行留待下轮；不能用取锁前过期快照覆盖续租 |
| 日志追加 | Run KEY SHARE → 按 Run 的日志 advisory/游标锁 | 同执行日志保证顺序，不同执行互不共享日志锁；不拿调度全局锁 |
| 恢复完成/cleanup、旧 complete/cleanup、未启动释放 | 原全局资源锁顺序 | 会影响终态、清理或资源占用，保留现有事务语义 |
| 服务 probe-session、池成员管理、批次删除 | 原全局锁 | 涉及就绪或多实体生命周期，不能直接取消 |

控制路径使用 Run `FOR NO KEY UPDATE`，与日志的 `KEY SHARE` 相容。普通 `FOR UPDATE` 会额外阻挡日志引用 Run 的外键检查，形成不必要耦合。对同一 Run 的两个状态写仍互斥；同卡 Slot 最新观测仍串行，未引入无锁覆盖。

## 顺序与重试

需要全局锁的操作先取全局锁，再取 Run，再取 grant。恢复入口先判断是否为过期服务；若取 Run 后才跨越过期边界，回滚并重试，禁止反向取全局锁。prepare/reconcile 对正在修改的执行快速返回 503，Worker 按已有重试协议重试；不会因此重启业务程序。

503 的 `reason` 区分 `scheduler_lock`（全局锁忙）、`execution_lock`（执行/授权行忙）、`execution_recheck`（服务租约跨界需重查）。`lock_timeout`、请求准入与连接池等待仍保留各自原因。压测不再把缺少 reason 的全部 503 归因于调度锁。

## 不变的约束与验证边界

原执行 ID、代次、运行身份、幂等序号和平台取消/超时优先不变；退出观察不是资源释放，cleanup 确认前保持占用。不增加数据库字段，不需要迁移或升级 Worker。API 角色应使用同版镜像。

本次没有移除整个调度 tick 的全局锁，也不宣称消除全部竞争。将 tick 改成分资源锁需要另行证明资源快照、预约和并发分配的安全性。

专项测试：`test_execution_local_control_locks.py`、`test_execution_lock_scope.py`、`test_recovery_control.py`、`test_recovery_launch.py`，以及取消、恢复收尾、日志 PostgreSQL 回归。真实 PostgreSQL 测试需隔离的 `LOG_TEST_DATABASE_URL`；不要指向生产库。混合负载与失败记录见 [等待与锁对照验证](validation/wait-tuning-20261126/README.md)。模拟 Worker 压测不代表真实模型验收。
