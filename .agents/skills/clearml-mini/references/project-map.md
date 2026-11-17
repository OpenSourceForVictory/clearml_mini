# 实现与操作索引

路径相对仓库根目录。用户操作入口是界面“用户指南”；维护者使用以下源码和文档定位。

| 边界 | 源码 / 文档 | 关联检查 |
| --- | --- | --- |
| 登录与会话 | backend/app/main.py、frontend/src/login-session.ts、api.ts、main.tsx；docs/login-sessions.md | 真实交互续期、闲置/12 小时上限、并发响应、同账号草稿恢复；轮询不续期 |
| 工作台、页面帮助 | frontend/src/main.tsx、navigation.tsx、user-guide.tsx、guide-content.ts；docs/user-guidance.md | 角色、表单草稿、键盘焦点、主题映射 |
| 业务评测向导与集合 | frontend/src/run-wizard.tsx、suites.tsx；backend/app/batches.py；docs/suite-management.md | 模板版本、参数快照、服务绑定、预检 |
| 模板和参数 | frontend/src/templates.tsx；runner/template_contract.py、parameter_contract.py、task_input.py；docs/runtime-templates.md、task-input.md | 类型、覆盖权限、argv/env 冲突、目录解析 |
| SDK 与直接协议 | runner/sdk.py、command_sdk.py、episode_contract.py、cs_entry.py；examples/integration | Worker 事件解析、API 回合校验、final 和退出码 |
| 机器和节点程序 | frontend/src/administration.tsx、node-agent.tsx、ssh-onboarding.tsx；docs/node-agent.md、node-reenrollment.md | 心跳/遥测、期望与生效配置、排空、升级 |
| 资源池和调度 | backend/app/scheduling.py、job_resources.py、node_resources.py、orchestration_scheduler.py；docs/gpu-scheduling.md、resource-pool-membership.md | 服务/客户端共同预约、CPU/RAM/显存、准入与清理 |

| 服务运行和探测 | runner/service_entry.py、readiness_contract.py；frontend/src/readiness-editor.tsx；docs/service-readiness.md | 端口、网络、就绪门槛、服务代次及客户端关联 |
| 宿主机和容器目录 | docs/host-execution.md、directory-mounts.md、worker-setup.md | approved 环境、cgroup 委派、挂载能力、磁盘门槛 |
| 运行结果 | frontend/src/batch-history.tsx、batch-detail.tsx；docs/batch-monitoring.md、batch-reports.md | 重试历史、真实回合分母、取消与删除语义 |

非平台资源占用准入：scheduling.eligibility_codes 与 node_resources.host_codes 分别以设备显存、主机内存的总量及最新实测剩余量限制新申请加全部未清理平台预约。没有按任务实际用量归因时，此规则会重复计入已运行任务的实际占用，偏向安全；不要将 `total-free-reserved` 解释成精确的非平台用量。待分配任务在 PENDING 队列中，尚无具体卡位预约。预检和调度共用准入，Worker 启动前仍复查设备剩余量。见 docs/gpu-scheduling.md、docs/template-resource-requirements.md；回归 test_gpu_sharing.py、test_accelerator_resources.py、test_resource_reporting.py。

## 验证与发布

- Python 测试使用仓库 `.venv/bin/python -m pytest ...`，按现有 fixtures 配置；不要通过生产数据库运行破坏性测试。
- 前端 `cd frontend && npm run build`；Playwright 用 `EVAL_API_URL` 指向测试前端，mock 测试不创建生产任务；live/fleet 测试单独检查用途再运行。
- `python scripts/build_integration_bundle.py` 更新界面下载的 SDK 包；`backend/tests/test_integration_bundle.py` 验证源码一致性及解压后自检。
- Skill 验证可使用已安装 skill-creator 的 quick_validate.py。指南测试在 frontend/tests/user-guide.spec.ts。
- 部署配置在 deploy/，运维说明见 docs/operations.md、fleet-release.md。发布前核对实际 Compose 项目、镜像 overlay 和活动执行，不复制历史机器地址或镜像标签作为事实。
- 本地 `.runtime/` 可含私有运维材料，不进入下载包或 Git。故障先读状态和日志，限制输出范围，禁止打印完整环境配置。

## 文档维护

