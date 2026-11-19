# 实现与操作索引

宿主机登录环境与逐回合启动（Worker 协议 15）：`runner/template_contract.py` 验证 `host_runtime.environment_source` 与 `episode_launch`；`worker/host_identity.py` 以批准业务账号读取非交互登录 shell 的导出变量；`worker/host_backend.py` 合并业务环境并最后写设备可见变量，旧模板保留原入口环境。`runner/host_episode.py` 在同一受控执行单元内顺序监视前台子进程，`runner/cs_entry.py` 与 `command_sdk.py` 处理 direct/普通命令/SDK 命令；API `clients.py`、`execution.py`、`batches.py` 维护回合与结果契约。Docker 与服务端不启用新逐回合适配器。界面和操作见 `frontend/src/templates.tsx`、`guide-content.ts`、`docs/host-execution.md`、`docs/runtime-templates.md`；定向回归 `backend/tests/test_host_shell_episode.py`。普通命令回合完成不等于评测成功率，真实节点验证与模拟测试分别记录。

路径相对仓库根目录。用户操作入口是界面“用户指南”；维护者使用以下源码和文档定位。

| 边界 | 源码 / 文档 | 关联检查 |
| --- | --- | --- |
| 登录与会话 | backend/app/main.py、frontend/src/login-session.ts、api.ts、main.tsx；docs/login-sessions.md | 真实交互续期、闲置/12 小时上限、并发响应、同账号草稿恢复；轮询不续期 |
| 工作台、页面帮助 | frontend/src/main.tsx、navigation.tsx、user-guide.tsx、guide-content.ts；docs/user-guidance.md | 角色、表单草稿、键盘焦点、主题映射 |
| 业务评测向导与集合 | frontend/src/run-wizard.tsx、suites.tsx；backend/app/batches.py；docs/suite-management.md | 模板版本、参数快照、服务绑定、预检 |
| 模板和参数 | frontend/src/templates.tsx、command-mappings.tsx；runner/template_contract.py、parameter_contract.py、task_input.py；docs/runtime-templates.md、task-input.md | 类型、覆盖权限、argv/env 冲突、目录解析；业务字段已绑定时仍可在映射选择框定位现有配置，见 template-guidance.spec.ts |
| SDK 与直接协议 | runner/sdk.py、command_sdk.py、episode_contract.py、cs_entry.py；examples/integration | Worker 事件解析、API 回合校验、final 和退出码 |
| 机器和节点程序 | frontend/src/administration.tsx、node-agent.tsx、ssh-onboarding.tsx；docs/node-agent.md、node-reenrollment.md | 心跳/遥测、期望与生效配置、排空、升级 |
| 资源池和调度 | backend/app/scheduling.py、job_resources.py、node_resources.py、orchestration_scheduler.py；docs/gpu-scheduling.md、resource-pool-membership.md | 服务/客户端共同预约、CPU/RAM/显存、准入与清理 |

| 服务运行和探测 | runner/service_entry.py、readiness_contract.py；frontend/src/readiness-editor.tsx；docs/service-readiness.md | 端口、网络、就绪门槛、服务代次及客户端关联 |
| 宿主机和容器目录 | docs/host-execution.md、directory-mounts.md、worker-setup.md | approved 环境、cgroup 委派、挂载能力、磁盘门槛 |

宿主机执行的控制端短时忙碌：`worker/client.py` 在执行心跳 HTTP 503 时保留正在运行的单元并重试，租约 watchdog 仍生效；其他 HTTP 拒绝不按瞬时故障吞掉。定位时对照 `backend/tests/test_worker_execution_recovery.py` 与 `docs/host-execution.md`。
| 运行结果 | frontend/src/batch-history.tsx、batch-detail.tsx；backend/app/batch_deletion.py；docs/batch-monitoring.md、batch-reports.md、interface-pages.md | 重试历史、真实回合分母、取消与删除语义；删除批次会清理其任务/运行数据并保留删除墓碑与审计 |

CPU/内存预算：资源池 cpu_per_job、ram_gb_per_job 是缺省申请而非上限；0 表示未申请的任务不预约该轴，Worker 不设置相应运行额度。模板或提交的明确申请按节点配置容量、Agent host 样本和现有预约准入。Agent host 心跳有 cpu_count、cpu_utilization、memory_total_mb、memory_available_mb；没有“剩余 CPU 核数”。验证见 test_unlimited_cpu_memory.py、test_pair_capacity_diagnostics.py、test_resource_reporting.py，说明见 docs/template-resource-requirements.md 和 docs/node-resource-reporting.md。

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

