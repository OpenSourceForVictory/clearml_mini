# 整机可靠队列专项（2026-11-25）

Worker 0.21.19 让新安装的同节点多卡共用一个可靠结果 SQLite 文件。单次事务同时检查整机 64 MiB/10000 条、单执行 16 MiB/2500 条，以及控制记录预留；回合/任务结果按执行 ID 交付和封口。执行 journal 在建库前固定队列路径，首次建库同步目录项；重启只能打开原队列。最终平台同步确认后，删除本执行已确认摘要，其他执行待补报数据不受影响。旧 journal 和手工未配置的节点仍沿原每执行队列；未配置共享根不能领取新协商执行。普通日志窗口截断不影响协议结果队列，队列满或磁盘失败时保留源文件及游标。

最终专项：使用隔离 PostgreSQL、`RECOVERY_RUNTIME_TEST=1` 运行共享队列、四类结果采集、恢复执行/完成、结果应用、领取、安装、普通日志窗口和断联基础测试，**270 通过、0 跳过**。覆盖率按 coverage.py `--branch --source=worker` 统计本次触及模块：reliable_outbox 97.21%/95.31%，recovery_results 97.59%/96.55%，recovery_delivery 92.11%/83.33%，recovery_finish 100%/100%，recovery_poll 95.92%/93.33%，recovery_execution 97.87%/95.45%（行/分支）。原始覆盖数据位于本机 `/tmp/mini-eval-outbox-release-coverage-20261125`，不是全项目覆盖率或前端插桩结果。

初次扩大专项时 **19 失败、181 通过、3 跳过**：新增领取前共享根校验后，旧 `test_worker_recovery_poll.py` 测试夹具未声明 Worker 配置，均因 `AttributeError: SimpleNamespace has no attribute config` 失败。补齐同节点共享根夹具后，领取专项 27 通过；保留此失败记录，不将其解释为生产故障。随后补测整机并发写入、执行级字节/条数边界、缺库恢复、建库目录同步失败、最终摘要退休失败和真实 host/Docker 收尾。此证据不代表 30 分钟负载、真实模型、已部署节点或完整断联恢复能力验收。
