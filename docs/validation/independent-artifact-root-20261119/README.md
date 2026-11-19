# 节点独立产物根验证（2026-11-19）

本轮实现节点可选 artifact_root，以及批次/任务/执行/回合输出目录、模板产物目录映射、共享目录登记。没有部署生产 API、更新已纳管节点或在生产界面创建测试批次。用户要求后续保留界面可查看的功能验证批次，该验收仍待执行；不能把当前测试当作 GPU/NPU 模型评测。

## 验证范围

- `test_artifact_layout.py`：旧配置、独立批次/standalone/重试、配置冻结与领取重放、旧 Worker 能力拒绝、普通账号创建分支、权限失败、路径穿越/链接/重叠、umask、宿主和容器挂载、工作目录清理不删除产物。
- 实际本机业务子进程：普通命令、直接协议、SDK 命令及原有整任务配置；检查命令映射、占位符、环境变量、输入 JSON 的目录一致，逐回合目录隔离、seed 和原有回合参数语义。
- 实际 Docker：以 UID 65534 执行当前 Runner/SDK，两个回合生成 JSON；删除容器后文件仍可读。测试只生成样例文件，不运行模型；普通业务账号身份切换的部分分支是 mock，真实 systemd 用例仍受显式开关控制。
- `test_runner_entrypoint_contracts.py`：宿主/容器自定义输出别名与最终启动环境、输入和动态参数一致；非法 JSON、目录冒充结果文件、越界产物引用保持退出码0并独立报告协议错误。
- 一次性 PostgreSQL 16.8 用于共享目录迁移、并发、删除等关联回归，不访问生产数据库。当前独立根使用现有 SharedArtifactRoot JSON 快照，无新增迁移；前置版本仍为 a730d12。
- Playwright：存储配置、模板映射及模板基础流程 27 项通过；TypeScript 和 Vite 构建通过，保留超过500kB的构建提示。

## 保留失败与修正

- 实际 Docker 初次验证27项中1项失败：镜像工作目录中的旧 SDK 遮蔽了只读挂载的当前 Runner。明确设置工作目录 /opt/app 后通过；这不表示所有旧业务镜像已升级，镜像内自带旧 SDK 的部署仍需更新。
- UI 首次27项中1项失败：清空目录测试没有等保存响应，草稿被前一次响应刷新。增加等待版本保存确认后通过，未删除清空配置断言。
- 补测时 Ruff 发现既有测试文件未使用的 os 导入，移除后通过。
- 覆盖率使用完整项目分母，定向执行未达到全仓目录/功能门槛；详见 coverage-summary.md 和 results.json。没有下调门槛或排除业务文件。前端和未插桩的容器/业务子进程不计入 Python 覆盖率。

## 复现

设置 LOG_TEST_DATABASE_URL 指向一次性 PostgreSQL，MINI_EVAL_TEST_ARTIFACT_DOCKER=1，PYTEST_ADDOPTS 为 `--import-mode=importlib -k "artifact_layout or worker_storage or test_storage or sdk_output_directories or host_backend or execution_launch_boundaries or shared_artifact or worker_docker_contracts or cs_runner or image_runtime or command_sdk or task_input or host_episode or mounts or runner_entrypoint_contracts or typed_template_flow"`，运行 `.venv/bin/python -m scripts.test_coverage --output 自选目录`。本轮是关联回归，不是全仓测试。

## 升级与边界

API 和 Worker 0.21.10 配合 artifact_roots_v1；原生业务 SDK/旧式镜像需同步当前 Runner。管理设置配置产物根后等待各卡确认；API 共享存储只读映射必须覆盖此根。目录不可读不自动退回文件上传。未配置独立根保持原路径；运行中任务不迁移目录。删除工作区或批次不会清理独立产物文件，本轮没有产物配额或自动保留清理。操作步骤见 [共享产物指南](../../shared-storage-artifacts.md)。

## 最终统计

关联 Python 294通过、11跳过、1969未选择；浏览器27通过。新增 artifact_layout 行/分支100%/100%，shared_artifacts 93.81%/84.85%，storage 95.83%/86%，sdk 95.04%/91.67%。command_sdk 86.81%/76.67%、cs_entry 70.33%/52.94%未达到关键功能90%/80%目标；不能宣称全部修改模块或功能组覆盖率达标。跳过原因逐项保留在 results.json。