代码改变时找到 guide-content.ts 对应主题，同步步骤、错误与示例；新 FunctionPages 组加入 functionGuideTopics；其他页面显式挂 PageHelp/GuideButton。SDK 内容由脚本从源文件生成，不直接编辑 ZIP。更新 docs/user-guidance.md 的验证范围，不把未经运行的测试写成已通过。

宿主机 cwd：`runner/template_contract.py` 校验 `host_runtime.working_directory`；`worker/host_backend.py` 基于批准 code_root 解析 `_resolved_cwd`，客户端入口、服务入口和 command_sdk 都需消费它。保留 work/output 目录语义。新字段要求 Worker 协议 13，检查 scheduling 与 host_environments 两处准入；不修改旧模板默认 cwd。验证见 backend/tests/test_host_working_directory.py 和 docs/host-execution.md。

多设备存储设置：backend/app/storage.py 从 Worker.connection 的 slot_storage_overrides 解析每卡覆盖，整机默认保留 Worker.storage_config 原形；execution.heartbeat 按槽位下发，旧 Worker 协议 v4 仍使用 `/slots/<slot_id>`。frontend/src/storage.tsx 不能用第 0 卡实际目录预填整机表单，每卡编辑与版本冲突独立处理。操作见 docs/worker-setup.md；回归 test_storage.py、storage.spec.ts。回退旧 API 时覆盖不再参与调度，应先恢复新 API。

GPU/NPU 共享与双执行环境纳管：frontend/src/ssh-onboarding.tsx 选择 preserve/enabled/disabled，经 onboarding/contracts.py、executor.py 写入安装凭据；scripts/install_system_node.py 新安装默认关闭，scripts/install_node.py 重新纳管默认保留、显式选择时仅更新本地 gpu_runtime.allow_shared 或 npu_runtime.allow_shared 并沿原回滚恢复。onboarding/probe.py 只展示逐卡布尔值，不暴露完整配置。Worker 在注册时由 accelerators.capabilities 转为 gpu:*:shared 或 npu:*:shared 能力，普通心跳不传原字段。操作与手工定位见 docs/root-managed-host.md，回归 test_system_node_installer.py、test_node_installation.py、test_ssh_onboarding.py、ssh-onboarding.spec.ts。

心跳连接池争用：backend/app/execution.py 空闲心跳、node_agents.py 采样和 services.py 健康探测更新最新数据库状态，不排队等待 scheduling.py 全局锁；同节点/服务通过数据库行锁保护序号与配置。携带执行租约的心跳用 fast_lock 快速失败 503，保留状态转换互斥；探测会话启动仍用全局锁。见 docs/operations.md 和 docs/validation/heartbeat-db-contention/README.md；回归 test_heartbeat_contention.py、test_service_readiness.py、test_node_agents.py。

独立解释器与业务目录的可载入样例：frontend/public/template-examples/catalog.json 中三个 `-cwd` 样例；机器环境 `template_demo_cwd` 指定 Python 与 code_root，模板 working_directory 指向 examples/runtime_templates。准备说明通过注册模板页面下载 README.md（源 examples/runtime_templates/README.md）。

批次详情布局由 frontend/src/batch-detail.tsx 与 batch-detail.css 管理；参数与服务页按首次访问加载并保留组件状态。修改页签需回归 batch-monitoring、batch-controls、batch-reports、config-pages 浏览器用例，确保筛选、补跑尝试快照和控制版本不变。

服务端调度概览：backend/app/orchestrations.py 的编排详情对尚无实例的服务定义复用 DiagnosticContext 输出服务端池、候选设备与阻塞原因，不写入调度状态；frontend/src/orchestration-status.tsx 在默认任务页显示调度、启动和健康阶段，完整日志与绑定保留在服务页。操作见 docs/batch-monitoring.md；回归 test_orchestrations.py 和 orchestration-status.spec.ts。

SSH 纳管部署排查：docs/ssh-onboarding-executor.md；默认 Compose 自动启动 onboarding 服务（onboarding/Dockerfile），专用 socket 卷连接 API，状态卷仅执行器持有；onboarding.healthcheck 检查本地协议，无 API 依赖环。旧显式 ONBOARDING_SOCKET 及父目录 bind mount 保留兼容宿主机 mini-eval-onboarding.service。发布检查 scripts/fleet_rollout.py 保护环境和挂载，不能把执行器缺失误判成目标 SSH 密码错误。

