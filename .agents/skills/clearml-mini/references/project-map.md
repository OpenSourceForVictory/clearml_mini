# 实现与操作索引

宿主机登录环境与逐回合启动（Worker 协议 15）：`runner/template_contract.py` 验证 `host_runtime.environment_source` 与 `episode_launch`；`worker/host_identity.py` 以批准业务账号读取非交互登录 shell 的导出变量；`worker/host_backend.py` 合并业务环境并最后写设备可见变量，旧模板保留原入口环境。`runner/host_episode.py` 在同一受控执行单元内顺序监视前台子进程，`runner/cs_entry.py` 与 `command_sdk.py` 处理 direct/普通命令/SDK 命令；API `clients.py`、`execution.py`、`batches.py` 维护回合与结果契约。Docker 与服务端不启用新逐回合适配器。界面和操作见 `frontend/src/templates.tsx`、`guide-content.ts`、`docs/host-execution.md`、`docs/runtime-templates.md`；定向回归 `backend/tests/test_host_shell_episode.py`。普通命令回合完成不等于评测成功率，真实节点验证与模拟测试分别记录。

路径相对仓库根目录。用户操作入口是界面“用户指南”；维护者使用以下源码和文档定位。

本机平台于 2026-11-23 启用双 API 网关和角色连接池，2026-11-25 切换为下述四角色；六台机器的 13 个 Worker 卡位随后已升至 0.21.17。基础 Compose 仍是单 API 默认配置，其他部署不继承本机状态。发布顺序、真实 GPU SDK 小任务和 100 Worker 模拟负载边界见 [双进程验证](../../../../docs/validation/split-control-api-process-20261123/README.md)。

四角色 API 隔离为可选部署，2026-11-25 已替换本机双进程（提交 `51fe692`，13 个 Worker 卡位及 6 个 Agent 回连通过）；其他机器不会自动切换。`backend/app/priority_route_manifest.json` 是 167 条方法/路径的唯一分类源；`priority_gateway.py` 生成网关，`priority_runtime.py` 校验角色、总连接预算与 PostgreSQL 余量，并隔离各类准入队列。`deploy/priority-api-compose.yml` 分离执行控制、用户交互、Worker 数据和批量传输，仅 Worker 数据角色启动调度器；网关健康检查聚合四角色及配置指纹。部署、回退和连接预算见 [四角色部署指南](../../../../docs/priority-api-isolation.md)。单/双 API 保留原分类和行为。

证据见 [API 流量审计与混合基线](../../../../docs/validation/priority-api-isolation-20261124/README.md)：包含全路由请求体契约、事务/锁抽查、真实 PostgreSQL 混合压测。`backend/tests/control_plane_load.py --mixed-ui --continuous-batches` 使用模拟 Worker 驱动真实调度，重复提交、暂停/恢复、失败/重评测、幂等结果和完成；不能替代真实模型评测。Agent 心跳同时包含可合并采样与配置 ACK/事件，不能整条丢弃。API 隔离不消除跨池行锁，也未完成断联后持续执行与持久 outbox；租约 watchdog 仍生效。

| 边界 | 源码 / 文档 | 关联检查 |
| --- | --- | --- |
| 登录与会话 | backend/app/main.py、frontend/src/login-session.ts、api.ts、main.tsx；docs/login-sessions.md | 真实交互续期、闲置/12 小时上限、并发响应、同账号草稿恢复；轮询不续期 |
| 工作台、页面帮助 | frontend/src/main.tsx、navigation.tsx、user-guide.tsx、guide-content.ts；docs/user-guidance.md | 角色、表单草稿、键盘焦点、主题映射 |
| 业务评测向导与集合 | frontend/src/run-wizard.tsx、suites.tsx；backend/app/batches.py；docs/suite-management.md | 模板版本、参数快照、服务绑定、预检 |
| 模板和参数 | frontend/src/templates.tsx、command-mappings.tsx；runner/template_contract.py、parameter_contract.py、task_input.py；docs/runtime-templates.md、task-input.md | 类型、覆盖权限、argv/env 冲突、目录解析；业务字段已绑定时仍可在映射选择框定位现有配置，见 template-guidance.spec.ts |
| SDK 与直接协议 | runner/sdk.py、task_result_contract.py、command_sdk.py、episode_contract.py、cs_entry.py；examples/integration | Worker 按执行 ID 隔离转发任务级/回合事件；API 幂等与权限；final 和退出码独立 |
| 机器和节点程序 | frontend/src/administration.tsx、node-agent.tsx、ssh-onboarding.tsx；docs/node-agent.md、node-reenrollment.md | 心跳/遥测、期望与生效配置、排空、升级 |
| 资源池和调度 | backend/app/scheduling.py、job_resources.py、node_resources.py、orchestration_scheduler.py；docs/gpu-scheduling.md、resource-pool-membership.md | 服务/客户端共同预约、CPU/RAM/显存、准入与清理 |

| 服务运行和探测 | runner/service_entry.py、readiness_contract.py；frontend/src/readiness-editor.tsx；docs/service-readiness.md | 端口、网络、就绪门槛、服务代次及客户端关联 |
| 宿主机和容器目录 | docs/host-execution.md、directory-mounts.md、worker-setup.md | approved 环境、cgroup 委派、挂载能力、磁盘门槛 |

宿主机执行的控制端短时忙碌：`worker/client.py` 在执行心跳 HTTP 503 时保留正在运行的单元并重试，租约 watchdog 仍生效；其他 HTTP 拒绝不按瞬时故障吞掉。定位时对照 `backend/tests/test_worker_execution_recovery.py` 与 `docs/host-execution.md`。
| 运行结果 | frontend/src/batch-history.tsx、batch-detail.tsx；backend/app/batch_deletion.py；docs/batch-monitoring.md、batch-reports.md、interface-pages.md | 重试历史、真实回合分母、取消与删除语义；删除批次会清理其任务/运行数据并保留删除墓碑与审计 |

批次排队优先级：`EvaluationBatch.priority` 为 0–9，批次所有者可在运行记录里修改仍有 PENDING 项的批次；调度仅在用户内部按优先级降序、创建时间升序，跨用户公平轮转保留。API、迁移和回归见 `backend/app/batch_priority.py`、`backend/alembic/versions/aa741b2_batch_priority.py`、`backend/tests/test_batch_priority.py`；操作说明见 `docs/batch-controls.md`。

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

