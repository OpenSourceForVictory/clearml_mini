# 断联继续执行：实施前接口核对

2026-11-24，基线 `a1ffea5`（当前工作区仅另有服务补齐公平性变更）。本记录是代码审计，不是功能完成或真机断网验收；线上仍遵循旧租约行为。

## 必须一起改变的边界

| 路径 | 当前行为 | 新协议实施要求 |
| --- | --- | --- |
| `worker/client.py:execute` | watchdog、启动检查、主循环及 Backoff 共用租约截止；心跳失败会阻塞后续进程检查，永久错误可停止程序 | 拆出本地监督与交付两个职责；已持久领取的执行按业务期限及取消控制，网络新鲜度不充当停止许可 |
| `scheduling.reconcile` | 超过租约加宽限即 TIMED_OUT；保留 cleanup=false 并隔离卡 | 仅对新协商策略保留执行状态及全部预约；连接状态单独显示，不假定已退出；旧策略不变 |
| `execution.live` | 心跳、回合、任务结果、完成等拒绝过期租约 | 新恢复入口校验归属、原执行代次及控制意图；不能简单删除 live 检查或让任意旧身份续租 |
| `execution.claim` | STARTING/RUNNING 恢复仍需有效 lease；仅靠 known_execution_ids 不等于持久接管 ACK | 同一分配响应丢失可恢复；平台必须知道该策略已经授予，Worker 先落盘再启动 |
| `services` 健康接口与 `service_dependencies` | 健康上报和新客户端绑定均依赖有效租约 | 失联服务不接受新绑定；重连核对运行身份和重新就绪；已有绑定继续保留 |
| `Worker.save/recover_journals` | 保存任务/created/游标，启动前写入；恢复线程重新进入 execute | 加入不可变协议策略、进程身份、业务期限、退出摘要和交付 ACK；复用执行 ID，不重新启动已结束程序 |
| `batch_deletion.remove` | 只有终态且全部清理后允许删除，然后删除 Run 和数据 | 若允许失联期间删除，需要保留安全归属和取消墓碑；现有删除限制不能被直接绕过 |
| `complete/cleanup` | 结果完整性、终态与清理在既有顺序内处理 | 程序退出、协议结果待同步、物理清理分开；重复补报不能重新占资源；日志积压不能阻止安全收尾 |

## 实施顺序与发布门槛

1. 冻结新执行的能力与策略，定义恢复请求、不可变身份、控制版本和可靠结果摘要；增加对应迁移、旧记录默认旧策略及往返迁移测试。
2. 节点本地持久监督：实际业务时限、本地停止、进程身份核验、退出及未确认结果；领取/启动/退出各崩溃窗口单独测试。
3. 平台保留预约与恢复对账、取消/完成序列化、服务新绑定禁止；终态补传独立鉴权，不用过期租约接口顶替。
4. 接入 Worker 双向能力协商、交付队列和界面连接/同步状态；全部相关能力验收之前不广告支持、不启用新任务策略。
5. 宿主机与容器真实进程测试：断网超过旧租约仍完成多个回合；断网结束后不重启；API/Worker 重启、ACK 丢失、取消、磁盘满、同节点多实例及旧版本兼容。禁止用仅修改 lease 数值的单元测试代替。

此工作对应 `worker-control-plane-backpressure` 的 4.1–4.10 和关联控制重试条目。不能只勾选 4.6，就宣称断联执行及补报完整；也不能仅延长 LEASE_SECONDS 或关掉 watchdog 作为实现。服务补齐公平性和调度等价优化不能暗中改变这些生命周期规则。

## 2026-11-25 基础实现进展（未启用）

- `worker/disconnection.py`：有限正整数秒/null 不限制；只处理原执行、原代次的有序控制确认，按请求发出时间刷新单调计时。截止后的 ACK 不撤销停止；同 boot ID 恢复不重置计时，机器重启需对账。调用者必须持久化快照后才确认状态，不能把内存对象当作完整监督器。
- `worker/reliable_outbox.py`：本地 SQLite FULL 事务存储控制、回合与任务结果；相同键同摘要幂等、异摘要拒绝。ACK 后保留摘要墓碑，只有原执行全部确认后才允许显式清理；不按离线时长删除。控制预留独立于普通结果，数量限制包含 ACK 墓碑，字节限制按待交付 UTF-8 正文计，实际磁盘另有 SQLite 页及事务开销。磁盘/事务失败明确抛出，不能返回接收成功。日志不放入此队列。
- `Worker.save`：文件同步及原子重命名后补父目录同步，I/O 失败传播；这是当前 journal 的持久化修正，不改变租约停止、取消或资源释放语义。
- 基础测试包括真实 SQLite 并发、重复 ACK、容量预留、冲突、子进程骤停后的提交保留与事务回滚，以及 Worker journal 的目录同步失败。数据库失败使用事务故障注入，不宣称真实磁盘耗尽或整机掉电验收；独立子进程覆盖率未合并。

尚缺策略配置/能力协商、运行循环本地监督接线、服务端恢复鉴权与迁移、补报接口、取消删除并发及界面状态。新模块暂不被现有执行协议调用、不发布能力标记。任务 4.2、4.6–4.10 保持未完成；不得通过增大租约或放开 `live` 校验代替后续实施。

专项命令：