GPU/NPU 共享与双执行环境纳管：frontend/src/ssh-onboarding.tsx 新纳管选择 host/docker/both、共享 enabled/disabled；重新纳管执行环境和共享均默认 preserve，显式选择才改写。onboarding/contracts.py、executor.py 传递选择，probe.py 根据保留或新选择检查运行依赖；scripts/install_system_node.py 新装写入模式与共享，scripts/install_node.py 重新纳管更新每卡 execution_modes 或 gpu_runtime.allow_shared/npu_runtime.allow_shared 并在失败时回滚。Worker 注册时上报 execution_modes，并由 accelerators.capabilities 转为 gpu:*:shared 或 npu:*:shared；普通心跳不传这些配置。操作与手工定位见 docs/node-reenrollment.md、docs/root-managed-host.md，回归 test_system_node_installer.py、test_node_installation.py、test_ssh_onboarding.py、ssh-onboarding.spec.ts。

新机共享选择入口：SSH 表单的加速卡区域排在认证字段之前；手动接入由 administration.tsx 收集设备类型和共享选择，node-enrollment.tsx 将其写入下载凭据，scripts/install_system_node.py 读取对应字段。重新生成凭据前后的选择不可混用。回归 ssh-onboarding.spec.ts、memberships.spec.ts；界面步骤见 docs/root-managed-host.md。

Agent JSON 并发维护：所有修改 Worker.connection 的管理路径（Agent、单卡目录、纳管、SSH 清理、updater 回执）须先获取 Worker 行锁并刷新，再读取/合并；已有全局锁的路径保持全局锁→行锁顺序，心跳不取全局锁。不能用旧 ORM JSON 覆盖新样本；回归 integration/test_node_agent_race.py 在一次性 PostgreSQL 控制事务交错，SQLite 测试不能证明行锁有效。

心跳连接池争用：backend/app/execution.py 空闲心跳、node_agents.py 采样和 services.py 健康探测更新最新数据库状态，不排队等待 scheduling.py 全局锁；同节点/服务通过数据库行锁保护序号与配置。携带执行租约的心跳用 fast_lock 快速失败 503，保留状态转换互斥；探测会话启动仍用全局锁。见 docs/operations.md 和 docs/validation/heartbeat-db-contention/README.md；回归 test_heartbeat_contention.py、test_service_readiness.py、test_node_agents.py。

独立解释器与业务目录的可载入样例：frontend/public/template-examples/catalog.json 中三个 `-cwd` 样例；机器环境 `template_demo_cwd` 指定 Python 与 code_root，模板 working_directory 指向 examples/runtime_templates。准备说明通过注册模板页面下载 README.md（源 examples/runtime_templates/README.md）。

批次详情布局由 frontend/src/batch-detail.tsx 与 batch-detail.css 管理；参数与服务页按首次访问加载并保留组件状态。修改页签需回归 batch-monitoring、batch-controls、batch-reports、config-pages 浏览器用例，确保筛选、补跑尝试快照和控制版本不变。

服务端调度概览：backend/app/orchestrations.py 的编排详情对尚无实例的服务定义复用 DiagnosticContext 输出服务端池、候选设备与阻塞原因，不写入调度状态；frontend/src/orchestration-status.tsx 在默认任务页显示调度、启动和健康阶段，完整日志与绑定保留在服务页。操作见 docs/batch-monitoring.md；回归 test_orchestrations.py 和 orchestration-status.spec.ts。

SSH 纳管部署排查：docs/ssh-onboarding-executor.md；默认 Compose 自动启动 onboarding 服务（onboarding/Dockerfile），专用 socket 卷连接 API，状态卷仅执行器持有；onboarding.healthcheck 检查本地协议，无 API 依赖环。旧显式 ONBOARDING_SOCKET 及父目录 bind mount 保留兼容宿主机 mini-eval-onboarding.service。发布检查 scripts/fleet_rollout.py 保护环境和挂载，不能把执行器缺失误判成目标 SSH 密码错误。

