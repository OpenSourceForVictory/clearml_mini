# NPU 宿主机 CANN 环境加载验证

## 触发条件与修正

NPU 宿主机服务使用独立 systemd 单元启动，不读取 SSH 登录 shell。节点旧显式 `LD_LIBRARY_PATH` 没有包含 `libhccl.so` 所在的 CANN 目录时，业务 Python 导入 `torch_npu` 报 `ImportError: libhccl.so: cannot open shared object file`。

Worker 现在以批准的业务账号加载节点安装的 `set_env.sh`，只合并 PATH、LD_LIBRARY_PATH、PYTHONPATH 和已知 ASCEND 运行路径；模板业务库目录与平台设置的单卡可见性保留。批准宿主机环境探测也执行该初始化。没有可发现脚本时，保留旧的节点显式配置；找到脚本却无法执行或缺少 `LD_LIBRARY_PATH` 时关闭该环境准入。Docker 使用镜像自身环境。

## 验证边界

- 定向单元测试覆盖脚本真实 source 与路径合并、业务身份参数、保留设备选择、缺少脚本、脚本失败、无库路径、无效编码，以及宿主机探测和业务启动环境。执行 `.venv/bin/python -m pytest backend/tests/test_npu_cann_environment.py backend/tests/test_worker_environment_probe.py backend/tests/test_execution_launch_boundaries.py backend/tests/test_host_backend.py backend/tests/test_host_backend_lifecycle.py backend/tests/test_host_working_directory.py backend/tests/test_accelerator_resources.py backend/tests/test_system_node_installer.py backend/tests/test_node_installation.py -q -rs`，结果 148 passed、7 skipped。跳过项为 3 个需显式开启的真实 systemd 宿主机用例和 4 个需 systemd user manager 的业务目录用例。未运行全量测试及统一覆盖率门槛。
- 前端使用本机缓存的 Node 24 执行 TypeScript 类型检查和 Vite 构建，通过；系统默认 Node 12 对当前 TypeScript 7 不兼容，首次直接运行 TypeScript 时失败。构建有既有的单个 chunk 超过 500 kB 提示。
- 29 节点只读检查：`/usr/local/Ascend/cann/set_env.sh` 可由 `HwHiAiUser` 加载，加载后 `ctypes.CDLL('libhccl.so')` 成功。该机没有报错中使用的 `/efs-mi-east4-1/apps/miniconda3/envs/causal-wam/bin/python`，系统 Python 也未安装 `torch_npu`；因此在 29 节点尝试导入业务环境时因解释器路径不存在而退出码 127，不能据此证明 `torch_npu` 或 Causal-WAM 服务已通过。
- 首次定向测试因旧探测用例的 NPU settings mock 仅返回两个值而失败；已更新为实际三元组并重新通过。保留此失败边界，不把 mock 测试称为真实模型评测。
- 扩展调度回归首次有 6 例失败：`eligibility_codes` 在 NPU 和 GPU 共享能力判断中引用未定义的 `mode`。现从任务模板读取执行方式，新增宿主机 NPU 后端匹配用例；此缺陷与 CANN 初始化独立，但会阻止后续调度。
- 尚未在具备 `causal-wam` 环境的故障节点完成 `torch_npu` 导入和真实业务服务验收，也未部署本次 Worker 改动。部署后应以业务账号确认 CANN 初始化，再试运行服务并查看实际日志。
