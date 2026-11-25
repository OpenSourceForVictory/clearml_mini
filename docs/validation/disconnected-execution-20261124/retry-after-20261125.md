# Worker Retry-After 专项，2026-11-25

触发条件：0.21.17 的空闲领取循环用 `Backoff(max_total=3)` 把服务端较长的 `Retry-After` 一同截为 3 秒。若 API 要求 60 秒后重试，Worker 会在约 3 秒后再次领取；恢复控制对账的 5 秒分段等待也有相同边界。

修正：0.21.18 为心跳、领取及恢复控制对账分别记录对应接口的最早重试时刻，服务端提示后增加随机抖动。每段等待仍短，领取受限期间继续心跳，恢复控制受限期间本地监督继续观察原业务进程；执行中的旧协议租约、取消和业务时限不因等待而延长。永久性 HTTP 拒绝不进入过载退避。

验证：运行 `.venv/bin/python -m pytest -q backend/tests/test_worker_backpressure.py backend/tests/test_worker_execution_recovery.py backend/tests/test_worker_overload_execution.py backend/tests/test_worker_recovery_monitor.py backend/tests/test_worker_recovery_execution.py`，177 passed、4 skipped、0 failed，39.91 秒。新增用例覆盖 60 秒提示在 3 秒分段后仍禁止提前重试、较短后续提示不缩短原时刻、永久错误不设置窗口、空闲领取/心跳互不阻塞，以及恢复控制延迟时本地观察仍确认退出。既有用例覆盖同秒随机抖动、断网、永久错误、结果/日志故障不阻塞清理、取消和服务启动截止。4 个跳过为需显式开启的真实 host/Docker 监控用例，本次未开启；本项是代码和定向测试验收，不是生产升级或真实 NPU/GPU 断联验收。完整全量/覆盖率与生产启用分别留在 OpenSpec 5.4、5.3。
