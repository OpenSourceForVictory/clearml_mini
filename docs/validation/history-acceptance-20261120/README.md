# 历史功能验收清单（2026-11-20）

本清单覆盖当前 OpenSpec 全部历史变更。它区分需求记录、测试资产、实际运行证据和发布验收；不把勾选任务、测试文件存在或覆盖率达标当作全功能正确的证明。目录内 [历史任务台账](changes.md) 列出每项变更及每条未勾选任务，不遗漏早期 MVP。

## 当前可复核基线

- `cc2f1fe`：Python 2218 通过、101 跳过；浏览器 197 通过、10 跳过。Python 行 91.73%、分支 82.58%，各目录和关键组门槛通过。[完整记录](../coverage-followup-20261120/README.md)保留失败与跳过。
- `0f19b46`：新增集合全选，6 项相关浏览器测试及 TypeScript 通过。[专项记录](../suite-selection-20261120/README.md)。仅前端变更，未重跑全部测试，不能把旧全量数字作为本提交重新全量的结果。
- 上述最新提交尚未完成发布验收。日志预算同步需 b841e23 迁移及 Worker 0.21.11；隔离备份恢复已验证，不能替代生产部署和选定节点升级。
- 浏览器测试中有模拟 API；真实 API/硬件证据单列。前端、未合并的子进程和真机没有 Python 覆盖率结论。

## 核心行为与证据对应

下列测试是可定位的现有资产，测试结果以对应版本报告为准，不声称本清单编写时重新执行了全部用例。

| 行为要求及必须断言的效果 | 自动化测试入口（仓库路径） | 实际运行证据与剩余边界 |
| --- | --- | --- |
| 宿主机登录环境只进入业务进程；超时/无效输出明确失败，参数与单回合输入一致 | `backend/tests/test_host_shell_episode.py`：`test_worker_to_host_episode_preserves_business_argv_env_and_input`、`test_per_episode_dynamic_mapping_uses_child_input`、真实 Bash 测试 | [宿主机验收](../host-shell-episode-launch/README.md)；不代表所有节点 shell 初始化脚本兼容 |
| 整任务保持总回合数；逐回合 seed、参数、目录按当前回合传递，stop/continue 分别执行 | `test_host_shell_episode.py`、`test_command_sdk.py`、`test_sdk_output_directories.py` | [宿主机验收](../host-shell-episode-launch/README.md)、[GPU SDK 产物](../artifact-runner-gpu-20261119/README.md)；不是模型质量验收 |
| 同节点不同卡、同名服务的身份/端口/日志独立；就绪前不运行客户端 | `test_service_instance_claim_identity.py`、`test_host_failure_recovery.py`、`test_service_readiness_execution.py` | [NPU 平台双实例](../npu-process-logs-platform-20261119/README.md)；真实 NPU 6/7 与客户端请求，但使用 SSH 转发，不证明现场公网连通 |
| 取消、租约过期或准备超时不误启动；重启不重复执行；清理确认前不释放资源 | `test_worker_startup_lease.py`、`test_worker_execution_recovery.py`、`test_host_backend_lifecycle.py`、`test_service_recovery.py` | [宿主机恢复](../host-recovery-full-20261119/README.md)；各故障的隔离注入不能一概说成物理故障验收 |
| 退出码与协议结果分开；退出 0 缺 final 不伪造业务成功 | `test_result_outcomes.py`、`test_sdk_reporting_contract.py`、`frontend/tests/batch-monitoring.spec.ts` | [结果分离](../episode-result-diagnostics-20261119/README.md) |
| 日志不跨执行/实例，权限拒绝、磁盘失败、断线/重启保留边界明确 | `test_process_log_pipeline.py`、`test_process_log_traversal.py`、`test_process_log_failures.py`、`frontend/tests/process-logs.spec.ts` | [GPU/Docker](../process-logs-live-20261119/README.md)、[NPU](../npu-process-logs-platform-20261119/README.md)；新预算版本仍需发布验收 |
| 慢节点读取不占满 API DB 连接；TTL/并发预算有界 | `test_process_log_load.py`、`test_execution_logs_postgres.py` | [2 连接池/慢节点验证](../coverage-followup-20261120/README.md)；是隔离负载，不等于生产容量承诺 |
| 产物目录按身份构造，API 共享直读鉴权/Range/穿越保护，节点离线仍可读 | `test_artifact_layout.py`、`test_shared_artifacts.py`、`test_artifact_media.py`、`test_sdk_output_directories.py` | [共享直读](../shared-storage-direct-20261119/README.md)、[GPU](../artifact-runner-gpu-20261119/README.md)；manifest/自定义目录仍有未完成项，NPU 无产物不要求 NFS |
| 升级先排空全卡；实际组件回连才成功；失败不恢复错误节点接单 | `test_worker_upgrades.py`、`test_agent_install_upgrade.py`、`frontend/tests/worker-upgrades.spec.ts` | [产物根发布](../artifact-root-release-20261119/README.md)是旧版本证据；0.21.11 界面发起升级和回执尚待验收 |
| 历史批次导入向导、参数映射展示、草稿和只读边界 | `test_batch_rerun.py`、`frontend/tests/batch-rerun.spec.ts`、`batch-run-configuration.spec.ts`、`suite-selection.spec.ts` | [配置展示](../batch-run-configuration/README.md)、[向导](../evaluation-wizard-clarity-20261119/README.md)、[全选](../suite-selection-20261120/README.md) |