统一 root 纳管/普通业务身份：docs/root-managed-host.md；scripts/install_system_node.py 创建系统服务与 mini-eval，worker/host_identity.py 负责身份/权限/资源检查。host_execution 是节点批准配置，模板不能覆盖；旧用户服务升级保留身份。覆盖率门槛见 backend/tests/managed-host.coveragerc，真实测试需 MANAGED_HOST_TEST=1 和隔离 systemd 环境。

通用命令（Worker 0.17.0 / 协议 14）：docs/generic-commands.md、host-execution.md。host_runtime/runtime.command_mode=exec，缺省 legacy；runner/process_command.py 在最终参数/环境绑定后按业务身份解析程序，cs_entry/service_entry/command_sdk 共用。宿主机 exec 缺省 cwd 为 code_root，平台入口使用 Worker Python；Docker working_directory 独立于 work/output，平台 interpreter 可为绝对 Python 路径。调度检查 image_runtime、host_environments 与 scheduling，旧节点拒绝新字段。测试 backend/tests/test_generic_commands.py，Docker 显式启用，验证边界不是模型评测。界面指南与模板示例必须同步。

项目覆盖率：docs/code-coverage.md；backend/tests/project.coveragerc 固定 Python 六目录分母，coverage-floors.json 固定目录 80/70 与关键功能 90/80 门槛。scripts/test_coverage.py 全量测试并保留失败报告；scripts/coverage_report.py 按计数加权聚合目录/重叠功能。CI 测试或门槛不通过均失败；不允许删除低覆盖文件或降低门槛规避。前端未插桩和未合并的子进程单独注明，不称为全项目 100%。工作区有未完成特性时隔离验证，保留并披露原工作区失败。

纳管诊断回归：`backend/tests/test_onboarding_probes.py` 区分控制面响应超限、无效 JSON、网络失败与 HTTP 拒绝；`Rejected` 必须在 `ValueError` 前保留。Worker 执行恢复、宿主机环境异步探测与构建/部署 CLI 的故障测试见 `docs/code-coverage.md`，这些模拟边界验收不替代真机验收。

覆盖率补测的范围、失败证据和真接口界面验收见 `docs/validation/project-coverage/README.md`；新增 `templates-live.spec.ts` 通过显式隔离 API 开关验证四类模板注册，`suites-live.spec.ts` 按当前“评测运行与结果 → 评测任务集合管理”导航。

执行与恢复补测：test_worker_service_execution.py（健康探测、租约 watchdog、完成上报）、test_onboarding_recovery_edges.py（恢复身份、取消、凭据过期）、test_worker_bootstrap_transport.py（启动清理、传输）、test_execution_launch_boundaries.py（身份、端口、产物续租）、test_fleet_rollout_cli.py（发布检查点重入与排空前拒绝）。这些边界模拟测试不替代 systemd / GPU / NPU 真机验收；各目录当前门禁状态见最新验证记录。

运维验收驱动器补测见 docs/code-coverage.md 与 docs/validation/project-coverage/README.md：双机脚本必须在启动监听前拒绝非隔离 API；smoke_worker_upgrades 使用当前版本元信息构造临时旧版本，不能继续写死 0.10.0。test_migration_driver_scripts.py 需 MIGRATION_DRIVER_TEST_URL 指向一次性 PostgreSQL 服务器，并为每个用例创建/删除专用数据库。虚拟时间 soak 和模拟 SSH/Docker 结果不能写成真实稳定性或硬件验收。

集合可用客户端模板：在集合管理中勾选；PUT /api/v1/suites/{id}/client-templates 独立更新（owner/revision 校验，至少一个）。suite_client_templates 保存多选，template_id 保留兼容默认；迁移 v285e67 回填旧集合。向导、批次与编排校验所选模板属于集合；更新不修改历史快照。操作见 docs/suite-management.md，回归见 backend/tests/test_suite_template_relations.py。不提供额外关系管理入口。

HTTP IP 访问兼容：frontend/src/request-id.ts 统一请求 UUID，randomUUID 缺失时用 getRandomValues；SSH 注册和运行向导共用，不能直接调用 crypto.randomUUID。浏览器回归见 ssh-onboarding.spec.ts 与 run-wizard.spec.ts。

