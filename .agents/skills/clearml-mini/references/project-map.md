# 实现与操作索引

路径相对仓库根目录。用户操作入口是界面“用户指南”；维护者使用以下源码和文档定位。

| 边界 | 源码 / 文档 | 关联检查 |
| --- | --- | --- |
| 工作台、页面帮助 | frontend/src/main.tsx、navigation.tsx、user-guide.tsx、guide-content.ts；docs/user-guidance.md | 角色、表单草稿、键盘焦点、主题映射 |
| 业务评测向导与集合 | frontend/src/run-wizard.tsx、suites.tsx；backend/app/batches.py；docs/suite-management.md | 模板版本、参数快照、服务绑定、预检 |
| 模板和参数 | frontend/src/templates.tsx；runner/template_contract.py、parameter_contract.py、task_input.py；docs/runtime-templates.md、task-input.md | 类型、覆盖权限、argv/env 冲突、目录解析 |
| SDK 与直接协议 | runner/sdk.py、command_sdk.py、episode_contract.py、cs_entry.py；examples/integration | Worker 事件解析、API 回合校验、final 和退出码 |
| 机器和节点程序 | frontend/src/administration.tsx、node-agent.tsx、ssh-onboarding.tsx；docs/node-agent.md、node-reenrollment.md | 心跳/遥测、期望与生效配置、排空、升级 |
| 资源池和调度 | backend/app/scheduling.py、job_resources.py、node_resources.py、orchestration_scheduler.py；docs/gpu-scheduling.md、resource-pool-membership.md | 服务/客户端共同预约、CPU/RAM/显存、准入与清理 |
| 服务运行和探测 | runner/service_entry.py、readiness_contract.py；frontend/src/readiness-editor.tsx；docs/service-readiness.md | 端口、网络、就绪门槛、服务代次及客户端关联 |
| 宿主机和容器目录 | docs/host-execution.md、directory-mounts.md、worker-setup.md | approved 环境、cgroup 委派、挂载能力、磁盘门槛 |
| 运行结果 | frontend/src/batch-history.tsx、batch-detail.tsx；docs/batch-monitoring.md、batch-reports.md | 重试历史、真实回合分母、取消与删除语义 |

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

独立解释器与业务目录的可载入样例：frontend/public/template-examples/catalog.json 中三个 `-cwd` 样例；机器环境 `template_demo_cwd` 指定 Python 与 code_root，模板 working_directory 指向 examples/runtime_templates。准备说明通过注册模板页面下载 README.md（源 examples/runtime_templates/README.md）。

批次详情布局由 frontend/src/batch-detail.tsx 与 batch-detail.css 管理；参数与服务页按首次访问加载并保留组件状态。修改页签需回归 batch-monitoring、batch-controls、batch-reports、config-pages 浏览器用例，确保筛选、补跑尝试快照和控制版本不变。

SSH 纳管部署排查：docs/ssh-onboarding-executor.md；API 需要 ONBOARDING_SOCKET 及父目录 bind mount，宿主机 mini-eval-onboarding.service 使用 onboarding.server。发布检查 scripts/fleet_rollout.py 保护环境和挂载，不能把执行器缺失误判成目标 SSH 密码错误。

统一 root 纳管/普通业务身份：docs/root-managed-host.md；scripts/install_system_node.py 创建系统服务与 mini-eval，worker/host_identity.py 负责身份/权限/资源检查。host_execution 是节点批准配置，模板不能覆盖；旧用户服务升级保留身份。覆盖率门槛见 backend/tests/managed-host.coveragerc，真实测试需 MANAGED_HOST_TEST=1 和隔离 systemd 环境。

通用命令（Worker 0.17.0 / 协议 14）：docs/generic-commands.md、host-execution.md。host_runtime/runtime.command_mode=exec，缺省 legacy；runner/process_command.py 在最终参数/环境绑定后按业务身份解析程序，cs_entry/service_entry/command_sdk 共用。宿主机 exec 缺省 cwd 为 code_root，平台入口使用 Worker Python；Docker working_directory 独立于 work/output，平台 interpreter 可为绝对 Python 路径。调度检查 image_runtime、host_environments 与 scheduling，旧节点拒绝新字段。测试 backend/tests/test_generic_commands.py，Docker 显式启用，验证边界不是模型评测。界面指南与模板示例必须同步。

项目覆盖率：docs/code-coverage.md；backend/tests/project.coveragerc 固定 Python 六目录分母，coverage-floors.json 固定目录 80/70 与关键功能 90/80 门槛。scripts/test_coverage.py 全量测试并保留失败报告；scripts/coverage_report.py 按计数加权聚合目录/重叠功能。CI 测试或门槛不通过均失败；不允许删除低覆盖文件或降低门槛规避。前端未插桩和未合并的子进程单独注明，不称为全项目 100%。工作区有未完成特性时隔离验证，保留并披露原工作区失败。

纳管诊断回归：`backend/tests/test_onboarding_probes.py` 区分控制面响应超限、无效 JSON、网络失败与 HTTP 拒绝；`Rejected` 必须在 `ValueError` 前保留。Worker 执行恢复、宿主机环境异步探测与构建/部署 CLI 的故障测试见 `docs/code-coverage.md`，这些模拟边界验收不替代真机验收。

覆盖率补测的范围、失败证据和真接口界面验收见 `docs/validation/project-coverage/README.md`；新增 `templates-live.spec.ts` 通过显式隔离 API 开关验证四类模板注册，`suites-live.spec.ts` 按当前“评测运行与结果 → 评测任务集合管理”导航。

执行与恢复补测：test_worker_service_execution.py（健康探测、租约 watchdog、完成上报）、test_onboarding_recovery_edges.py（恢复身份、取消、凭据过期）、test_worker_bootstrap_transport.py（启动清理、传输）、test_execution_launch_boundaries.py（身份、端口、产物续租）、test_fleet_rollout_cli.py（发布检查点重入与排空前拒绝）。这些边界模拟测试不替代 systemd / GPU / NPU 真机验收；当前剩余门禁缺口见验证记录中的 scripts 目录。