```bash
PYTEST_ADDOPTS='--import-mode=importlib -k "disconnected_execution_foundations or worker_execution_recovery or worker_startup_lease or worker_service_execution or worker_recovery"' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/disconnected-execution-foundations
.venv/bin/python -m pytest -q backend/tests/test_coverage_reporting.py --import-mode=importlib
```

本轮 144 通过、2865 未选择、1 个依赖弃用警告；统计工具另有 14 通过。两个新增模块行/分支均 100%，`worker/client.py` 专项行 65.29%、分支 51.90%，不是全模块达标结论。统一脚本保留完整分母，全项目门槛未通过，退出 1；没有降低门槛或排除业务文件。原始报告位于上述 coverage-artifacts 输出，精简证据见 [foundations-20261125.json](foundations-20261125.json)。首次统计工具测试路径拼错退出 4，改为实际文件名后通过，保留在证据中。

## 2026-11-25 服务端补报收据与交付器

已实现 `ExecutionRecovery` 不可变执行归属/代次及 `ExecutionRecoveryReceipt` 幂等账本，新增 `ae185f6`。控制、回合、任务结果均按执行/类型/序号和摘要去重；仅取原执行账本行锁，不争无关全局调度锁。事务提交后才 HTTP ACK；晚到消息无需旧租约，但不能由此变更 Run 状态、续租或释放资源。撤销/删除后业务数据拒绝；原节点鉴权有效时仍可保留控制证据。节点凭据撤销依旧 401，无法绕过。

`RecoveryDelivery.step` 将本地队列交给对应控制/结果接口，核对服务端摘要后推进本地 ACK；响应丢失可重放。HTTP 收据 `reconciled: false` 不是恢复运行许可。接收账本的正文容量和条数有上限，给控制记录预留空间；未确认结果留在节点，不丢弃。

新接口分别归执行控制与可靠结果，更新单/双/四角色分类与网关。历史 167 路由验收文件不改写；两个新增路由及 schema 单独保存在 `recovery-route-contracts.json`，测试合并后二者必须精确覆盖当前 169 路由。

验证命令：

```bash
# LOG_TEST_DATABASE_URL 必须指向专用临时 PostgreSQL，fixture 自建并删除独立数据库。
PYTEST_ADDOPTS='--import-mode=importlib -k "recovery_records or disconnected_execution_foundations or priority_routes or priority_gateway or split_api_process or execution_lock_scope or worker_execution_recovery or worker_startup_lease or worker_service_execution or worker_recovery"' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/disconnected-execution-receipts
```

260 通过、2776 未选择、无跳过；统计工具另 14 通过。两个本轮新增逻辑模块行/分支均 100%，此前两个基础模块也均 100%。这是专项，统一脚本的全项目门槛仍未通过、退出 1；不宣称整项目达标。失败轨迹与迁移边界见 [receipts-20261125.json](receipts-20261125.json)。真实 PostgreSQL 验证并发 16 次同记录仅持久化一次、不等待无关调度锁，以及升级/回滚保护；HTTP 加本地队列验证长期旧租约、断网和提交后丢响应重放。

当前领取路径仍不调用 `grant`，新策略未广告、未部署。仍需发放/持久接管授权、本地监督接线、控制版本与实际进程身份对账、可靠结果应用及资源收尾、向导配置/展示和真机端到端验证。不能把本节的“消息已收”说成“任务恢复完成”。

### 新策略默认不限断联时长（2026-11-25）

恢复授权 grant 默认 tolerance=None，显式固化为 null；参数化验证省略值、显式 null、有限 120 秒，以及既有授权不可静默改写，共 4 项通过（24 项未选择）。首次宽泛 -k grant 为 2 通过、1 跳过（未提供 PostgreSQL 测试连接），之后精确选择本次默认值测试，无跳过。OpenSpec 严格校验通过。本次未重跑全量覆盖率，不据此宣称全项目达标；未部署，完整恢复链路仍未启用。同步设计、运维说明与 Skill 实现索引。

### 调度侧断联保护接线（2026-11-25）

新 test_recovery_scheduling.py 验证 STARTING/RUNNING/CANCEL_REQUESTED、有限/无限、撤销、旧授权错配、业务时限与未领取预约；新增真实 PostgreSQL 场景。调度器在批量查询和锁后复核中读取原授权，不能凭租约或缺启动上报终结已授权执行。初轮调度 28 通过；统一覆盖率专项 155 通过、8 跳过（真实进程样例 opt-in 未开）、2896 未选择。专项调度模块行 86.39%、分支 63.01%，全项目门槛未通过；保留 coverage-artifacts/recovery-scheduling 原始报告，继续补充相关运行与资源回归。授权发放和完整 Worker 恢复仍未启用，不勾选 4.6 完成。

### 四角色发布及真实业务进程回归

代码 bd64d5c 已构建为 mini-eval-api:recovery-foundations-20261125，并更新本机四角色 API；数据库从 ad074e5 升到 ae185f6。发布前无活跃任务/待清理，保留生产备份；发布后 13 卡位和 6 Agent 时间戳均推进，主要页面接口 200。恢复接口未认证均 401，未向生产执行发放恢复授权。完整断联继续执行/对账仍未启用，不能把本轮发布视为该功能验收。

扩大专项 254 通过、7 环境开关跳过；调度 scheduling.py 行 93.88%、分支 84.93%，收据 recovery_records.py 为 100%/100%。标准脚本仍因全项目分母门槛退出 1，不宣称全项目达标。额外失联服务就绪用例 1 通过。首次 8 个 opt-in 跳过的多批次样例已实际开启，宿主机/Docker、共享/独占、失败/取消的 8 场景全部通过（394.84 秒）；真实子进程覆盖率未合并。