统一 root 纳管/普通业务身份：docs/root-managed-host.md；scripts/install_system_node.py 创建 root 系统服务，并将新 root SSH 纳管账号写入 host_execution；SSH 表单的工作目录根路径经 onboarding/probe.py 预览，安装时按机器名和设备名创建独立工作目录，资产和缓存使用机器目录下的 assets、cache，同机各卡共享；重新纳管保留原路径。worker/host_identity.py 负责身份/权限/资源检查。host_execution 是节点批准配置，模板不能覆盖；旧用户服务升级保留身份。覆盖率门槛见 backend/tests/managed-host.coveragerc，真实测试需 MANAGED_HOST_TEST=1 和隔离 systemd 环境。

宿主机业务身份：host_identity.settings 校验节点批准的 SSH 账号；root 账号需 uid/gid=0，NPU host 必须使用同一身份。host_identity.properties 对 root 保留 CAP_DAC_OVERRIDE、CAP_DAC_READ_SEARCH，普通账号仍清空 capabilities；NPU DevicePolicy=closed 与任务额度继续应用。新纳管默认使用 SSH 账号，已有安装和重新纳管保留本地身份；Docker 不受影响。操作与敏感文件/NFS root_squash 边界见 docs/root-managed-host.md，回归 test_system_node_installer.py、test_managed_host.py、test_host_backend_lifecycle.py。

NPU 宿主机 CANN 环境：worker/npu_host.py 的 installed_environment 按节点批准的 ASCEND_HOME_PATH、业务账号 ~/Ascend/cann 和系统标准目录发现 set_env.sh，以业务账号加载，只转发允许的 CANN/PATH 变量；worker/host_backend.py 保留模板业务库目录并设置逻辑设备 0，worker/host_environments.py 在批准环境探测时复用加载逻辑。没有脚本时沿用节点显式配置，发现脚本却加载失败时关闭准入；Docker 不使用这条路径。操作见 docs/host-execution.md、root-managed-host.md，验证边界见 docs/validation/auto-cann-host-environment/README.md；回归 test_npu_cann_environment.py、test_worker_environment_probe.py、test_execution_launch_boundaries.py。

宿主机已结束单元恢复：worker/host_supervisor.py 写 exit.json；worker/host_backend.py 的 inspect 在 systemd 单元消失时，仅对匹配的已启动 launch.json 与有效退出码恢复 exited，stop 不再停止已卸载单元。其他单元缺失由 worker/client.py 拒绝重复启动，并与 Docker 缺容器错误分开显示。操作见 docs/host-execution.md；test_worker_execution_recovery.py 联合真实本地标记和模拟 systemd 卸载，覆盖退出码 0、非零、缺失、损坏以及禁止重复启动，配合 test_host_backend_lifecycle.py 验证身份边界。不可凭失败提示删除 journal 重跑同一执行。

宿主机设备环境边界：runner/image_runtime.py 的 environment 禁止模板/任务写入设备可见变量与 MINI_EVAL_*；worker/host_backend.py 再次校验任务环境是否在 host_runtime.overridable_env 内，并在业务变量合并后最后写 GPU CUDA_VISIBLE_DEVICES。NPU 仍以设备 cgroup 和逻辑设备 0 约束。操作见 docs/host-execution.md，验证见 docs/validation/host-device-environment-boundary/README.md；回归 test_execution_launch_boundaries.py。GPU 环境变量不是硬隔离。

GPU 宿主机本机身份复核：平台 backend/app/execution.py 的 slot claim 下发 accelerator_type、device_index、gpu_uuid；worker/host_backend.py 在物理 GPU 启动前比较任务编号与本 Worker gpu_index，再用 worker/client.device() 查询当前 nvidia-smi UUID，比对失败则不写 launch.json。NPU 使用 worker/accelerators.assignment 的卡号和 identity 校验；Docker 由设备运行时按 UUID 分配。操作见 docs/host-execution.md、gpu-scheduling.md，验证见 docs/validation/host-gpu-assignment-verification/README.md；回归 test_execution_launch_boundaries.py。此举不构成 GPU 硬隔离。

NPU/共享 GPU 后端准入：backend/app/scheduling.py 的 eligibility_codes 从模板获取 execution_mode，再与 `npu:{mode}:{gpu_mode}` 或 `gpu:{mode}:shared` 能力匹配；不得引用 runtime_mismatch 的局部 mode。回归 test_accelerator_resources.py，失败证据见 docs/validation/auto-cann-host-environment/README.md。

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