资源池设备类型显示：administration.tsx 的物理池选项包含 GPU / NPU，接口仍为 gpu_model=*；memberships.target_error 区分物理/模拟，scheduling.eligibility_codes 按任务 accelerator_type 匹配设备。不要把通用物理池说明成 GPU 专用或已具备 NPU-only 成员约束。操作见 docs/resource-pool-membership.md。

 
cgroup 兼容（Worker 0.17.1）：root host_identity.verify_system 实测限制，resource_limits.verify_current 区分 v1/v2/混合，v1 读取 mountinfo 定位合并控制器；非 root inspect_host 保留 v2 委派要求。Docker-only 安装跳过 host 探针，onboarding/docker_prepare.py 仅生成手动镜像准备命令；纳管不拉取、构建或试运行容器，缺镜像不阻断，业务镜像准入仍由 image_runtime 控制。默认 runner 构建材料通过 enrollment.bundle 分发；发布必须同步 API 安装包、独立执行器与 UI，不能仅重启 API。旧自定义目录保护仍保留，验收见 docs/validation/cgroup-runtime/README.md。

旧 root 自定义布局重新纳管：scripts/node_layout.py 从独立 updater 配置验证 root 路径权限、全部配置、有效 systemd 服务及 Python，脱敏布局摘要经 probe → executor → install_node 二次校验。保留旧 Worker 单元和业务账号；辅助组件失败回滚保留文件权限，节点仍停用。新增模块必须进入 enrollment.bundle。操作见 docs/node-reenrollment.md；测试 test_node_layout.py、test_node_installation.py、test_onboarding_recovery_edges.py。

机器删除与 SSH 残留清理：administration.removable、ssh_onboarding、onboarding/cleanup.py；docs/node-reenrollment.md、ssh-onboarding-executor.md。Worker/Slot.deleted_at 保留历史，迁移 w396f78 的部分唯一索引释放存活身份；新增资源查询必须过滤软删除，历史查询保留原 ID。清理仅标准 root 安装，计划二次比对、平台许可、私有备份 manifest 与执行器结果核对；不可依据客户端提交的成功状态解除锁定。检查/确认传递当前登录令牌，不持久化凭据。测试 test_node_removal.py、test_node_cleanup.py、node-removal.spec.ts；模拟 SSH 不代表真机清理验收。

计算资源角色视图：main.workers 的可选 role=client/server 与 main.tsx Resources 页签；普通用户保留 total/online 汇总权限，管理员明细含 roles。双角色全量去重，无 Worker 的旧设备默认 client。操作见 docs/gpu-scheduling.md；回归 test_resource_roles.py、resource-roles.spec.ts。资源池列表不按角色过滤，页面说明其为全平台可用池。

托管服务联合容量诊断：orchestration_scheduler.Planner.structural_pair_diagnosis 保持 True/False/None 三态，compile_submission 在所有候选服务均为结构性 False 时返回任务、服务、客户端/服务端池名与配置限制；不将瞬时占用或缺少候选角色误判成永久冲突。前端向导直接显示 API detail，界面指南 wizard 与 docs/gpu-scheduling.md 说明排查顺序；回归 test_pair_capacity_diagnostics.py 和 test_orchestrations.py。

GPU 遥测新鲜度：main.workers 管理员明细调用 scheduling.telemetry_fresh，与任务准入统一检查样本位于过去 15 秒内且显存/利用率齐全；未来时间戳不得显示新鲜。主机采样是独立指标。操作见 docs/gpu-scheduling.md；回归 test_resource_roles.py。

回连恢复：onboarding/executor.py verify/retry_verification 默认 300 秒、环境配置 30–1800；核对安装 revision/release 后才完成。Worker probe_dependencies.start 后台准备，心跳刷新 software，enrollment/worker_upgrades 仅豁免可选 service_readiness_v1 版本验收，业务准入保持能力门槛。文档 node-reenrollment.md、ssh-onboarding-executor.md、service-readiness.md；测试 test_reconnect_resilience.py 与 ssh-onboarding.spec.ts。HTTP 响应 5 秒超时与回连窗口分开诊断，不自动延长遥测有效期。