平台可见批次：宿主机 SDK 4515cae8-72f8-4da5-a816-6b6acba096a7（业务成功/失败，程序均退出 0）；Docker 命令 b52918ed-f192-4684-bedc-2c7b02dce4ed（退出 0/1，对应执行成功/失败）。四项均确认清理，未修改原业务模板，未升级现有 Worker。这是实际节点上的短程序验收，不是模型评测，也不是长期断联验收。生产备份另在隔离 PostgreSQL 恢复并升级，461 执行/448 任务数量不变；本机直接调用 alembic 首次因 import 路径失败，使用 python -m alembic 后成功，生产容器迁移无此错误。完整结果见 release-20261125.json。

扩大专项的 7 个环境跳过随后全部补跑通过（6.39 秒）：新建专用资源迁移库，并开启 DIRECTORY_MOUNT_TEST=1、HOST_EXECUTION_TEST=1，真实两组 Docker 只读/可写挂载和四种 systemd 业务目录模式通过。首次跳过记录保留；补跑未合并覆盖率，不据此提高统计值。


### 控制对账与心跳容忍专项（2026-11-25）

新增 recovery/reconcile（execution_control），全路由契约现在为 170 条；control 决策由 af296a7 独立表保存。平台已提交取消/超时/终态优先，节点迟到退出事实不能覆盖；重复响应不刷新租约，身份冲突不停止其他进程。Worker 决策先持久化后采纳。默认不限断联、有限截止边界、连续两次心跳超期恢复、超限一年后补报、平台取消/超时覆盖迟到成功、服务重新就绪、并发重复、取消锁竞争和迁移降级保护均纳入专项。

统一统计命令选择 recovery_control、recovery_records、recovery_scheduling、disconnected_execution_foundations、priority_routes、priority_gateway、split_api_process、execution_lock_scope：264 通过、无跳过、2873 未选，104.33 秒。随后补充联合测试 4 通过：host/Docker 身份 × 默认不限/有限 120 秒，实际调用调度器、对账决策与持久化节点计时；这不是真实宿主机或容器断网启动实验。新增 API 模块行 100%、分支 98.53%，Worker 模块 100%/100%，未降低门槛；全项目门槛仍因专项范围未通过。

初轮失败证据：10 失败、221 通过，有限配置缺少原始 enqueued_at 时 TypeError；修复为待核对且不重置计时，补充缺失起点测试。单独早期运行 9 失败/37 通过，修复后 67 通过。最终证据见 control-20261125.json；原始覆盖率保留在 coverage-artifacts/recovery-control 与 recovery-control-final，不合并无关历史覆盖率。

尚未完成领取协商、真实 Worker 主循环接线、终态应用及资源收尾，未部署该控制阶段，不宣称完成核心目标的端到端验收。迁移 af296a7 仅专用 PostgreSQL 升降级验证；生产仍 ae185f6。


### 本地监督与运行实体接线验证（2026-11-25，开发阶段）

新增 recovery_supervisor.py：独立本地 tick 与 HTTP exchange，先持久化请求再发送，丢 ACK 原样重放，文件锁防双监督者，重启不重置计时。新增 recovery_runtime.py：读取真实 systemd InvocationID / Docker 完整 ID；不同运行身份不停止，原进程消失不伪造成功。HostBackend 默认 inspect 行为保持兼容，只在 recovery=True 时读取 InvocationID。

第一轮专项 195 通过、无跳过、2981 未选择（32.57 秒），新监督与 runtime 模块行/分支均 100%；host_backend 整体尚为 78.93%/59.09%，因此扩大相关回归。后续独立真实运行/API 联测 8 通过（15.00 秒）：其中 6 项使用真实 systemd 用户服务或 Docker 短程序，2 项为 runtime 身份隔离单测。断联时刻由单调测试时钟注入，HTTP 故障注入不改变生产网络；API 联测使用隔离 TestClient/SQLite。不是实际平台批次、NPU/GPU 模型评测或长时间断网实测。

中间扩大运行 330 通过、5 跳过、2843 未选择（70.73 秒）；其中覆盖率生成期间新增了关闭监督者保护代码，源码行号与该轮运行不再一致，因此不将这一轮新模块覆盖率作为验收证据，不合并到最终报告。原始记录保留在 coverage-artifacts/recovery-supervision-expanded。最终覆盖率必须由源码稳定后的独立完整专项重新生成。

尚未广告恢复能力、尚未接入生产 Worker.execute 领取/启动/结果/清理路径，不应按此阶段升级生产并认为断联继续执行已上线。下一步完成领取响应丢失恢复、可靠结果应用及清理确认后，再统一接线、发布和平台可见批次验收。


最终扩大专项 653 通过、50 跳过、2476 未选择（237.82 秒）。其中 45 个 Docker 环境跳过已开启 GENERIC_DOCKER_TEST / PROCESS_LOG_TEST_IMAGE / MINI_EVAL_TEST_ARTIFACT_DOCKER 补跑，最终补跑与监督专项 163 通过、无跳过、3017 未选择（100.56 秒），并核对测试标识确实覆盖原 45 项。剩余 5 项需要 root、systemd 和专用业务账号，当前用户无免密 sudo，保留跳过而不宣称通过。

