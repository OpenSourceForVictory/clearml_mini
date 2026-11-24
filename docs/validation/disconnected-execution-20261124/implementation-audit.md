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