测试路径未写前缀的 Python 文件均位于 `backend/tests/`。这些入口不是穷举所有用例，不能据此推断每种笛卡尔组合都覆盖。

## 历史待办的处理结论

| 变更 | 结论与下一验收动作 |
| --- | --- |
| suite-select-all | 已补实现、正常/边界/只读测试、指南和 Skill，已提交并随本轮前端发布 |
| execution-launch-snapshots | 用户已撤销，保留原讨论；由已完成的 batch-run-configuration 承接，不创建新表或 Worker 快照上传 |
| initial-mvp | ClearMLAdapter、ClearML 对账由 lightweight-workers 明确替代；其余调度/界面/部署目标需逐条关联后续证据，真实 RoboTwin 模型验收不能用 mock 代替 |
| lightweight-workers | 单/多卡并发与远程真实 RoboTwin 两项原复合验收仍需完整核对；未确定/核实 checkpoint、资产和模型，不标已完成 |
| generic-machine-runtime-management | SSH 部分已有后续变更承接，但五节点发布、模块边界、通用设备挂载和两节点参数路径验收不能根据后续功能存在就整项勾选 |
| process-scoped-execution-logs | 配置同步、真实界面升级和按需日志已发布验收；本轮继续逐项核对复合条款，不能按文件存在直接结项 |
| task-result-artifacts | 平台 output 根、manifest与模板自定义目录已交付；完整联合矩阵仍逐条核对。Worker 文件转发仍按用户决定暂缓 |
| host-inherit-task-limits | 空草稿；需求已由 host-shell-episode-launch 第 11/12 节及可选 PID 配置承接。保留草稿原状态，不伪造通过记录 |
| host-login-environment | 空目录草稿；实现见 host-shell-episode-launch。尚无独立 delta，原全仓 strict 的失败边界保持可见 |

具体函数名见 [重点测试用例索引](test-index.md)，方便逐条核对测试断言。

## 完成判据

后续每个功能按以下顺序补证据，不得仅因文件存在或全量通过勾选复合待办：

1. 列出要求的可观察效果、失败行为和兼容边界；明确被后续决策替代的条款。
2. 定位具体测试/参数化组合，检查断言是否验证实际 argv/env/文件/状态/资源，而非只验证 HTTP 200。
3. 补缺失测试，保留修复前失败；执行相关测试后再跑受影响全量。代码变更同步指南与 Skill。
4. 对要求真机的项目记录提交版本、组件版本、节点/设备、执行身份、真实进程和页面结果、资源清理；缺少环境就保留待验。
5. 发布单独记录迁移、活跃任务检查、实际部署/节点升级和回滚边界。旧版本证据不能自动覆盖新版本。