提交预检的逐卡原因：scheduling.runtime_mismatch 与 preflight.slot_reason 给出客户端/服务端所需宿主机环境名或容器镜像及 Worker 能力差异；orchestrations.diagnose_service 复用 DiagnosticContext，对托管服务候选设备追加服务网络/端口检查，预检 `server_checks` 与批次详情 `scheduling` 同口径。run-wizard.tsx 在预检确认显示服务端节点/设备和原因；无服务端角色成员时不把客户端卡误列为候选。操作见 docs/gpu-scheduling.md；回归 test_preflight.py、test_orchestrations.py、run-wizard.spec.ts。

客户端环境错误细化：preflight.image_reason/blocker_reason 只利用 Worker 批准配置和已保存检查报告，将镜像、宿主机环境、逻辑资产区分为未批准、节点缺镜像、检查失败或未上报；不转发报告原始 error/path。runtime_pending 区分配置版本未应用与报告缺失/过期。界面继续用逐卡 Reasons 渲染行动建议；操作及边界见 docs/gpu-scheduling.md、docs/user-guidance.md，回归 test_preflight.py 与 run-wizard.spec.ts。

节点新鲜度：freshness.py 将宿主机、容器和存储配置回执统一为 60 秒，将磁盘、GPU/NPU 与 Agent 主机资源采样统一为 45 秒；main.workers 与调度共用 telemetry_fresh/recent，未来时间戳及缺失字段关闭准入。Worker 在线默认 30 秒、执行租约 30 秒独立不变。操作见 docs/operations.md、docs/gpu-scheduling.md、docs/node-resource-reporting.md；回归 test_freshness.py、test_resource_roles.py、test_resource_reporting.py。

回连恢复：onboarding/executor.py verify/retry_verification 默认 300 秒、环境配置 30–1800；核对安装 revision/release 后才完成。Worker probe_dependencies.start 后台准备，心跳刷新 software，enrollment/worker_upgrades 仅豁免可选 service_readiness_v1 版本验收，业务准入保持能力门槛。文档 node-reenrollment.md、ssh-onboarding-executor.md、service-readiness.md；测试 test_reconnect_resilience.py 与 ssh-onboarding.spec.ts。HTTP 响应 5 秒超时与回连窗口分开诊断，不自动延长遥测有效期。

宿主机首次握手恢复（Worker 0.18.1）：client.execute 在 started=false 的首次心跳 503 时有界重试，原租约与停止信号持续有效；调度 tick 对已读取候选报告采用读取完成时刻检查，显式 at 保持固定，未来和过期数据仍拒绝。回归 test_worker_execution_recovery.py、test_worker_service_execution.py、test_scheduling.py、test_freshness.py；现场证据见 host-shell-episode-launch 验证记录。

节点组件升级界面：worker-upgrades.tsx 统一说明 Worker/Runner/Agent；发布标签沿用 Worker 版本，Runner 无独立版本，Agent 托管有条件。运行适配器复制新版 Runner，旧镜像内置 Runner 需重建。见 docs/worker-upgrades.md、指南 upgrades、worker-upgrades.spec.ts；不要把节点升级描述成镜像或驱动更新。

删除批次诊断：HTTP 500 的文本响应由 frontend/src/api.ts 显示为“请求失败 (500)”，不再出现 JSON 解析错误。失败时保留批次及比较选择；记录批次 ID、请求时间并检查 API 异常日志，必要时刷新确认状态后再重试。409 仍展示未清理或关联服务未停止的业务原因。节点工作目录不由该接口清除。测试见 batch-history.spec.ts、test_batch_deletion_records.py；前端错误展示修复不代表现场后台 500 根因已修复。

