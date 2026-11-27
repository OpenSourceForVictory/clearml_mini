# 批次取消锁竞争专项验证（2026-11-28）

触发证据：用户在另一套平台上报告查看批次、取消评测时经常出现 `lock_timeout`，且有多个批次和大量任务排队；用户界面还看到 509。当前仓库把 PostgreSQL 55P03 映射为 503/`lock_timeout`，未找到返回 509 的代码。未访问该故障平台，不能把本机测试当成现场复现。

代码原因：取消入口和调度器共用事务级 advisory lock，原控制请求最多等待默认 0.5 秒数据库锁；整批取消还对每项待运行任务单独锁定 Run 并刷新 Job。高排队负载可增加调度持锁时间，当前批次任务数可增加取消持锁时间。

修正：取消入口最多在请求预算内尝试 2 秒调度锁，每次失败回滚并归还数据库连接；超限返回 503/`scheduler_lock`，不部分写入。PENDING/RESERVED 复用已经加载的最新 Run，STARTING/RUNNING 继续逐项锁定并刷新。调度算法、Worker、连接池和 API 进程数未修改。

验证：`test_batch_controls.py` 构造 100 个排队任务并禁止逐任务 Run 查询，取消返回 100 项且各项终态正确。隔离 PostgreSQL 上 `test_control_operations_postgres.py` 与 `test_execution_local_control_locks.py` 合计 16 通过，覆盖短暂持锁后 API 取消成功、持续持锁返回 503 且无部分效果、等待中连接归还、原运行任务并发取消；数据库由测试夹具创建并删除。此前批次控制与编排定向测试 31 通过、10 条 PostgreSQL 条件跳过；后续已单独启用隔离 PostgreSQL 跑锁测试。前端构建通过。没有运行全项目覆盖率，也没有在用户的另一套平台部署或验证网关 509。

部署边界：只需更新 API/前端；不需要数据库迁移或 Worker 升级。若现场仍有 `lock_timeout`，应在错误发生时按[控制操作排查](../control-operation-backpressure.md)采样 advisory lock 持有者和事务年龄，不能继续盲目增加控制副本数。