最终新监督和 runtime 模块行/分支均 100%；未再修改的 host_backend 在扩大专项为 100% / 92.73%。这些为各自标准报告的模块指标，没有合并独立进程或不同报告充当全项目结果；标准脚本仍因专项不能满足全项目门槛而退出 1。补跑还覆盖只创建尚未启动的容器不能当作退出 0、旧监督者关闭后不能再写入、停止操作期间不能释放监督权。结果见 supervision-20261125.json。没有新增数据库迁移，没有部署或升级生产节点。

### 领取响应丢失恢复基础（2026-11-25，未启用）

原领取快照与授权事务绑定，查询不续租、不取全局锁、不重新渲染任务；节点持久请求和幂等 journal 交接覆盖重启、双持有者与落盘失败。平台已取消/超时/撤销或执行身份变化，不返回启动配置，收据始终不是启动许可。新增 b03a7b8，仅隔离 PostgreSQL 16 迁移验证，未部署。

首轮扩大专项 359 通过、1 失败：真实 API 统一参数校验返回 400，新增测试错误预期 422。核对原处理器后修正测试，不修改错误处理语义；失败证据保存在 claim-failure-20261125.txt。补充非规范 UUID 拒绝用例后，标准覆盖率入口最终 361 通过、0 跳过、2881 未选择，118.05 秒。新 backend/app/recovery_claims.py 与 worker/recovery_claim.py 行和分支均 100%，不代表全项目覆盖率；专项仍未满足全项目门槛，脚本退出 1，pytest 退出 0。详见 claims-20261125.json。

本轮验证包括真实 PostgreSQL 并发重复领取、持有调度锁时查询、迁移与实际 API 鉴权；HTTP 响应丢失为隔离 TestClient 故障模拟，不是真实生产批次或 GPU/NPU 模型评测。启动前对账、实际 Worker 领取/监督循环及终态清理尚未集成，不能宣称完整长期断联已完成。

### 首次启动确认和实际运行标识（2026-11-25，未启用）

实现 recovery/prepare、节点持久化一次性启动意图，以及 HostBackend Description / Docker label 的可选原启动标识。RecoveryRuntime 在启动前检查 created 状态及原标识，Docker 按完整 ID 启动；响应丢失后只能核对同一实例，不重启。平台已接受的取消/超时优先，重放不续租、不重置起点。默认旧任务不携带标识，执行主循环未切换。

审查发现首次启动窗口未受剩余断联容忍约束，已改为最长 30 秒且不超过剩余容忍，可小于 1 秒；正好截止的确认拒绝启动。启动窗口到期不影响已运行进程的正常对账。另修正合法极大容忍值以 timedelta 构造绝对截止会溢出的问题，改为比较已过秒数，覆盖 2**63-1 上界。

标准入口扩大专项 1332 通过、8 跳过、1969 未选择（528.23 秒）；在这一轮后，仅对 recovery_control 的大容忍值比较补充一行修正和边界用例，最终边界专项 149 通过、无跳过、3162 未选择（23.40 秒），包括全部控制对账、启动确认与真实启动后端测试。没有把不同版本的报告混算为一次全项目结果。新启动确认/账本模块与未再修改的运行适配器行/分支均 100%；最终控制对账 100% / 98.57%；宿主机后端 100% / 92.86%，Worker 主模块 94.77% / 85.95%。入口 execution.py 在专项为 85.40% / 66.29%，全项目门槛仍未满足，脚本返回 1，不宣称全覆盖达标。

扩大专项中产物和存储探测两个开关跳过，已在最终边界专项开启并通过；剩余 1 项需要独立纳管 Compose、5 项需要 root/systemd/专用业务账号。此前一轮扩大测试因修正剩余容忍边界主动中断（278 通过、16 跳过时，pytest 退出 2），未计为完整验收；没有在覆盖率运行中修改源码。证据和逐模块报告来源见 launch-20261125.json。

真实本机 systemd/Docker 短程序验证实际运行后端的启动响应丢失、原实例恢复及正常退出；不是生产评测批次、NPU/GPU 模型验证。未部署，没有新增数据库迁移。生产领取能力协商、完整 Worker 循环、可靠结果和清理确认、界面与全链路验收仍未完成，相关 OpenSpec 任务不勾选。

### 用户要求上线后的部署前复核（2026-11-25）

现网四角色 API 仍使用 mini-eval-api:recovery-foundations-20261125，容器均健康。核对 worker/client.py，尚未调用 RecoverySupervisor、RecoveryLaunch 或恢复领取路径，因此本轮没有部署、迁移或升级节点，也没有启用长期断联能力。缺少实际领取/监督主循环、可靠结果应用及清理确认，更新镜像不能替代这些实现。

重新开启 RECOVERY_RUNTIME_TEST=1，经标准 scripts.test_coverage 入口运行 runtime、launch runtime 和 Worker control 专项，33 通过、无跳过、3278 未选（25.95 秒）。实际 systemd 用户服务/Docker 短进程验证断联监督、原运行身份对账、启动响应丢失后不重复启动；网络失败与时间为注入，API 为隔离 TestClient，不是现网批次、真实网络中断或模型评测。另跑 supervisor 专项，30 通过、无跳过、3281 未选（2.26 秒），覆盖平台停止决定、本地业务超时不受 HTTP 阻塞、停止重试与持久化失败；此组为单元测试。