客户端启动前连通性诊断：更新客户端 Worker 后，journal 中 `client connectivity passed/failed` 记录目标 origin（协议、主机、端口）、检查耗时、超时上限，成功记录方式（HEAD/TCP/TLS）与 HTTP 状态（TCP/TLS 为 -），失败记录异常类型和原因分类；任务错误保留 `client_service_unreachable` 并附带分类（timeout、dns、connection_refused、tls、http_response 等）。不记录路径、查询、凭据、响应正文或原始异常文本。HTTP(S) HEAD 收到 405 仍判定可达。WS 所有路径仅检查 TCP，WSS 检查 TCP/TLS 并使用系统默认信任校验证书；不再发送 HEAD 或 WebSocket 握手，不验证路径、业务认证、子协议或模型就绪。DNS 结果逐项校验并固定数值地址后连接；业务是否就绪仍以服务端就绪检查为准。此修复针对 WS 服务拒绝 HEAD 导致 unsupported http method: HEAD / ClientResponseError 400 的误判。默认 3 秒、不读取环境代理不变。查看客户端 `journalctl -u 'mini-eval-worker-*' --since '10 minutes ago'` 并对照失败时间。无需数据库迁移；仅更新平台 API 不会更新已纳管节点的 Worker。

两端运行位置：批次默认“任务与结果”页提供独立“客户端分配位置”列；托管服务启动摘要和“服务端与关联”详情展示服务端分配位置。位置来自当前任务最新执行尝试的 Run.slot_id，包含节点、GPU/NPU 类型、卡号及型号；悬停可查看设备身份。位置表示资源分配，预约/运行/停止仍以状态为准。尚无分配或历史槽位不可用时明确提示，重试不会回退显示旧尝试的位置；软删除但仍保留的槽位可显示历史位置。外部服务只展示地址，不推测其节点或卡。旧 API 缺少 placement 时显示位置信息不可用，需要同步更新平台 API 和前端；无需数据库迁移或 Worker 升级。

### 客户端总执行时限

`backend/app/clients.py` 的认证接口 `/api/v1/execution-limits` 返回 `EXECUTION_TIMEOUT_SECONDS` 上限与默认值；客户端提交按该上限校验。`scheduling.py` 对已提交客户端使用快照时限，不因后续降低平台上限截短，并区分总时限、启动超时和租约过期。`frontend/src/run-wizard.tsx` 动态显示上限并阻止未加载配置时提交。操作见 `docs/operations.md`，验证见 `backend/tests/test_client_execution_limits.py` 和 `frontend/tests/run-wizard.spec.ts`；变更不涉及 Worker 升级或数据库迁移。

### 管理详情导航

`frontend/src/detail-navigation.ts` 管理独立详情的列表滚动/触发焦点恢复与草稿替换确认；机器、模板、集合分别接入 administration.tsx、templates.tsx、suites.tsx。列表保持挂载而隐藏，详情返回不会保存表单。验证见 detail-navigation.spec.ts、suite-layout.spec.ts 和 template-guidance.spec.ts；仅前端，无迁移或 Worker 更新。不要将当前组件内保留草稿描述成刷新后持久化。


### 服务数量、实例行与回合退出码

新编排 instance_count 是目标数量，旧 max_instances 仍是按需上限；orchestration_scheduler.fill_desired_instances 在客户端准入后用相同账本补实例，保留客户端容量并尊重暂停/终态/人工停止。orchestration-status.tsx 每实例一行并独立展开日志；位置来自该服务当前执行 Run，不由客户端或候选节点推断。episodes.py 的 include_planned 补充计划占位并返回判定来源。execution.Finish/Run.exit_code 保存整次执行实际退出码，runner host_episode/command_sdk 经 sdk 的可选事件 exit_code 保存子进程码；缺协议但退出 0 仍是异常。旧记录/纯 SDK 无进程码不可猜测。迁移 x407a89 与 Worker/Runner 0.18.2；先更新 API 再更新节点。见 docs/batch-monitoring.md、docs/task-input.md，专项 test_desired_service_instances、test_episode_visibility、test_host_shell_episode、test_worker_execution_recovery 和界面 orchestration-status/batch-monitoring。

可选执行限制：客户端 timeout_seconds=0 不设总时限，但租约、启动、取消继续有效。resources.cpu_unlimited/memory_unlimited 将正数调度预约与运行硬上限分开；最低预约继续参与容量判断和计账，领取运行轴为0并附reserved_*。模板cpus/ram_gb覆盖权限控制不限选项。Worker0.19.0/协议16，旧Worker在调度及领取拦截。见 docs/optional-execution-limits.md；回归 test_client_execution_limits.py、test_resource_contract.py、test_host_backend_lifecycle.py、test_worker_docker_contracts.py 与 run-wizard.spec.ts。无需迁移，非真机模型验收。