本清单完成的是可追溯性整理和已明确缺口的登记，不是宣告全部历史需求已经完成或功能正确性已得到保证。

## 本轮实际复验

`PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m pytest backend/tests/test_host_shell_episode.py backend/tests/test_host_failure_recovery.py backend/tests/test_worker_startup_lease.py backend/tests/test_sdk_output_directories.py -q --tb=short`：**103 通过，0 跳过，0 失败**，38.74 秒。使用测试临时目录/SQLite，含真实业务子进程及故障注入，不连接生产数据库。保留 Starlette/AnyIO 弃用警告。[原始输出](targeted-python.txt)。

该专项没有启用覆盖率采集，不产生新的覆盖率百分比，也不替代全量测试或 GPU/NPU 真机验收。集合前端 6 项通过、TypeScript 通过；[构建通过](frontend-build.txt)，保留大于 500 kB 的既有打包警告。未部署、未升级节点。

## 后续发布更新

2026-11-20 已完成上述源码的 API/前端部署、b841e23 迁移及 local4090-151 真实界面升级，日志预算回执与三回合业务/下载通过。原“未发布”描述是初始基线；最新发布边界以 [发布记录](../log-policy-release-20261120/README.md) 为准。其他节点未升级，其他历史待办未因此完成。

## 继续收尾更新（2026-11-20）

历史导入确认已移到向导操作区，修改配置后撤销确认；部署后真实历史批次只读导入与修改验证通过。产物窗口、返回上层、共享不可用时仍显示输出目录已发布。0.21.12 manifest 已通过管理界面升级本机 GPU 节点，使用安装包 SDK 的真实 CUDA 三回合和浏览器归属预览通过，见 [发布记录](../artifact-manifest-release-20261120/README.md)。其他节点仍保留原版本，没有借本机验收扩大全节点升级。

待办表数量已按当前 tasks.md 重新统计，仍未完成的原复合任务没有因补充一个切片自动勾选。其中模板自定义浏览目录已补齐实现与发布验收；通用设备映射、完整多节点发布矩阵及真实 RoboTwin 模型/checkpoint 验收仍有缺口；当前生产可用的日志、平台输出目录浏览与这些扩展项必须区分。Worker 产物转发、整机日志配额通道、NPU 产物共享继续遵守用户暂缓/不需要的决定。

最新产物真机矩阵见[最终矩阵](../artifact-final-matrix-20261120/README.md)，自定义目录已部署c952f34；NPU真实双实例、界面部署及停止清理均通过。全量最终结果以[补全回归](../full-followup-20261120/README.md)为准，旧基线数字不覆盖本轮源码。

## 早期重复条款复核

initial-mvp中的Compose启动、调度轮转/FIFO/限额、逐卡身份与TTL、超时断联取消、任务日志指标和React管理界面，已由lightweight-workers及后续专题实现。对应test_execution（公平/UUID/过期/权限/取消）、test_scheduling、test_memberships、结果/API合同、前端全套及[五节点验收](../production-fleet-acceptance/README.md)，本次实际六节点在线和部署健康复核通过。只完成不依赖ClearML的明确条款；带ClearML对账、远程真实RoboTwin与未逐条确认的复合项保持未完成。

generic-machine-runtime-management的SSH执行器和幂等安装（5.1/5.2）已有onboarding_transport、ssh_onboarding、onboarding_recovery_edges、node_installation/system_node_installer、Agent安装回连等实现和测试；日志/凭据脱敏、身份变化拒绝、安装中断不重放、非标准布局保护、NFS单元保留均有具体断言。隔离SSH目标完整安装5.3不据此勾选。

该变更6.1/6.2的五节点部署与跨物理机编排验收由2026-11-16 production-fleet-acceptance承接：0.12.0/协议10、五节点身份/NFS保留、8种host/docker跨机CS组合及两小时多用户排队/暂停/取消/重试。它是已完成的历史验收，不意味着本轮把五节点都升级到了0.21.12；本轮只升级授权的本机节点。最新版本边界见本轮发布记录。
