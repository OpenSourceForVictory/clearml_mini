# 同节点同名服务与客户端运行实体隔离

命令：`RECOVERY_RUNTIME_TEST=1 .venv/bin/python -m pytest -q backend/tests/test_recovery_runtime.py::test_real_same_name_services_and_clients_keep_distinct_runtime_identity --tb=short`。2026-11-25 本机结果：真实 systemd 和 Docker 各 1 项，**2 passed**，约 44 秒。

每种模式在同一节点启动两个显示名相同但服务实例 ID 不同的服务短程序，以及两个客户端短程序。四个执行各有不同执行 ID 和 systemd InvocationID / Docker 完整容器 ID；停止第二个服务后，另一个服务及两个客户端仍在运行。测试结束逐一停止并删除运行实体，未触碰现有 Worker 服务。

首次运行时宿主机断言失败：测试夹具未保存恢复授权的运行身份，导致停止后不能从已卸载的 unit 读取退出证明；补齐与真实 Worker 一致的绑定后，两模式均通过。这是测试夹具缺口，不将其作为生产故障。测试只覆盖真实短程序和运行实体隔离，`device_index` 为模拟卡位；未验证真实 GPU/NPU 分配、模型评测、平台批次页面、全接入方式及磁盘满。4.10 和完整恢复发布仍待其余矩阵通过。