原始报告分别为 coverage-artifacts/recovery-predeploy-20261125 和 coverage-artifacts/recovery-predeploy-supervisor-20261125；两个 pytest_exit_code 均为 0，标准入口因全项目覆盖率门槛仍退出 1，不合并两次报告宣称全项目达标。上线和平台可见端到端验收尚未完成。

### 恢复业务结果接线（2026-11-25，未上线）

已实现 recovery/results 显式 apply:true，把回合及 SDK task-result 写入原业务记录，和收据同事务；取消/超时/已结束执行仍接受原授权事实，但不续租、不改变终态和清理。Worker 交付器要求摘要及应用 ACK，旧 API 仅收据时保留待交付记录。无迁移，不改变现有 Worker 主循环或路由分类。

首轮新增测试 18 失败/7 通过：测试 helper 使用 dict 重复关键字、缺完整 COMPLETED 字段、错误调用不存在的 outbox.close、ORM 删除 Job 触发非空外键；修正测试构造，保留 /tmp/recovery-results-first.log，第二轮25通过。随后增加真实 PostgreSQL 并发/取消测试和缺授权用例，统一入口专项186通过、无跳过、3153未选（20.73秒）；覆盖率来源 coverage-artifacts/recovery-results-20261125，recovery_results、recovery_records、recovery_delivery 三模块行/分支均100%。全项目门槛仍未达到，pytest退出0，标准脚本退出1，不把专项统计当全项目达标。

专用 PostgreSQL16（端口32820）验证8次并发同结果仅一份收据、全局调度锁被占用时仍完成补报、取消提交后保留成功事实且平台保持取消。这不是实际平台批次或模型评测。原完成接口仍受租约检查且直接清理，后续须接入分离的完成摘要/清理与实际领取监督主循环；本轮不切换现网。

### 完成与清理接口接线（2026-11-25，未上线）

complete 携带恢复代次/进程身份走新路径，必须有核对后的退出事实；原完成正文幂等保存，不直接释放资源。cleanup 独立确认，取消/超时/终态不被迟到成功覆盖。恢复授权执行不能调用无证明旧路径，旧执行保持兼容。修正对账把相同退出重报判为身份异常的问题，以及已确认退出后实体卸载抹掉事实的问题；相同退出保留，退出码冲突/重新运行仍待核对。

初轮新模块22项通过；扩大标准专项1失败/155通过，HTTP测试创建的后台调度器与准备阶段抢全局锁而返回预期503。失败记录 finish-first-failure-20261125.txt；该接口测试隔离后台调度，另外专用PostgreSQL测试显式持调度锁验证503及取消提交后重试，未修改繁忙语义。最终扩大标准专项178通过、无跳过、3189未选择，96.53秒。recovery_finish行/分支100%；recovery_control为99.34%/97.30%；execution入口81.24%/56.45%，尚未满足关键模块门槛。全项目门槛未通过，pytest退出0、标准脚本退出1；报告 coverage-artifacts/recovery-finish-final-20261125。

用户提出取消后重新评测，补充独立用例：未结束/未清理不得重试，清理后生成新Run，旧完成/清理/对账重放不改新任务PENDING，1通过。此补测未与前述覆盖率报告合并。真实HTTP/PostgreSQL验证，不是现网批次或真实模型评测；未部署、无新迁移。完整Worker链路、删除安全记录及结果未齐时独立资源收尾仍未完成。

### Worker 收尾恢复入口（2026-11-25，未上线）

新增 worker/recovery_finish.py 和 Worker.finalize_recovery，execute 对已有 recovery_finalization 优先恢复，不进入旧启动流程。完成正文/身份先原子落盘，完成ACK后移除原已退出实体，清理ACK后才清空journal；每个阶段均可在响应丢失/重启后重放。Docker按完整ID删除，同名不同身份或仍运行不得移除；missing只用于已记录阶段的幂等继续。正常领取/启动/监督尚未接通此路径，不能因存在收尾入口就启用生产恢复。

初轮26项通过；标准专项93通过、无跳过、3305未选（29.52秒）。扩大旧Worker启动、服务执行、过载、交付、恢复和Docker契约回归274通过、无跳过、3124未选（72.25秒）。最终报告coverage-artifacts/worker-recovery-finish-expanded-20261125：新收尾及runtime模块行/分支100%，worker/client.py为79.42%/70.16%，尚未达到关键模块门槛；标准入口仍因全项目门槛退出1，pytest退出0。没有修改覆盖率分母或合并未插桩子进程。

真实systemd用户服务和Docker短程序均执行完退出0，移除后模拟清理响应丢失，再由Worker.execute恢复原cleanup正文，未重启程序。控制响应为测试替身，非现网批次、真实断网或GPU/NPU模型验收；其余实际运行后端恢复测试也同时通过。无新增迁移，未部署，现网默认行为未切换。

## 2026-11-25 领取接口事务接线（未启用）