Agent 配置与观察并发维护：修改 Worker.connection 的管理路径（Agent 配置、单卡目录、纳管、SSH 清理、updater 回执）须先获取 Worker 行锁并刷新再合并；已有全局锁的路径保持全局锁→行锁顺序。Agent 心跳只更新独立观察记录，管理写不能复制旧观察快照回配置；读取最新样本使用 agent_state.record。回归 integration/test_node_agent_race.py 在一次性 PostgreSQL 控制事务交错，SQLite 测试不能证明行锁有效。

心跳连接池争用：backend/app/execution.py 空闲心跳、node_agents.py 采样和 services.py 健康探测更新最新数据库状态，不排队等待 scheduling.py 全局锁；同节点/服务通过数据库行锁保护序号与配置。运行中执行续租使用执行/设备局部锁，STARTING 转换仍用 fast_lock 快速失败 503，保留状态转换互斥；探测会话启动仍用全局锁。见 docs/operations.md 和 docs/validation/heartbeat-db-contention/README.md；回归 test_heartbeat_contention.py、test_service_readiness.py、test_node_agents.py。进行中的 worker-control-plane-backpressure 使用 worker/backpressure.py 分类暂时过载、解析 Retry-After 并在租约内退避；client.py 可靠上报失败后回续租循环，禁止在通用 HTTP 客户端无限重放。test_worker_backpressure.py 和 test_worker_overload_execution.py 是边界故障注入，不代表真实数据库容量或真机模型验收；backend/app/db_budget.py 提供默认关闭的普通/执行控制/Agent观察/可靠结果/后台调度五类池、全 HTTP 入口并发和在线 SQL 预算，main.db 将鉴权和处理绑定同类 Session；claim 无候选时只读返回，存在候选时尝试调度锁并复核，锁忙返回 200 空结果。连接上限按 API 进程数累加，先升级 Worker 再启用 DB_BACKPRESSURE_ENABLED。可选 `deploy/split-api-compose.yml` 把控制和普通 HTTP 路由到不同 API 进程，保留原外部端口；两个进程分别设置 `API_ROUTE_GROUP`，只有普通进程启动调度器，网关路径必须与 `request_category` 一致。双进程覆盖配置两边读取相同类别额度 23/40/9/7/1，普通进程仅创建普通/观察/结果/调度池（合计最多 40）、控制进程仅创建控制池（最多 40），双进程合计最多 80 个连接；单进程仍创建五类池；双进程覆盖配置设置 DB_ADMISSION_ENABLED=0，取消新增分类入口并发闸门；双进程线程额度保持默认 40，连接池是最大可同时持有的连接数，超时仍可返回 503。部署前须核对 PostgreSQL 连接余量。见 `backend/tests/test_split_api_process.py`、`backend/tests/test_control_plane_load_gateway.py`、`docs/operations.md` 和 `docs/validation/split-control-api-process-20261123/load/README.md`。test_db_budget.py 包含真实 PostgreSQL 超时、死锁和 HTTP 提前断开；backend/tests/control_plane_load.py 只新建并删除独立数据库，通过真实 HTTP 做阶梯负载，保留首次失败档，不代表真实模型或调度分配吞吐。当前容量与全量门槛以 docs/validation/control-plane-backpressure-20261123 的记录为准。负载工具 `--independent-logs` 将日志生产与心跳解耦；`--client-per-worker` 排除共享 HTTPX 客户端瓶颈，`--agent-telemetry` 加入独立 Agent 资源采样，`--trace-http-phases` 区分客户端传输前等待与响应等待。50 Worker 的 30 秒专项通过，约 25 分钟观察由用户决定提前停止；人为日志积压的 120 秒档整体未通过但心跳未超期，200 Worker 有控制准入拒绝及日志积压；详见该目录下的 `independent-logs/README.md`，不能据此宣称长期容量。

领取轮询：`backend/app/execution.py` 在认证卡位后先只读检查候选执行和待清理记录，完全空闲时不获取 `scheduling.py` 全局锁。候选时才尝试调度锁，锁忙返回本轮空结果，持锁后复核以防竞态；真实数据库/网络 503 仍交给 Worker 退避。`worker/client.py` 的领取循环使用 `Backoff(max_total=3)`，只限制单次领取重试等待，不限制次数或执行中续租。见 `docs/operations.md` 与 `openspec/changes/split-control-api-process`；回归 `test_execution.py`、`test_worker_backpressure.py`。

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

日志 v2 API：backend/app/execution_logs.py、core.RunLogState/RunLogChunk、迁移 y518b90；256 KiB/256 块滚动窗口、RunLogState 行锁及执行专属终态事务锁、ACK 摘要与游标缺口；旧 run_logs 不改写。v2 写入仅对 Run 取 KEY SHARE，运行中心跳取 NO KEY UPDATE；旧 `/logs` 仍更新 Run.log_bytes 并与心跳争行锁。Worker 0.21.0 按 bounded_logs_v2 协商；runner/log_capture.py 与 worker/log_stream.py 提供有界采集、独立协议记录和上传，旧 journal 不切换。只有所选节点升级后新执行才启用，不能声称已自动修复其他部署。见 docs/execution-logs.md，专项及 PostgreSQL 竞态测试 test_execution_logs_v2/test_execution_logs_postgres。

Worker 0.21.14 在 host create/start 调用边界将本地 OSError 转为可持久化的启动失败，避免与控制面通信故障混淆；硬中断/状态盘不可写仍拒绝不确定重启。中断点矩阵与首错失败证据见 docs/validation/host-missing-20261123/README.md。

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

分作用域日志开发：runner/process_log_contract.py 定义身份/预算/游标；backend/app/execution_locations.py 按 Run 和 execution_id 查询历史目录，不使用当前节点配置推断。任务/批次共用 frontend/src/execution-location.tsx。新 Catalog/Read/Subscriber 模型及 z629c01 为后续按需日志读取基础，Worker 0.21.6 已接通新执行的按需读取；未升级节点没有该能力。操作边界见 docs/execution-logs.md、database-migration.md；OpenSpec process-scoped-execution-logs 管理全链路实施。

