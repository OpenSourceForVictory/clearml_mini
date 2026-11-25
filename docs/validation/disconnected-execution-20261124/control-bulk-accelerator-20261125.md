# 完成清理与批量上报解耦：专项验收

2026-11-25 在隔离 FastAPI/SQLite 与真实 systemd/Docker 短程序中，`RECOVERY_RUNTIME_TEST=1 .venv/bin/python -m pytest -q backend/tests/test_recovery_execution_real.py -k bulk_rejected --tb=short` 为 **2 passed**（15.74 秒）。日志、产物接口持续返回注入的 503 时，宿主机和 Docker 各自仍确认退出、完成、物理清理与资源释放，协议结果另行补报；不能把批量通道恢复当作清理前置条件。

`RECOVERY_RUNTIME_TEST=1 .venv/bin/python -m pytest -q backend/tests/test_recovery_runtime.py::test_real_runtime_reconciles_original_identity_after_lease_expiry --tb=short` 为 **6 passed**（10.85 秒）。新增的宿主机和 Docker 两个故障分支在平台取消后注入停止未确认：程序仍运行，Run 保持 `CANCEL_REQUESTED` 且 `cleanup_confirmed=false`；随后对原身份重试真实停止，退出对账后才完成和释放。另有 `test_worker_recovery_monitor.py::test_failed_local_stop_retains_original_execution_and_retries` 验证本地监督持续保留原执行。

加速卡控制短程序使用 `backend/tests/accelerator_control_smoke.py`。本机 RTX 4090 在 user systemd unit 中完成 CUDA 张量计算（结果 32.0），恢复运行时按原 InvocationID 停止，最终 unit inactive/not-found。29 节点空闲的 NPU0 在隔离 transient systemd unit 中，使用现有 Python/CANN 环境完成 `torch_npu` 张量计算（结果 32.0），恢复运行时同样按原 InvocationID 停止，最终 unit inactive/not-found。两次均没有重启现有 Worker 服务或占用其他在运卡。GPU 首次系统级 unit 启动被系统拒绝（需要交互鉴权），改用当前账号的 user systemd 后通过；此失败保留为环境边界，不是算子失败。

可重复入口：在已安装项目依赖且有 user systemd 的 GPU 节点，执行 `PYTHONPATH=. python -m backend.tests.accelerator_control_smoke --accelerator gpu --python /path/to/torch-python --device GPU-UUID --user-unit`；NPU 节点以允许 transient systemd 的身份执行同一模块，指定 `--accelerator npu --python /path/to/torch-npu-python --device 0 --cann-set-env /path/to/set_env.sh`。使用前先确认选定卡空闲。测试生成唯一执行 ID 和 unit，退出时停止并清理 unit。

边界：GPU/NPU 项验证了真实加速卡短程序与 `RecoveryRuntime.stop` 的身份控制路径，未通过生产平台领取、调度或完整 `Worker.execute` 启动；宿主机/Docker 的完成清理用例是短程序加隔离 API，不是模型质量评测。完整恢复能力仍未广告，混合版本及 30 分钟负载仍在后续清单。