宿主机逐回合错误策略：host_runtime.episode_error_policy=stop（默认）或continue，仅per_episode客户端允许continue；runner/sdk.py记录ERROR后继续，全部尝试结束有错误则整体失败，不输出成功final。正常success=false仍是COMPLETED。setup/teardown、取消、租约和时限不受继续策略豁免。Worker0.20.0/协议17；调度、host/image环境诊断和claim阻断旧Worker。界面 templates.tsx复制保留策略，whole_task保存stop。见 docs/host-execution.md；测试test_host_shell_episode.py、test_command_sdk.py、test_sdk_reporting_contract.py和template-guidance.spec.ts。无需迁移。

可选限制真机修正：Worker0.20.1 的 resource_limits.verify_group 只读取有限 CPU/内存轴的 cgroup 文件，兼容 systemd 未启用不限轴控制器；有限轴缺文件和原 pids 限制仍拒绝。专项 test_resource_limits.py 覆盖 v1/v2。部署与真机边界见 docs/validation/episode-limits-live/README.md。

登录读取超时：Worker0.20.2 的 host_identity.login_environment 默认60秒，host_backend从节点Worker JSON顶层 host_login_timeout_seconds 传入整数1–300。超时报账号及实际时限，不输出环境值；显式环境/Docker不读取该设置。平台 STARTUP_TIMEOUT_SECONDS（默认120）与租约仍独立有效。test_host_shell_episode.py包含真实11秒Shell与边界/脱敏回归。配置与升级操作见 docs/host-execution.md“登录环境读取超时”；界面暂无配置字段。

登录函数兼容：Worker0.20.3 的 host_identity.login_environment 在普通变量校验前过滤 BASH_FUNC_*%%，修正 ml/module 导出导致 invalid；总环境上限先校验，普通变量名/长度/编码保护保留。test_host_shell_episode.py 使用真实 Bash 输出复现并回归；不传函数定义，不改变 Docker。操作及验证边界见 docs/host-execution.md“登录环境包含 Modules 导出函数”。

Worker0.20.4 登录环境名称改用进程环境语义：非空UTF-8，NUL/首个等号为分隔符，不再应用Shell标识符正则；函数过滤、大小/编码限制和平台覆盖不变。test_host_shell_episode.py 验证特殊名称及含等号/命令样式的字面值传到真实子进程；模板字段规则不变。见 docs/host-execution.md“登录环境名称兼容”。

Worker0.20.5：client.execute初始握手后使用专用线程发送started=false启动心跳，覆盖start_lock、登录环境和create/start；启动结束join再恢复运行心跳。取消/终止拒绝交主线程清理，watchdog仍独立保护，晚响应不复活失效租约。test_worker_startup_lease.py覆盖真实慢Shell、线程和三次连续执行，外部API/Docker/systemd故障注入；非现场根因确认/真机评测。配置与升级边界见docs/host-execution.md启动续租章节。

批次运行配置：frontend/src/batch-parameters.tsx按向导四组读取jobs.client_spec和orchestrations定义；batch-detail传入monitor已保存的suite_id。显示客户端/服务端模板名称版本、逐任务最终参数及parameter_sources、独立资源预约/硬限制；0与缺失区分。无实时模板补历史、不转储节点环境、不重跑预检。专项batch-run-configuration.spec.ts，操作docs/batch-monitoring.md，真实结束批次只读证据docs/validation/batch-run-configuration/README.md。仅前端发布。

0.20.5及配置页实验发布：docs/validation/startup-lease-release/README.md。仅local4090-151与ascend910b3-29组件升级；真实API/systemd下3任务9GPU回合和NPU服务通过，35秒慢登录是安装代码+模拟API/systemd隔离验证，勿合并宣称完整端到端长登录验证。原接单状态恢复、 held=0，测试模板归档，其他故障平台未确认升级。

批次删除外键回归：batch_deletion.remove 每层删除显式 flush，确保服务任务先于 ServiceInstance 删除，保持事务回滚；test_batch_deletion_records.py 必须先 tick 生成真实数据库服务记录，不能只提交编排定义后取消。SQLite 外键验证不等于现场 PostgreSQL 验收，见 docs/validation/batch-delete-foreign-keys/README.md。