整任务普通命令展示：`backend/app/result_views.py` 的 detail_mode 由任务类型及进程模式决定，`episodes.py` 不补造此类计划回合，`frontend/src/cs.tsx` 展示整次执行；批次入口复用同一模式。该模式使用任务退出码统计执行（不冒充独立业务子进程退出证据），保留生命周期诊断。协议单进程及逐回合路径不变；操作说明见 docs/batch-monitoring.md。

历史批次再次运行：`backend/app/batch_rerun.py` 提供只读 `GET /api/v1/batches/{id}/rerun-draft`，按任务/编排快照白名单恢复提交字段，批量检查模板和池；`frontend/src/batch-rerun.ts` 定义草稿契约，列表/详情经 CSWorkbench 导入 RunWizard。来源只在草稿显示，不复用执行地址、实例或幂等键。缺失、异构和不可用配置明确提示；操作见 docs/suite-run-preflight.md，定向测试见 test_batch_rerun.py 与 batch-rerun.spec.ts。无需本功能数据库迁移。

结果布局：frontend/src/batch-history.css 为程序执行/协议结果保留独立列并允许换行；result-outcomes.css 使用专用双列汇总卡片，窄屏堆叠，不复用 submit-grid。batch-history/batch-monitoring 浏览器测试检查文本边界，避免只验证文案却遗漏覆盖相邻列的问题。此修正只需前端发布，无数据库迁移或节点升级。

独立日志基础模块：runner/process_log_capture.py 为增量 JSONL 编码与异步有界写入队列；worker/process_log_reader.py 为可信目录句柄和分片索引上的安全分页。test_process_log_primitives 覆盖真实本地子进程退出、故障 sink、UTF-8、游标隔离、轮转缺口与文件替换。现由 runner/process_log_store.py、process_log_run.py 和 worker/process_logs.py 接通采集、文件与读取邮箱，frontend/src/process-logs.tsx 展示。整机配额默认不限制，跨账号配额通道不启用；已通过小程序测试不等于生产部署或模型验收。边界与内部页预算见 docs/execution-logs.md。

原生 SDK 接入回归：test_process_log_pipeline.py::test_native_sdk_process_logs 使用真正的 EvaluationClient，覆盖 host whole_task/per_episode 和 Docker 整任务、正常/业务失败/异常/非法结果/退出，核对参数、环境、seed 回绕、事件去重和日志归属。SDK 随 runner 组件包更新，旧式业务镜像不自动更新；更新指南和前端发布边界见 docs/worker-upgrades.md。

日志故障专项：backend/tests/test_process_log_failures.py 覆盖 process_log_run 与 worker/process_logs 的降级、身份拒绝、清理锁冲突、持久登记恢复和 API 故障。比例按 scripts.test_coverage 保留全项目分母；单文件达标不代表业务执行功能分组达标，详见 docs/code-coverage.md。

业务执行功能组补测：test_execution_group_gaps 覆盖真实 host_episode 控制与存储边界；test_process_log_concurrency 验证 4 进程写入、清理互斥、真实进程退出恢复与大索引；test_process_log_load 验证旧能力协商、离线积压和独立 PostgreSQL 两连接池。0.21.7 的 process_log_store.recover_pruning 使用持久 prune.json，在提交索引后才删除旧分片；Worker 历史同步负责恢复。验证边界见 docs/validation/execution-group-logs-20261119/README.md。

运行配置动态映射：batch-parameters.tsx 的 Mappings 合并 platform_bindings 与 parameter_schema.fields 的 binding（默认 input），值来自当前任务/服务定义快照。不得仅展示 platform_bindings 或将基础命令称为实际执行 argv。浏览器回归覆盖宿主机/容器客户端、服务端、业务绑定独立存在、任务切换及缺失旧快照；操作见 docs/batch-monitoring.md。仅前端变更，无 Worker/数据库升级。

运行配置命令展开：frontend/src/batch-command-preview.tsx 按 Runner 的占位符、业务 binding、platform_bindings 顺序预览 argv，env/input 单独处理；宿主机 per_episode 改写回合和 seed，SDK-command 非 per_episode 保留任务字段。使用已有任务/模板快照，无新表、上报或 Worker 修改。execution-launch-snapshots 扩展已由用户撤销。历史批次导入必须部署 batch_rerun API，不能只发布静态前端；通用 Not Found 与 batch not found 区分提示。

结果摘要简化：result-outcomes.tsx 的 ExecutionOutcome/ProtocolOutcome 用已知退出码、有效协议结果作为各自分母，并显示覆盖进度。ResultOutcomes 通过原生 details 保留完整计数，列表进入批次/任务查看明细；零分母不伪造 0%，协议异常与未接入区分。不改 result_views.py、数据库或节点。说明见 docs/batch-monitoring.md，回归 batch-history/batch-monitoring。

结果详情入口补齐：ExecutionOutcome/ProtocolOutcome 统一包含摘要及原生 details，供列表、任务行及顶部卡片复用；不再仅 ResultOutcomes 卡片有入口。测试覆盖行内独立展开、收起、轮询保留及窄屏。

0.21.8 普通业务账号日志：process_log_store.locked 对父目录使用 O_PATH，仅最终日志根使用读句柄，兼容宿主机工作目录只授予穿过权限；禁止符号链接不变。test_process_log_traversal 验证 service/business/episode、父目录不可列出、缺少穿过权限、最终目录不可读和链接/非目录拒绝。出现 process log unavailable 而业务仍运行时，核对 Runner 是否包含此修复，不能通过开放所有执行目录代替修复；更新与边界见 docs/execution-logs.md。

SDK 回合目录接口：runner/sdk.py 保留 Context.output_dir 与旧 artifacts 相对任务根语义；episode_output_dir 在 run_episode 中按需创建，独立 context 防止前一回合引用变化；_parent_episode_id 标记下使用既有输出根避免二次嵌套。artifact_path 仅生成根内相对引用，不上传。测试 test_sdk_output_directories.py；说明 docs/task-input.md。完整产物通道/侧栏设计见 openspec/changes/task-result-artifacts，未完成项不可当作已上线能力。

