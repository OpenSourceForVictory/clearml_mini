# 宿主机设备环境边界验证

触发条件：API 正常提交已拒绝模板和任务写入设备变量，但 Worker 原先没有复核任务级覆盖，且宿主机 GPU 的 `CUDA_VISIBLE_DEVICES` 在业务环境合并前写入。异常或旧任务快照可能覆盖平台分配的 GPU 选择。

修正后 Worker 对任务覆盖再次执行保留变量校验、核对模板 `overridable_env`，并在业务变量合并后写入分配的 GPU UUID。NPU 继续使用节点批准的设备访问列表和逻辑设备 0。此变更不增加 GPU 内核设备隔离，不继承登录 Shell，不修改正常任务的分配结果。

验证：项目虚拟环境下执行 `test_execution_launch_boundaries.py`、`test_host_backend.py`、`test_host_working_directory.py`、`test_npu_cann_environment.py`、`test_worker_environment_probe.py`、`test_parameter_contract.py`、`test_task_input.py`，110 passed、7 skipped；TypeScript `tsc --noEmit` 通过。首次用系统 `python` 运行前三个文件得到 39 passed、7 skipped、1 failed；失败是系统解释器缺少 `fastapi`，改用项目虚拟环境后通过。上述均为模拟启动边界，未在真实 GPU/NPU 节点运行业务；没有运行全量覆盖率。
