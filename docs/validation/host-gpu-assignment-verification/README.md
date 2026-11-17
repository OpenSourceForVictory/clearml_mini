# 宿主机 GPU 分配身份复核

触发条件：平台通过 claim 把槽位的编号和 UUID 下发给 Worker；原宿主机 GPU 路径只将 UUID 写入业务环境，未在启动前复核 Worker 本机该卡身份。异常任务快照或设备替换可能让预约卡与业务卡不一致。

修正行为：带物理 GPU UUID 的宿主机任务必须先匹配 Worker 配置的 `gpu_index`，再用本机 `nvidia-smi` 查询当前 UUID；编号或 UUID 不匹配、查询失败时拒绝创建启动标记。NPU 原有 identity 与设备限制、Docker 设备选择保持原样。GPU 环境变量仍不是硬设备隔离。

验证：`.venv/bin/python -m pytest backend/tests/test_execution_launch_boundaries.py backend/tests/test_host_backend.py backend/tests/test_host_working_directory.py backend/tests/test_accelerator_resources.py -q`，65 passed、7 skipped。测试模拟本机 GPU 查询与启动边界，未在真实多卡 GPU 节点运行业务；没有运行全量覆盖率。