产物侧栏基础：frontend/src/artifact-browser.tsx 从任务/回合打开，backend/app/clients.py 按 EpisodeRecord 声明返回 episode_ids，artifact_media.py 提供文件限定短期 cookie 与 GET/HEAD/Range 访问。旧 RunArtifact.data 继续读；新方案为不上传文件、只登记节点索引并按需读取；节点目录通道尚未实施。测试 test_artifact_media.py、batch-monitoring.spec.ts；说明 docs/batch-monitoring.md。此阶段无迁移，不代表完整 task-result-artifacts 完成或真机验收。

通用产物预览：frontend/src/artifact-text-preview.tsx 使用 Range 和有界 stream 读取（即使服务端忽略 Range 也最多保留 256 KiB），切换时 AbortController 取消；JSON 懒展开/深度与子项限制、CSV 行列限制、无效 JSON 原文与截断提示。WebP 通过 artifact_media.py 白名单内联；HTML/SVG 保持下载。界面测试 batch-monitoring.spec.ts；阶段不变更 Worker 上传行为，不等同节点浏览已上线。

节点产物只读基础：worker/artifact_access.py 登记批准根/账号/版本，并以业务身份启动隔离子进程；worker/artifact_reader.py 执行目录分页和 1 MiB 范围读取，fd/no-follow、普通文件与版本校验。尚未接主循环/API/UI，不代表已停止上传；容器任意 UID 映射也尚待接线。内部契约与边界见 docs/node-artifact-reading.md；test_artifact_reader.py 包含真实普通账号权限及显式选择的 Docker 删除后读取测试。

API共享产物直读：shared_storage.py 管理显式节点/根/marker映射，shared_artifacts.py 做执行目录登记、任务鉴权和媒体授权，shared_file_io.py 隔离有界文件I/O并在读取前释放DB连接。复用 worker/artifact_reader.py，不启用 Worker 转发或 artifact_access 身份切换。新 SharedArtifactRoot 迁移 a730d12；Worker 0.21.9 收到 artifact_transport=shared 时仅登记路径、不上传正文，旧 Worker 不保证停止上传。shared-artifact-browser.tsx 提供共享/历史双入口。操作见 docs/shared-storage-artifacts.md；旧上传基础的历史说明不代表当前默认数据源。测试 test_shared_artifacts.py、test_shared_artifact_migration.py、batch-monitoring.spec.ts。

独立节点产物根：storage.py/worker Storage 同步可选 artifact_root，artifact_roots_v1 能力门控；shared_artifacts.plan 在首次 claim 使用现有 SharedArtifactRoot 冻结布局，不新增迁移。worker/artifact_layout.py 校验独立根并按业务账号创建批次/任务/执行目录，HostBackend 与 Docker 公共/别名绑定共用；不挂入工作目录树，保留清理隔离。runner SDK/host_episode/command_sdk 按 batch-v1 配置传当前回合目录；cs_entry 保持运行时 output_dir 别名的 env/input 一致。模板 platform.paths.output 显示为产物目录，普通命令不因新根改变 cwd。新 Worker0.21.10，旧镜像SDK升级边界见 docs/worker-upgrades.md、shared-storage-artifacts.md。测试 test_artifact_layout.py、storage.spec.ts、template-guidance.spec.ts。

产物根继承：storage.desired 对卡级缺省/null artifact_root 合并整机值，不修改保存的原始覆盖；其他目录仍整份覆盖。旧配置更新API后需保存整机存储触发新修订，无迁移或Worker改动。回归 test_storage.py 的多卡下发与回执校验。

产物Runner入口边界补测：test_runner_entrypoint_contracts覆盖SDK包装环境、宿主逐回合真实子进程、参数冲突拒绝、资产/挂载展开与连接检查。真实CUDA原生SDK按context.artifact_path登记回合文件并在结果页筛选验证，见docs/validation/artifact-runner-gpu-20261119；不把声明引用的归属测试当作自动manifest已完成或NPU模型验收。

向导分组与配置确认：`run-wizard.tsx`保留原草稿、预检序列和幂等提交；`wizard-fields.tsx`提供模块、运行摘要、环境变量双视图、JSON草稿和有效值表。`managed-services.tsx`直接显示服务资源池，跟随客户端池仅为展示，不修改提交优先级。预检最终参数/资源复用后端字段，不在前端重复解析规则。操作见docs/suite-management.md及界面wizard指南；新交互回归wizard-clarity.spec.ts，与run-wizard/orchestration-wizard/batch-rerun共同验证。此改动无需数据库迁移或Worker/Runner升级。

服务实例旧入口日志兼容：`services.logs`与任务日志按RunLogState选择v2分片或旧RunLog，不能仅查询RunLog；v2状态为空也不可回退拼接旧内容。查询限定当前服务关联的job及执行尝试，保留管理员权限和跨实例404。真机触发条件及回归见docs/inference-services.md、backend/tests/test_service_log_compatibility.py。修复仅API，无迁移或Worker升级。

服务探测会话申请也属于启动阶段：worker/client.py 在启动租约续期线程存活时重试 503/传输故障，受启动时限、取消和租约约束。test_worker_service_execution.py 覆盖 host/Docker 重试、拒绝、停止和超时；不能把 unit missing 一律归因于同名实例。

面向业务用户的入口是左侧“用户指南 → 业务接入”，主说明见 docs/user-guidance.md，完整操作/可复制样例在 examples/integration/README.md；指南必须区分 task_type、integration_mode、execution_mode 与宿主机 episode_launch，退出码不能替代评测结果。SDK 下载包完整性由 test_integration_bundle.py 校验源码一致及仓库外独立日志运行；新增 Runner 运行时依赖需同步 scripts/build_integration_bundle.py 清单并重新生成 frontend/public/integration/mini-eval-sdk.zip，普通 self_test 通过不能替代可选日志路径验证。

日志预算：backend/app/log_policy.py 管理整机期望版本与每卡回执；worker/log_policy.py 在历史日志线程持久化。首次领取冻结 ExecutionLogPolicy（b841e23），心跳不可覆盖回执；整机配额仍不限制。前端入口为机器数据保留，详见 docs/execution-logs.md。

集合全选由 suites.tsx 的原始任务索引选择，不受搜索过滤影响；当前显示选择仍替换原选择，批量操作包含隐藏选中项。已发布/归档通过 fieldset 保持只读。回归见 suite-selection.spec.ts，操作见 docs/suite-management.md；仅前端变更，无迁移或 Worker 更新。