- 原 claim 接口增加成对的 recovery_version=1/request_id 协商。新授权仅测试开关 `RECOVERY_EXECUTION_ENABLED=1` 且节点声明 `disconnected_execution_v1` 时创建；实际 Worker 尚未声明此能力。已创建收据在关闭开关后仍可恢复查询。
- 分配状态与不可变领取快照同事务提交；注入绑定后异常验证全部回滚。同请求重放不续租、不修改原快照；取消后只返回停止处置。旧请求不能领取重新评测产生的新尝试；旧协议不能绕过恢复授权领取或清理。
- 标准覆盖率运行：169 passed、22 skipped、3216 deselected；pytest_exit_code=0，但目录/功能组覆盖率门槛未通过。报告位于 `coverage-artifacts/recovery-claim-integration-20261125/`。本次选择下 execution.py 行77.94%、分支59.22%；recovery_claims.py 行77.46%、分支53.85%。不能宣称覆盖率达标。
- 原22项 PostgreSQL 跳过随后在独立 PostgreSQL 16.8 容器中补跑，22 passed。领取及完成专项（含新增取消后重新评测领取隔离）37 passed、2项 PostgreSQL 跳过；随后完整 recovery_finish.py 在隔离 PostgreSQL 环境补跑29 passed、无跳过。这些独立补跑未合并进覆盖率数据，不与前述通过数相加充当独立用例数。
- 取消后重新评测的领取测试预置已清理终态，测试的是领取隔离；真实完成/清理链路另有 recovery_finish 专项。未做本阶段真机断网批次验收，未部署，不新增迁移。主循环、结果未齐收尾和界面接线仍未完成。

## 2026-11-25 Worker 领取主循环交接（未启用）

- `worker/recovery_poll.py` 接入实际 run 的领取调用。未广告能力且无 pending journal 保持旧 wire contract；未决请求优先按原 request_id 恢复。收据→执行 journal→清除领取文件严格排序，磁盘失败、冲突或停止处置保留证据，不进入旧启动流程。
- 新增16项测试覆盖响应丢失、无能力恢复未决请求、交接提交后清除失败重放保留offset、子journal落盘失败、已有journal冲突、无效ACK、停止收据、空ACK和旧协议兼容。API+Worker联测另1通过：真实API事务提交后丢响应，重试原快照落入执行journal，授权只创建一次；不是部署批次。
- 标准专项199 passed、2 skipped、3223 deselected，pytest成功，覆盖率门槛失败。报告 `coverage-artifacts/worker-recovery-poll-20261125/`；新模块行/分支100%/100%，本次选集中 worker/client.py 为57.33%/38.24%，不能据此宣称整个Worker达标。两项跳过为真实运行后端opt-in，随后启用本机systemd用户服务/Docker短程序测试，2 passed，无跳过。单独补跑与API联测未并入覆盖率。
- 同步运维指南、升级指南、Skill索引与OpenSpec。没有新增数据库迁移，没有部署或广告新能力。接下来仍需完整监督入口、停止/未启动收据收尾、结果未齐收尾及界面接线；恢复执行不能退回旧watchdog作为临时替代。

## 2026-11-25 恢复启动组合入口（未启用）

- 新增 `worker/recovery_start.py` 与 Worker.start_recovery，组合原领取快照校验、运行准备、平台prepare、持久启动尝试、真实身份绑定和父journal身份证明提交。生产execute尚未调用该入口，原安全拦截仍在。
- 19项定向测试通过，覆盖正常/重复绑定、启动响应丢失、父身份写入失败、创建调用结果未知、已创建对象响应丢失、取消/冲突、原确认窗口过期、boot/slot/任务/标识/身份篡改和已有运行实例。两项为本机systemd用户服务与Docker短Python程序：调用真实RecoveryRuntime.start后注入响应丢失，从磁盘恢复仅绑定同一InvocationID/容器ID，程序退出0后再恢复也不重启。prepare API响应为测试替身，未作为真实平台批次或模型评测。
- 标准专项140 passed、1 skipped、3303 deselected，pytest成功；覆盖率报告 `coverage-artifacts/worker-recovery-start-20261125/`。新增recovery_start行/分支100%/100%；本次选集worker/client为20.44%/12.03%，整体覆盖率门槛仍失败。未降低门槛或排除业务代码。
- 跳过项为PostgreSQL启动/取消锁竞争，随后新建独立PostgreSQL16.8补跑1 passed，无跳过；补跑未合并进覆盖率。容器测试资源清理，未操作生产数据库。
- 同步操作/升级文档、Skill索引、OpenSpec设计与任务说明；未新增迁移，未启用或部署新恢复能力。持续监督、未启动停止处置、结果交付和清理的完整入口仍待接通，不能用本阶段替代整体断联验收。


2026-11-25 监督组合入口：标准专项107 passed、3354 deselected、无跳过，报告coverage-artifacts/worker-recovery-monitor-20261125；recovery_monitor行/分支100/100，worker/client本次选集9.53/2.14，整体覆盖率门槛失败。真实host/Docker停止测试使用API响应替身，不能替代平台批次验收。首次2 failed/15 passed的证据见monitor-first-failure-20261125.txt，修正测试后17 passed。未部署，生产execute仍保留安全拦截。

### 结果采集与独立同步（2026-11-25）

新增四种读取器可靠入队接线及原子游标；最终采集必须核对原实体退出、完整读取并持久封口。空队列不能替代采集结束。pending_results允许完成/清理和结果应用分离；同步确认需完成、清理及完整性校验，旧协议不能使用此旁路。取消后重新评测的旧结果不影响新尝试。恢复扫描修正launch/supervision旁路JSON被当成执行ID的问题。