日志 v2 API：backend/app/execution_logs.py、core.RunLogState/RunLogChunk、迁移 y518b90；256 KiB/256 块滚动窗口、Run 行锁、ACK 摘要与游标缺口；旧 run_logs 不改写。Worker 0.21.0 按 bounded_logs_v2 协商；runner/log_capture.py 与 worker/log_stream.py 提供有界采集、独立协议记录和上传，旧 journal 不切换。只有所选节点升级后新执行才启用，不能声称已自动修复其他部署。见 docs/execution-logs.md，专项及 PostgreSQL 竞态测试 test_execution_logs_v2/test_execution_logs_postgres。

Worker0.21.1 启动失败恢复：client.execute 将 terminal_failure 在停止/上报前保存到执行 journal，恢复只重放失败与清理，不重复创建；created/launch.started 是尝试标记，不是运行确认。execution.claim 附可选 service_instance_id；missing host unit 包含执行身份和判定来源。不同卡 Worker 独立 journal/unit，服务显示名称不作为身份。见 docs/host-execution.md“启动标记与失败恢复”、backend/tests/test_host_failure_recovery.py、test_service_instance_claim_identity.py；现场首次启动根因未确认，不能将模拟双 Worker 验证描述成真实 NPU 模型验收。

全量验证入口与夹具：python -m scripts.test_coverage 使用项目统一覆盖分母；浏览器测试需要 Vite（部分用例动态加载 /src/api.ts），不能统一替换成静态 dist 服务。集合跳转运行向导的模拟 API 必须返回 execution-limits，缺失时页面按设计拒绝继续。2026-11-19 首轮失败、复测与跳过证据见 docs/validation/host-recovery-full-20261119/README.md，不用复测通过覆盖首轮失败记录。

Agent HTTP 集成测试的模拟 systemctl 状态只在 start/stop 时写入，并原子 replace；只读 inspect 不得重写 JSON，否则断言与后台轮询并发时可能读到空文件。该夹具修正不改变实际 Agent 生命周期，失败证据见上述全量验证记录。

覆盖补充：test_migration_driver_scripts.py 需要 MIGRATION_DRIVER_TEST_URL 指向专用一次性 PostgreSQL；用例自行建立/删除数据库，scripts/check_*_migration.py 包含破坏性降级，不得使用平台数据库。2026-11-19 全量加三项迁移补测同实现合并已过覆盖门槛，不能抹去首轮失败/跳过或宣称第二轮全量通过；证据见 docs/validation/host-recovery-full-20261119/README.md。

数据库迁移操作入口：docs/database-migration.md，适用于其他实际部署机器。核对原 --env-file/-p/全部 -f 配置，api/migrate/bootstrap 使用同目标镜像；迁移前排空与备份，显式 run --rm --no-deps migrate 后启动并验收。restart 不迁移，Compose 不自动升级节点，不能用 stamp 或删卷绕过失败。日志优化 y518b90 有迁移，Worker0.21.1 恢复修复无新增迁移；head 以目标镜像为准。

2026-11-19 修正后完整复测：docs/validation/full-recheck-20261119/README.md；Python 1629 通过/133 跳过、前端 138 通过/10 跳过、无失败。本轮真实迁移补测 3 通过，合并本轮覆盖数据过门槛；默认 scripts.test_coverage 因 scripts 未启用迁移而覆盖不足仍返回 1，不能混淆测试成功与默认命令退出码。未更新 29 或验收真实 NPU 模型。

宿主机每回合参数映射修复（0.21.2）：HostBackend 保留回合相关占位符，cs_entry 将 direct/普通命令映射推迟至 host_episode；显式 per_episode command_sdk 使用相同单回合输入。task.episodes 为1，seed/路径随回合变化，外层计数不变。整任务/Docker 不变，无迁移。验证 test_episode_mapping_pipeline.py（真实子进程、协议1/2事件上传替身，第二回合运行时可上传第一回合结果）与 test_host_shell_episode.py；见 docs/host-execution.md。

Seed专项：test_episode_mapping_pipeline.py 覆盖初始0/42/2147483647、10回合、direct/普通命令/sdk-command及两种日志协议；断言实际业务进程 argv、环境、JSON输入及上报事件seed一致，取模2147483648回绕，字面量参数不改写。