历史功能验收索引：docs/validation/history-acceptance-20261120/README.md 关联核心行为、测试入口、真机版本及待验边界；changes.md 包含全部历史变更与未勾选条款。继续实施时按当前任务刷新台账，不能用历史通过报告或复合任务的部分实现代替当前完整验收；已撤销启动快照、已替代 ClearML 运行时和暂缓 Worker 文件转发必须保留用户决策。

日志预算 0.21.11 本机界面升级与部署验收见 docs/validation/log-policy-release-20261120/README.md：数据库 b841e23、本机 GPU 节点升级、其他节点旧版、三回合日志下载及预算还原；后续操作须重新查询当前版本，不能把此记录当全节点升级。

回合产物元数据：runner/artifact_manifest.py 写入目录内 .mini-eval-artifacts，SDK lazy 属性/host_episode 使用；worker/artifact_reader.py 在固定执行根内读显式归属，shared_artifacts.py 以协议逐文件声明优先。元数据不能授权任意路径；固定 -I 子进程按已安装绝对位置加载辅助模块，SDK ZIP 必须包含该模块。操作与兼容边界见 docs/shared-storage-artifacts.md。

向导资源布局：导航样式仅匹配 .shell > aside，托管服务资源摘要使用普通 section，避免 fixed 长条遮挡；run-wizard 的 wizard-resource-grid 将资源契约和高级约束跨整行；resource-requirements 的字段容器在向导内响应式排列。回归 wizard-clarity.spec.ts 覆盖1440/1024/390px及GPU/NPU/旧模板、锁定与草稿/预检参数，不改调度契约；操作见 docs/template-resource-requirements.md。

历史批次集合恢复：向导默认显示 source.suite_id 对应原集合（历史运行快照），客户端候选按集合关联过滤并保留当前可用的原客户端；历史任务仍通过cases提交、suite_id=null，不读取当前集合覆盖。用户显式点击使用当前配置才绑定实时集合。缺失客户端展示原名称及重选提示，fatal问题逐项显示且不绕过；回归 batch-rerun.spec.ts。

共享产物简化配置：shared_storage 在高级JSON为空时从 SHARED_ARTIFACT_HOST_ROOT 生成各节点同路径映射，固定API容器根 /mnt/mini-eval-artifacts；主 deploy/compose.yml 已内置变量和可选只读挂载，未设置变量时挂载 deploy/empty-artifacts 占位目录且不启用映射；原构建/启动命令不变。shared-storage-compose.yml 保留旧命令兼容。简化模式无磁盘marker检查，保留执行鉴权和路径边界；高级JSON（含[]）完全优先，旧快照不自动转换。见 docs/shared-storage-artifacts.md；测试 test_shared_root_config.py。

产物来源选择：SharedArtifactBrowser收到enabled=false仅显示未配置状态，不再调用legacy回调自动切换；来源由用户选择，刷新可恢复共享读取。回归batch-monitoring.spec.ts，指南docs/shared-storage-artifacts.md。

产物路径展示复用 execution-location API 的 output_directory 字段：读取 SharedArtifactRoot 执行快照或唯一同尝试目录清单，无映射校验、文件I/O或写入；计划与上报区分，旧记录不从当前配置猜测。ArtifactOutputLocation独立于文件来源及读取状态。

产物浏览UI：artifact-dialog为居中最大1280px原生dialog，窄屏留白并内部滚动；SharedArtifactBrowser返回父目录复用navigate清空分页/预览，根禁用。测试batch-monitoring.spec.ts覆盖居中尺寸、焦点与逐级返回。

产物弹窗嵌在批次表格DOM中，artifact-dialog须覆盖全局nav列布局/低对比颜色及batch-detail-table的fixed列宽；回归检查目录导航row和表格auto。

历史批次导入：每步底部的“历史配置确认”列出待核对字段与所属步骤，可返回已访问步骤修改。旧服务实例数必须先填写 1–20 的整数；修改配置后需重新确认并预检。无法恢复的问题仍禁止提交，无需数据库迁移或节点升级。

独立日志实时尾部不使用目录上报序号截断，目录刷新滞后仍可看到新输出；下载才固定结束序号，包含当前已显示页，后续分片沿用同一范围。该修正只需更新前端，不需要迁移或节点升级。

尾部请求只复用请求发起后 3 秒内的已完成页；仍在排队/读取的相同请求继续合并。旧订阅者的页保持原 30 秒有效期，固定范围下载的缓存不变。全局 128 槽位及用户/节点并发上限继续生效。该缓存修正需更新 API，不需要数据库迁移或 Worker 升级。


模板自定义结果浏览目录：ClientTemplate.result_directory 由 result_directories.py 校验，execution.claim 首次解析并写 ExecutionBrowseRoot；宿主批准根/容器持久挂载最深匹配、类型化业务参数、历史不补造。shared_artifacts 使用 directory=custom 命名空间，媒体 cookie 同时绑定来源；共享 I/O 前释放数据库连接。自定义文件不推断回合归属、不修改业务结果。迁移 c952f34，无 Worker 协议变化；指南 docs/shared-storage-artifacts.md、runtime-templates.md、database-migration.md。测试 test_result_directories.py、test_result_directory_migration.py、template-guidance.spec.ts、batch-monitoring.spec.ts。

共享目录快速切换遇429：SharedArtifactBrowser按照Retry-After等待1–5秒，最多重试两次；AbortController取消等待与旧读取，持续失败保留错误。API共享读取并发预算不变。真机快速来源切换复现，浏览器回归覆盖恢复/持续繁忙/切换取消。

宿主机批准环境面板只配置Python/代码目录；业务身份由纳管账号及已有本地批准配置决定。新纳管继承SSH账号，旧节点升级/重新纳管保留身份，不能通过该表单切换账号；说明与docs/host-execution.md一致。

服务管理列表复用execution-placement.tsx展示services.view已有placement；不得回退到实例target slot。部署选择按accelerator_type显示GPU/NPU。缺失字段与null沿用既有兼容提示，无迁移或Worker变更；services.spec.ts覆盖实际NPU、未分配及旧API。

