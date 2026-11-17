# 可选 root 宿主机业务身份验证

## 触发条件与行为

默认 mini-eval 业务账号可能无法访问节点上的私有代码或 Python/NPU 依赖。只有节点本地 `host_execution` 同时批准 root 用户、UID/GID 0 和 `allow_root_business=true` 时，Worker 才允许宿主机任务以 root 运行；NPU 还要求 `npu_runtime.host.user=root`。缺少标志、身份不一致或普通账号误设标志均拒绝。默认新纳管与旧配置仍使用普通账号，模板不能选择 root。

systemd 任务继续设置 CPU/内存/进程限制、NoNewPrivileges 和 NPU DevicePolicy 白名单；root 分支仅保留 CAP_DAC_OVERRIDE 与 CAP_DAC_READ_SEARCH 两项能力。root 业务仍可读取本机敏感文件，不能用于不可信多租户代码。NFS root_squash 可能把 root 映射为匿名账号，无法通过本地身份修改绕过。

## 验证边界

定向单元测试验证显式批准、缺省拒绝、身份漂移、宿主机环境探测中的实际 UID 参数、systemd 单元属性与 NPU 设备策略。执行 `.venv/bin/python -m pytest backend/tests/test_managed_host.py backend/tests/test_host_backend.py backend/tests/test_host_backend_lifecycle.py backend/tests/test_worker_environment_probe.py backend/tests/test_execution_launch_boundaries.py backend/tests/test_npu_cann_environment.py backend/tests/test_accelerator_resources.py backend/tests/test_system_node_installer.py backend/tests/test_node_installation.py -q -rs`，结果 147 passed、8 skipped、1 个已有依赖弃用警告。跳过项为 5 个需 root、systemd 和专用账号的真实管理用例，以及 3 个需显式开启的真实宿主机 systemd 用例。

使用本机缓存 Node 24 执行 TypeScript 类型检查与 Vite 构建，均通过；构建提示现有单个 chunk 超过 500 kB。真实 root systemd 运行、NPU 业务程序和 NFS root_squash 节点尚未验收。未运行全量测试和统一覆盖率门槛。