标准脚本专项309 passed、3195 deselected、无跳过，36.09秒；开启RECOVERY_RUNTIME_TEST和独立PostgreSQL。新采集模块、Worker收尾及后端收尾行/分支均100/100；全项目门槛仍失败。具体入口覆盖率见result-collection-20261125.json，完整报告coverage-artifacts/recovery-results-complete-20261125。真实测试是systemd/Docker短程序与收尾，不是完整平台断联批次或真实模型评测。

失败证据见result-collector-failures-20261125.txt：初始测试夹具缺runs目录、游标失败注入位置错误；API回合记录会增加平台时间戳，断言已改为核对业务字段及时间戳；新增最终采集标志测试必须落盘后模拟重启。标准首轮遗漏importlib导致同名测试收集冲突，按现有文档补参数；扩大检查发现契约清单和生成网关未同步，已补齐新路由及旧cleanup可选身份正文的覆盖记录，未改历史审计原件。最终上述专项全部通过。

同步worker-upgrades、operations、Skill项目索引、OpenSpec设计及任务记录；strict validate通过。生产execute尚未接通正常监督、延后交付及界面状态，任务4.x不勾选，未部署、未广告能力。本阶段不新增迁移。

### 清理后的恢复交付入口（2026-11-25）

Worker.execute恢复已清理的finalization时，以原执行outbox继续可靠结果交付和最终同步确认，不再进入启动/续租流程。每轮最多16条；没有最终采集标志保持待处理，原队列文件丢失报错保留journal，不能新建空队列假装成功。测试覆盖结果ACK与最终同步ACK丢失、进程重启重放、队列缺失和稳定执行路径。

results-finished仅使用Run→grant行锁，不拿资源全局锁。真实PostgreSQL测试持有调度锁时，首次及重复结果同步仍成功；完成/清理的资源锁不变。标准专项315 passed、3195 deselected、无跳过，38.44秒；三个直接修改的恢复模块行/分支均100/100，完整入口及全项目门槛仍未达标。报告coverage-artifacts/recovery-results-entry-final-20261125；真实host/Docker短程序均通过，不能替代完整平台断联批次。同步操作/升级文档、Skill、OpenSpec，strict validate通过。未启用生产领取能力、未部署完整恢复功能。

### 停止后实体卸载的恢复证据（2026-11-25）

修正已核验原身份、停止成功后systemd卸载单元导致恢复路径只见missing、无法完成收尾的问题。新增runtime-exit旁路证据，绑定执行/代次/launch/boot/运行身份，未知退出码保留null；任意missing、停止失败、身份替换、坏文件及磁盘满不生成确认。原退出事实不可被不同退出码覆盖，多线程证据写入串行且重复观察不重复fsync。配套文件不进入执行journal扫描；退出证据支持重启后启动交接只核对、不再次启动。若停止后落盘前崩溃且实体已消失，仍保留待核对，未假装解决不可判定窗口。

首轮定向102 passed；标准专项307 passed、3221 deselected、无跳过，50.78秒，开启真实host/Docker与隔离PostgreSQL。worker/recovery_runtime.py行/分支100/100；worker/client.py 50.05/37.04，整体门槛仍失败。报告coverage-artifacts/recovery-exit-20261125。旧stop替身修正为模拟真实停止后的状态；实体卸载后断言区分物理不存在和已保留退出事实，未放宽身份验证。

标准报告后增加真实运行+隔离FastAPI联测，宿主机/Docker分别覆盖过期旧租约继续执行和恢复取消→停止→完成→清理，共4 passed、9 deselected，9.06秒。调用实际reconcile/finish/cleanup处理与事务；退出码为未知或实际值，平台取消结论保留。此独立补测覆盖率未合并，不与307项相加冒充统一报告。未测试完整生产Worker主循环、界面或真实模型批次，未部署新能力。

操作/升级说明、Skill项目索引及OpenSpec设计/任务记录已同步，strict validate通过。4.x仍未全部完成，生产execute对新领取仍保留拦截。

### 独立控制循环（2026-11-25）

恢复监控新增唯一控制HTTP线程，数据调用方阻塞不阻止对账；控制HTTP阻塞不阻止本地时限检查。传输异常重放原请求，401保留错误且等待凭据/服务修复，不杀业务；实际取消仍经原控制决策停止。启动/关闭串行，控制HTTP未返回不释放执行监督锁。组合入口Worker.monitor_recovery(control=True)启动失败会释放已经创建的本地监控。

定向首轮28 passed。标准专项89 passed、3454 deselected、无跳过，35.16秒；worker/recovery_monitor行/分支100/100，worker/client本次选集9.91/2.37，整体门槛失败。报告coverage-artifacts/recovery-control-loop-20261125。覆盖慢数据调用方、传输失败、401、取消、原请求重放、重复启动、关闭竞态、线程启动失败、无效间隔；真实host/Docker同时验证手动/自动控制HTTP阻塞时本地有限容忍停止及原身份恢复。真实测试为短程序与故障响应，不是完整生产Worker批次或模型评测。

操作/升级说明、Skill索引、OpenSpec设计与任务记录已同步。生产execute仍未接通完整新协议，服务探测重建、结果/日志全过程及发布验收未完成，不启用恢复能力、不勾选4.x完成。


### 2026-11-25 正常 Worker.execute 接线

本轮新增 `worker/recovery_execution.py` 并由真实 `Worker.execute` 分派，不再只测试独立 helper。首次启动/接管、独立控制与本地监督、服务探测、原退出 ACK、完成及清理、清理后补报均经过入口验证。源数据在移除运行实体前保存；结果或日志失败不替代进程退出事实。非法最终指标转为协议缺失/失败摘要，不用非法成功指标反复堵住控制完成。旧执行保留旧看门狗；协商执行在 Worker 正常退出时保留原进程和恢复账本。