服务旧模板部署表单按选中slot发送resources.accelerator_type，避免NPU目标被缺省GPU请求拒绝；资源契约分支仍使用契约值。services.spec.ts包含旧GPU字段兼容与NPU提交断言，保留修复前失败。

服务管理表格使用局部services.css的固定可读列宽和容器内横向滚动；STOPPED/CANCELLED的旧退出与探测消息折叠为历史诊断，其他故障仍显示。实际截图发现窄列折字，services.spec.ts验证位置列宽、390px无页面溢出及诊断折叠。

日志/产物复合边界测试与证据：`docs/validation/final-contract-audit-20261120/README.md`，包含真实PostgreSQL小池、登记/删除、路径替换、staging、宿主/Docker服务退出取消以及日志UI恢复。万scope历史索引不得称为万次业务吞吐验收。

SSH执行器IPC必须在释放认证Session和只读预检调度锁后调用，结果对账再开事务；cleanup/begin仍是实际清理授权边界。回归与失败证据见`docs/validation/onboarding-db-isolation-20261120/README.md`，仅API更新。

执行耗时：`backend/app/execution_timing.py` 统一批次/尝试/回合投影，批量读取 Event 最早 PENDING；迁移 ab852c3 仅加覆盖索引。`runner/episode_timing.py` 在 sdk.run 独立回合边界用 monotonic，`episode_contract` 验证可选 timing/schema=1；`execution.py` 按 episode_timing_v1 能力协商，Worker 恢复沿用快照。`episodes.py` 私有 `_platform_received_at` 不对外输出，原事件幂等比较须用 original helper，禁止业务输入私有键。前端 `execution-timing.tsx` 共用 tick，6 秒无新响应冻结。计时不改变调度/成功判据，不以缺时间触发重复启动。详见 docs/batch-monitoring.md、docs/worker-upgrades.md。

模板说明：`TemplateCreate.description` 为可选 2000 字符纯文本；客户端列/服务端 definition JSON 存储，迁移 ac963d4。注册、草稿复制编辑及向导当前选项展示，不参与运行参数；历史快照不回填。见 [运行模板](../../../../docs/runtime-templates.md)。

执行锁范围：运行中续租按 Run → Slot 行锁，不修改 Worker JSON；非执行配置确认仍锁 Worker。日志/回合/旧产物/任务结果按 Run 串行化，首次启动转换及资源分配/释放仍用全局调度锁。超时回收取行锁后刷新租约并复核，忙执行留到下次 tick。回归见 `backend/tests/test_execution_lock_scope.py`；运维边界见 `docs/operations.md`。

控制面预算新增 DB_OBSERVATION_POOL_SIZE / DB_RESULT_POOL_SIZE / DB_SCHEDULER_POOL_SIZE，完成与清理属于控制池，Agent 监控不借控制预留。AgentObservation/agent_state.py 与 ad074e5 迁移分离期望配置和最新观察；旧 API 不可混写，发布前检查迁移及同节点竞争专项。断联继续执行已实现计时、收据、控制对账与本地监督模块，完整执行循环和终态补传仍未接通，不能用当前重试代码宣称已启用。

2026-11-24 重新验证已部署版本：真机宿主机 SDK 单任务及同卡三任务排队均成功，100 仿真 Worker 混合上报完成 30 分钟；心跳无 503、日志最终无积压，普通通道仍有 97 次连接池 503。具体口径、全量回归与失败/跳过边界见 [真机与稳定性复测](../../../../docs/validation/real-load-full-20261124/README.md)。

模板说明引入同前缀区域名后，向导测试应精确定位模板 combobox，避免把“服务 1 模板说明”也匹配为控件；`frontend/tests/wizard-clarity.spec.ts` 同时验证未允许环境变量保留原输入、阻止进入资源步骤且不发预检。此项是测试选择器修正，不改变用户操作。

界面查询：`backend/app/read_views.py` 只用于请求内最新尝试及统计投影，不能用于调度/写操作或历史尝试接口；`batches?view=summary` 保留原完整默认响应。`monitoring.py` 仅待调度任务构造全局诊断，`orchestrations?view_mode=status` 省去批次结果重复计算。`administration.node_views` 批量获取节点占用；前端 `PollVisibility` 暂停隐藏页签而保留状态，顶层监控继续刷新控制修订。见 docs/batch-monitoring.md、backend/tests/test_read_views.py、frontend/tests/query-polling.spec.ts 与 docs/validation/ui-query-20261124/README.md。未修改调度快照或预约逻辑。

发布恢复边界补测：`backend/tests/test_release_recovery_boundaries.py` 验证备份清单/权限、隔离恢复顺序、历史记录数保护、失败清理及 HTTP 请求和错误脱敏；本机 HTTP 服务为真实协议测试，Docker 为故障注入。全目录覆盖复核见 `docs/code-coverage.md`，不得把专项低比例或模拟恢复当作全量/真实部署结论。

迁移降级事务边界：`ab852c3` 升级保留并发建索引，降级改为事务内删除，防止较早版本保护规则拒绝时留下“版本记录存在、索引已删除”的状态。检查 `test_endpoint_migration.py`、`test_retention_migration.py` 与 `test_execution_timing_migration.py` 的真实 PostgreSQL 回归；降级须排空维护，详见 `docs/database-migration.md`。这不是调度算法修改。

预约领取窗口：`scheduling.reservation_deadline` 仅登记本轮新预约或服务就绪后新开启的窗口，tick 在主要 flush 后提交前统一刷新期限；不续期旧预约、不延长执行租约。托管等待保留原时长，`reserved_at` 保留决策时间。回归见 `test_reservation_windows.py`，说明与提交延迟边界见 `docs/operations.md`。无需迁移或 Worker 更新，当前是否部署须另核实。

服务实例补齐公平性：`orchestration_scheduler.fill_desired_instances` 在客户端准入后使用独立 `SchedulerState(service_rotation)`，每用户每轮最多一个，用户内优先级/创建时间/ID 排序；资源 Planner 每次重新检查，游标与预约同事务。测试 `test_service_refill_fairness.py`，操作见 `docs/batch-controls.md`；无需迁移或 Worker 更新。

