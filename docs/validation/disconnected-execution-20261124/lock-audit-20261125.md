# 执行与恢复锁顺序审计（2026-11-25）

第 2.4 项核对了实际调用路径，而非仅检查锁函数是否存在。资源预约、STARTING 状态转换、取消、超时转终态、恢复启动许可、完成和清理均先取调度事务锁，再取 Run 行锁；恢复路径随后取原授权行。普通运行续租只取 Run→Slot，不等待无关执行的调度事务锁。恢复控制收据只取本执行授权行；业务结果应用和 `results-finished` 取 Run→授权行，不改资源占用。日志、回合和旧结果的局部锁不得反向进入调度锁。锁顺序只保证同一事务内资源转换，不替代进程停止证据。

超时扫描先读候选，在取得 `FOR UPDATE SKIP LOCKED` 的 Run 行锁后刷新租约和恢复授权、再次判定；忙行留待下一次扫描。未领取预约由调度锁串行，恢复领取的 request_id 与 execution_id 唯一约束防止重复绑定；已经领取的恢复授权不会因为旧租约过期被调度器回收或重派。取消先提交则后续完成保留取消，完成先提交则后续取消不得重开终态；物理清理确认前卡位仍被占用。

隔离 PostgreSQL 测试：`test_execution_lock_scope.py`、`test_recovery_control.py`、`test_recovery_finish.py`、`test_recovery_unstarted.py`、`test_control_operations_postgres.py` 合计 145 通过、0 跳过（44.85 秒），包含无关调度锁占用、STARTING 快速拒绝、旧租约快照刷新、取消持锁后重试、终态与清理顺序。`test_scheduling.py`、`test_recovery_claims.py`、`test_orchestrations.py` 合计 63 通过、0 跳过（126.94 秒），包含 16 个并发同一领取请求只返回一份原授权，以及同卡服务预约计数。测试夹具逐例创建、删除独立数据库，未接触生产库。没有新增产品行为；断联中删除批次的业务语义仍属于未完成第 4.9 项，不由本锁审计证明。
