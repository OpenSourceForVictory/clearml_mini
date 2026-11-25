# 断联容忍专项验收（2026-11-25）

范围是新协商执行的断联容忍策略及本地监督，不代表生产开关已启用。默认 `null` 表示不按断联时长停止，正整数秒为有限容忍；业务执行总时限、平台取消和原执行身份核验仍独立生效。旧执行缺少策略时维持旧协议；有限策略不能在旧 Worker 或恢复开关关闭时静默降级。

新增的 `test_worker_monitor_restart_keeps_original_finite_deadline` 关闭并重建监控器，确认原执行没有被停止、重新分配或重置截止时间；`test_failed_local_stop_retains_original_execution_and_retries` 注入运行时停止失败，确认原身份和停止原因保留，随后重试原进程。`test_recovery_execution_real.py` 的 `finite_reconnect` 让真实本机 systemd 服务及 Docker 短程序在有限容忍期内断开控制 API 超过旧一秒租约，恢复后仍按原执行完成并补报结果；现有 `finite_disconnect` 用例验证超限本地停止、结果同步与清理。

验证命令及结果：

```text
.venv/bin/python -m pytest -q backend/tests/test_worker_recovery_monitor.py --tb=short
29 passed, 4 skipped
RECOVERY_RUNTIME_TEST=1 .venv/bin/python -m pytest -q backend/tests/test_recovery_execution_real.py -k finite_reconnect --tb=short
2 passed, 18 deselected
.venv/bin/python -m pytest -q backend/tests/test_disconnect_tolerance_submission.py backend/tests/test_recovery_control.py backend/tests/test_recovery_scheduling.py backend/tests/test_worker_recovery_monitor.py --tb=short
118 passed, 10 skipped
LOG_TEST_DATABASE_URL=<隔离 PostgreSQL DSN> .venv/bin/python -m pytest -q backend/tests/test_recovery_control.py backend/tests/test_recovery_scheduling.py --tb=short
86 passed
```

第二组跳过包含隔离 PostgreSQL 和真实后端条件项；上面的隔离 PostgreSQL 补跑覆盖数据库条件项，其余真实后端条件项的专项证据见相邻记录。这里的真实执行仅为短业务程序，未运行真实 GPU/NPU 模型。Worker 整个进程停机期间没有本地监控线程；重启后按原持久身份和截止恢复，不能承诺停机期间精确停止。完整故障矩阵、混合版本灰度、长测与全量覆盖率分别保留在 4.7、4.10 和 5.x；此验收不会打开 `disconnected_execution_v1` 能力声明。