隔离调度基准：`scripts/scheduler_benchmark.py` 自建带唯一标签的 PostgreSQL 容器，不接受平台数据库 URL；使用说明及测量边界见 `docs/scheduler-benchmark.md`。断联继续执行尚未启用，跨 Worker/API/删除/服务关联的实施核对见 `docs/validation/disconnected-execution-20261124/implementation-audit.md`。

已有平台切换双 API 的可执行步骤见 `docs/split-api-startup.md`：保留项目名/环境/覆盖文件，基础 Compose 与 scripts/control.py 不会自动启用双进程；迁移前停两组 API，已有账号不重跑 bootstrap。

控制操作背压：重评 retry/retry-failed 与 nginx 同步进入 control；services.control_ack 提交前生成纯值确认，提交后不读取 ORM/完整详情。monitoring 批量重评只查选中任务但保留完整尝试关系。见 docs/control-operation-backpressure.md；test_control_operations、test_control_operations_postgres、services.spec.ts。调度锁仍保留，双进程不隔离 PostgreSQL 锁。

监控背压：frontend/src/poll.ts 支持 intervalMs，批次 monitor 5秒、其他默认2秒；失败指数退避至30秒并尊重更长Retry-After，document隐藏/卸载取消fetch，401停止自动轮询。api.ts ApiError保留status/reason/retryAfterMs。preflight.DiagnosticContext只在请求内按批次+服务集合+workload缓存托管候选并预计算holdings，不能用于scheduler共享快照。说明docs/batch-monitoring.md；回归test_monitor_backpressure.py、query-polling.spec.ts。

断联恢复基础（尚未启用新协议）：`worker/disconnection.py` 提供绑定执行/代次/boot ID 的单调计时与不可逆停止决定；`worker/reliable_outbox.py` 使用 SQLite FULL 事务持久化控制/结果、摘要幂等 ACK 和控制容量预留，日志继续使用原游标。尚未接入 Worker/API 恢复和界面，不能宣称旧租约已被替换。`client.save` 已补原子重命名后的父目录 fsync，失败向上传播。专项见 `backend/tests/test_disconnected_execution_foundations.py`；实施状态见 `docs/validation/disconnected-execution-20261124/implementation-audit.md`。

断联补报账本：`backend/app/recovery_records.py` 与 `ExecutionRecovery/ExecutionRecoveryReceipt`、迁移 `ae185f6` 保留原分配代次和事务收据；控制证据走 execution_control，结果走 worker_result。`worker/recovery_delivery.py` 逐批上传并匹配摘要后确认本地 outbox，不能将收据视为运行许可或清理确认。尚未接入领取授权、本地监督及生命周期恢复，不广告能力、不对历史任务启用。测试 `test_recovery_records.py` 覆盖真实 PostgreSQL 并发/迁移、HTTP 丢 ACK 与鉴权；运维和迁移边界见 docs/operations.md、docs/database-migration.md。

断联恢复默认值（开发中）：新协商执行的恢复授权默认显式保存 disconnect_tolerance_seconds=null，表示不限制断联时长；用户可选正整数秒。旧执行没有恢复策略字段时仍走旧协议，不能隐式启用。业务执行时限和已收到的取消独立生效。当前已实现基础模块、补报收据、控制对账与本地监督，完整领取/监督/结果恢复链路尚未启用，修改默认值不会改变现网执行。

调度恢复保护：scheduling.reconcile 对匹配执行 ID、Run ID、槽位的显式恢复授权，不再因旧租约或缺少启动上报将任务终结；撤销授权也不能充当进程已停止的证明。业务总执行时限仍独立生效，资源继续占用直至清理确认，旧协议及未领取预约维持原规则。批量读取使用相关 EXISTS，锁后重新核对授权与执行状态，不为每条执行增添独立授权查询。本次尚不向实际任务发放恢复授权，不能据此宣称端到端断联恢复已启用；回归见 test_recovery_scheduling.py。部署必须先完成 ae185f6 迁移。


控制对账开发阶段：backend/app/recovery_control.py + worker/recovery_control.py，迁移 af296a7 保存可变决策，原授权保持不可变。平台已生效取消/超时/终态优先于迟到节点结果；退出码仅保留事实，不能释放资源或撤销终态。API 决策核对执行/代次/boot/运行身份，重复响应不续期，节点先持久化再采纳。test_recovery_control.py / test_worker_recovery_control.py 覆盖容忍边界、长期迟到、身份冲突、取消锁竞争、重复及持久化失败；调度/控制/节点计时联合测试不等于真实 Worker 主循环验收，完整恢复仍未启用。操作边界见 docs/operations.md。


节点监督开发阶段：worker/recovery_supervisor.py 以执行级 flock、原子 fsync journal 保存控制时钟、原始业务起点、进程绑定、退出事实和精确重放请求。tick 本地观察/停止与 exchange HTTP 分开，不在网络期间持有监督锁。worker/recovery_runtime.py 使用 HostBackend.inspect(recovery=True) 的 InvocationID / Docker 完整 ID；缺少可信身份不接管，同名重建不停止，退出后卸载不抹掉原退出码。尚未接入 Worker.execute 或广告恢复能力。test_recovery_runtime.py 的 RECOVERY_RUNTIME_TEST=1 用真实 systemd 用户服务和本机镜像进行短程序+模拟时钟测试，API 联测为隔离 TestClient/SQLite，不是生产批次或 GPU/NPU 模型评测；不得将该测试当作功能已上线。

领取恢复基础：backend/app/recovery_claims.py 保存/查询原任务快照，迁移 b03a7b8；worker/recovery_claim.py 持久请求与幂等交接。查询走 execution_control，不取全局锁、不续租；收据 launch_authorized=false，平台取消/超时/撤销优先。绑定仅供后续领取事务集成，当前不广告能力、不接入生产主循环。test_recovery_claims.py / test_worker_recovery_claim.py 覆盖 HTTP 响应丢失、真实 PostgreSQL 并发及迁移、身份/摘要、重启与磁盘失败。操作和迁移限制见 docs/operations.md、docs/database-migration.md。