业务PID上限（Worker0.21.4）：worker/resource_limits.execution_pids_limit统一校验节点null/缺省或正整数；host_backend在launch.resource_limits.pids持久化，systemd与两处verify使用同一快照。client.create按同一配置设置Docker --pids-limit（缺省/null为-1）；缺省/null使用TasksMax=infinity，不保证系统无限制。探测32不变。操作见docs/host-execution.md、worker-setup.md，回归test_execution_pids.py及验证记录docs/validation/execution-pids-20261119/README.md。

0.21.4发布验证见docs/validation/unlimited-pids-20261119/README.md：6机13槽升级成功；宿主机客户端和NPU节点服务pids.max=max，Docker -1仍可能被运行时systemd scope默认限制为有限值，检查实际cgroup，不以参数代替生效证据。


回合结果诊断（Worker/Runner 0.21.5，协议 17）：runner/episode_contract.py 的固定 EpisodeResultError 由 host_episode / command_sdk 抛出，sdk.py 受控上报 ERROR.error 和业务 exit_code。backend/app/result_views.py 统一两套统计，供 episodes、batches、monitoring、reports 使用；frontend/src/result-outcomes.tsx 统一展示。普通命令不参与协议成功率；原生 SDK 回合无独立进程不伪造退出码；旧单进程非零包装器退出码不推断为业务退出。操作与更新见 docs/host-execution.md、batch-monitoring.md、batch-reports.md；回归 test_result_outcomes.py、test_command_sdk.py 及 batch-monitoring.spec.ts。

0.21.5 上线验收：docs/validation/episode-result-release-20261119/README.md；6 节点 13 槽位升级，真实本机宿主机逐回合与 Docker 普通命令、真实页面验证两套结果。本功能无新增迁移，但部署从 x407a89 补迁移至此前日志版本 y518b90；先备份和隔离恢复验证。非 GPU/NPU 模型验收。

分作用域日志开发：runner/process_log_contract.py 定义身份/预算/游标；backend/app/execution_locations.py 按 Run 和 execution_id 查询历史目录，不使用当前节点配置推断。任务/批次共用 frontend/src/execution-location.tsx。新 Catalog/Read/Subscriber 模型及 z629c01 为后续按需日志读取基础，未完成 Worker 接入前不能宣称已支持回合文件。操作边界见 docs/execution-logs.md、database-migration.md；OpenSpec process-scoped-execution-logs 管理全链路实施。

整任务普通命令展示：`backend/app/result_views.py` 的 detail_mode 由任务类型及进程模式决定，`episodes.py` 不补造此类计划回合，`frontend/src/cs.tsx` 展示整次执行；批次入口复用同一模式。该模式使用任务退出码统计执行（不冒充独立业务子进程退出证据），保留生命周期诊断。协议单进程及逐回合路径不变；操作说明见 docs/batch-monitoring.md。

历史批次再次运行：`backend/app/batch_rerun.py` 提供只读 `GET /api/v1/batches/{id}/rerun-draft`，按任务/编排快照白名单恢复提交字段，批量检查模板和池；`frontend/src/batch-rerun.ts` 定义草稿契约，列表/详情经 CSWorkbench 导入 RunWizard。来源只在草稿显示，不复用执行地址、实例或幂等键。缺失、异构和不可用配置明确提示；操作见 docs/suite-run-preflight.md，定向测试见 test_batch_rerun.py 与 batch-rerun.spec.ts。无需本功能数据库迁移。

结果布局：frontend/src/batch-history.css 为程序执行/协议结果保留独立列并允许换行；result-outcomes.css 使用专用双列汇总卡片，窄屏堆叠，不复用 submit-grid。batch-history/batch-monitoring 浏览器测试检查文本边界，避免只验证文案却遗漏覆盖相邻列的问题。此修正只需前端发布，无数据库迁移或节点升级。

独立日志基础模块：runner/process_log_capture.py 为增量 JSONL 编码与异步有界写入队列；worker/process_log_reader.py 为可信目录句柄和分片索引上的安全分页。test_process_log_primitives 覆盖真实本地子进程退出、故障 sink、UTF-8、游标隔离、轮转缺口与文件替换。尚未接入 Worker 读取线程或生产 Runner，配额通道待决；不能将模块测试称为真实节点日志功能验收。边界与内部页预算见 docs/execution-logs.md。