新增真实测试使用本机 systemd 用户服务及 Docker 短程序，配合隔离 FastAPI/SQLite，实际完成启动、断网注入、自然退出/取消/业务超时及 Worker 重启接管；服务测试实际监听 TCP，并经服务健康 API 到就绪再取消。HTTP 经 TestClient 调用，不是公网故障测试，不新增生产页面批次，也不是 GPU/NPU 模型质量验收。扩大专项同时使用独立 PostgreSQL 执行既有锁与收尾测试。

首次失败保留在 execute-loop-20261125.json / execute-loop-first-failures-20261125.txt。首次新单元夹具缺少 runs 目录；首次真实夹具缺少 client_spec.timeout_seconds；扩大回归的三项失败为两处尚断言入口拒绝的旧测试以及缺少 state 的旧关闭测试对象。分别修正夹具、更新入口行为断言并兼容旧子对象。未删除用例、降低门槛或将通过数量视为覆盖率。

本轮主入口接线不等于完整恢复特性可发布：4.x 待办保持未完成，尤其启动前取消处置、异常结果同步收敛、队列长期容量及界面仍需继续。Worker 进程本身退出时本地线程不再运行，不能宣称有限断联截止在该停机期间精确触发。生产未广告恢复能力、未开启实验开关；不对在途任务单边切换协议。

普通 stdout 末尾没有换行时，恢复结果采集在核实原进程退出后允许普通诊断文本收尾；完整或部分 `MINI_EVAL_EVENT ` 前缀仍保留等待处理，不将未完整协议记录当作普通日志丢弃。原始输出在运行时清理前持久保存。回归覆盖普通文本、中文、截断事件、部分事件前缀及 Worker.execute 完整收尾。

收尾专项 132 passed、2 skipped、3459 deselected（72.25s）；恢复主入口行 166/167、分支 62/64，结果采集 85/85、46/46，收尾 84/84、42/42。完整项目门槛未达标。扩大集合/编排专项按用户要求中断，不作为完成验收证据。先前扩大恢复专项为 833 passed、1 skipped、2753 deselected（411.46s），两轮不合并统计。详见 execute-loop-20261125.json。

### 启动前取消收尾

原领取未移交且无启动记录的 stop 收据可以独立确认；已有执行记录恢复原记录，不声明未启动。已准备对象仅在持久账本无启动尝试、原标识 stop 且清理确认时走 not-started 控制接口；平台保留既有终态，退出码保持空。接口分类、审计及 Skill/运维说明同步。211 项专项通过、无跳过，新增两模块关键覆盖门槛达标；额外 29 项边界通过。真实 host/Docker 未启动取消及 ACK 丢失重试 2 项通过。首轮旧断言和接口审计失败保留于 unstarted-20261125.json；全项目覆盖率尚未通过，生产开关保持关闭，删除/撤销等完整 4.x 仍待验收。

### 全量界面验证的旧等待假设

首次前端 254 passed、2 failed、10 skipped（5.3 分钟）。两项失败分别是旧测试以默认 5 秒断言等待撞上现有 5 秒监控周期，以及仍等待已移除的模板选项周期刷新；确认实现为批次监控 5 秒且失败退避、静态模板选项在 focus 时刷新。回归改用可控时钟验证断联/恢复，显式触发 focus 并等待请求验证草稿保持，不改变产品代码或放宽业务断言。失败证据 ui-first-failures-20261125.txt。

### 全量验证结束（2026-11-25）

Python 全量首轮 3534 passed、2 failed、92 skipped，2782.28 秒。覆盖率全部门槛通过：总体行 93.12%、分支 86.12%，任务集合与编排行 96.32%、分支 82.62%。两个失败由新 not-started 接口未同步双进程/四角色网关导致；同步两份配置后 106 项相关回归通过（1.72 秒），未再跑全量、未合并覆盖率。前端首轮 254 passed、2 failed、10 skipped；修复旧测试等待假设后 50 项相关通过，完整复测 256 passed、10 skipped。类型检查和构建通过，保留 500 kB 构建警告。

覆盖率不是所有环境验收完成：92 个 Python 跳过涉及专用迁移数据库、特权挂载、隔离纳管 Compose、指定旧版本/镜像及真实场景开关；10 个前端跳过需要额外真实 API/业务/GPU 环境。逐项记录于 full-20261125.json，不把跳过记作成功，也不把本机合成进程测试称为 GPU/NPU 模型评测。未部署、未开启恢复能力。

### 2026-11-25：上传前淘汰积压诊断日志

Worker v2 日志准备新块时只追赶最新 32 条环形记录（最多 64 KiB），显式 gap；已发送未确认 pending 原文重放，确认后才跳过旧记录。普通日志不再将整个 256 槽缓冲逐块补传。协议结果、独立日志文件及控制通道不变。

专项 120 通过、0 失败/跳过、3514 未选；初始日志专项 27 通过。修改模块行 93.06%、分支 80.95%，完整项目分母因专项选择未达门槛，不合并历史覆盖率冒充本轮全量。TypeScript 和 OpenSpec strict 检查通过。边界与命令见 latest-log-window-20261125.json；未部署、无新迁移，尚无整群重连压力验收。