首次启动确认基础：backend/app/recovery_launch.py 提供 recovery/prepare（execution_control），与取消同锁序，原确认短窗口重放不延长；worker/recovery_launch.py 在副作用前持久启动意图，绑定 launch_id + boot + InvocationID/容器 ID。恢复收据不能代替该确认。recovery_control 首次运行身份额外核对确认的 boot/mode。test_recovery_launch.py / test_worker_recovery_launch.py / test_recovery_launch_runtime.py 覆盖取消、重放、落盘失败、双持有者和真实隔离运行实体；HostBackend/Docker 可选启动元数据和 RecoveryRuntime.start/inspect_launch 已接线，真实短程序验证原实例恢复；Worker 主循环仍未接线，不宣称完整恢复已上线。无新增迁移，依赖已有 af296a7/b03a7b8。

启动窗口受原分配剩余有限容忍时间约束，边界及极大合法容忍值测试见 launch-20261125.json；控制对账按已过秒数比较，避免绝对截止日期溢出。首次启动窗口过期不终止已经启动的原进程。

恢复结果应用：backend/app/recovery_results.py 接入 recovery/results 的显式 apply:true 分支，按 Run→恢复授权锁序应用 episodes.record 或 SDK task-result，和收据同事务。无全局调度锁，不校验旧租约、不更新终态/清理；worker/recovery_delivery.py 的 apply_results 模式只有 reconciled:true 才 ACK。test_recovery_results.py 覆盖取消/超时后补报、失败回滚、旧收据应用、真实 PostgreSQL 并发重放与调度锁独立性。仍未启用生产能力或接入完整 Worker 主循环，不能按单模块完成宣称上线。

恢复收尾：backend/app/recovery_finish.py 接入 execution.py 的 complete（可选 recovery）及 cleanup（可选身份正文）。锁序全局资源锁→Run→授权；完成验证已核对退出，保存原正文幂等且不释放，cleanup确认后才清理占用。平台终态优先；旧执行维持原路径，恢复执行禁止无证明旁路。缺结果时，旧协议仍由完整性校验拒绝；恢复协议显式 pending_results 可独立收尾，结果补齐经 results-finished 校验。主循环及界面尚未接通，不能宣称完整上线。测试 test_recovery_finish.py 包括实际HTTP和PostgreSQL取消竞争。

恢复对账保留同一进程的已确认退出：相同退出可用新序号重报，退出后运行实体卸载不抹掉退出码；退出码冲突或声称重新运行进入待核对，不能据此完成/释放。此规则同样适用于平台已取消或超时后的上报。

Worker 恢复收尾接线：worker/recovery_finish.py 通过原子 journal 持久化完成正文和 completion/remove/cleanup/done 阶段，Worker.finalize_recovery 调用，Worker.execute 对已有 recovery_finalization 优先恢复而不启动程序。RecoveryRuntime.remove 只接受同身份的退出实体，Docker 按完整ID删除，missing允许幂等重试；未经完成ACK不移除、未经清理ACK不清除执行记录。test_worker_recovery_finish.py 覆盖HTTP响应丢失、落盘失败、身份冲突和真实host/Docker短程序。领取和正常监督循环尚未接通，不作为功能已上线依据。

恢复领取API接线：execution.Claim的request_id与recovery_version必须成对，claim在普通候选查询前及资源锁后查询原收据，首次RESERVED→STARTING与recovery_claims.bind同事务提交。默认关闭新分配，RECOVERY_EXECUTION_ENABLED与槽位disconnected_execution_v1同时满足才测试启用；已有收据恢复不依赖开关。恢复执行不能由旧claim或新的request_id接管；恢复终态禁止裸cleanup列表，防旧Worker绕过身份证明。test_recovery_claim_integration覆盖响应重放、取消、未协商、空ACK及事务回滚；生产主循环能力未广告。

Worker 领取分流：`worker/recovery_poll.py` 接入 run，按能力或待恢复请求选择原协议/持久领取；先执行 journal 后清除 claim，重复交接保留进度，停止收据保留待核对。`execute` 禁止 recovery_assignment 误入旧租约路径；完整监督入口仍待实现，不得广告能力或把此阶段当作发布完成。

`worker/recovery_start.py` / `Worker.start_recovery()` 组合持久领取、运行文件准备、平台prepare、RecoveryLaunch和RecoveryRuntime：许可请求在准备后，attempt在实际启动前，恢复只核对原标识并补回父journal身份证明。当前为未启用组合入口，生产execute仍阻止新恢复执行退回旧watchdog；完整监督与交付尚待接线。


`worker/recovery_monitor.py` / Worker.monitor_recovery：独立本地tick与外部exchange，原boot/执行代次/运行身份核对；异常观察重试、退出持久化、关闭时保留未结束交换的独占权。尚缺完整生产执行入口和交付收尾。

恢复结果采集：worker/recovery_results.py / test_worker_recovery_results.py，四类旧读取器先写outbox再提交游标；final采集需原实体退出，尾部不完整不封口。worker/recovery_finish.finish_results同时要求本地最终采集标记、空队列及平台应用/完整性ACK；backend/app/recovery_finish.results_finished需完成与清理已确认。同步阶段不改变Run生命周期或资源。恢复旁路JSON不能被recover_journals当成执行UUID。完整生产入口尚未启用。

清理后的恢复补报入口已接通：执行记录的recovery_finalization为done且最终采集已封口时，Worker.execute只恢复原outbox的可靠交付，再请求results-finished；不启动程序、不续租。每次最多处理16条，响应丢失保留原记录重试；原outbox丢失时保留journal报错，不能创建空队列冒充同步完成。默认队列为`<execution-journal>.outbox/reliable.sqlite3`。这仅完成已有收尾记录的恢复，正常运行监督与完整生产启用仍待完成。

恢复同步确认results_finished只锁Run→grant，不获取资源全局锁；完成/清理保留原资源锁序。对应PostgreSQL测试持有调度锁期间结果同步仍可确认。

恢复退出证据：worker/recovery_runtime.py在原身份的实际退出或确认停止后原子保存runtime-exit.json，按执行/代次/launch/boot/运行身份验证重用；未知退出码保持None，不能把任意missing变成exited。重复观察不重复fsync；同执行线程共享证据写锁。recover_journals忽略此旁路文件。test_recovery_exit_evidence.py覆盖停止失败、身份替换、磁盘满、坏文件和幂等恢复；真实host/Docker监控测试覆盖停止后启动交接只恢复、不重启。
