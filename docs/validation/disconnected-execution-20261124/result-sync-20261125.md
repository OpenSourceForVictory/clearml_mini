# 恢复结果拒收与独立同步状态专项（2026-11-25）

本轮修复恢复协议中非法业务结果反复占住本地 outbox 队首的问题，并在批次任务、执行尝试中单独显示结果同步状态。业务校验返回 400 时，API 在保存点回滚不完整的业务写入，但保留原始正文、摘要、应用状态和拒收原因；Worker 核对摘要及 `applied` / `rejected` 后才确认该条。后续有效结果可继续补报。认证、归属、代次、容量、网络及临时故障仍不确认。结果同步失败与程序退出状态分别展示，已成功写入的回合不回滚。日志缺口单独显示。

新增迁移 `c14d09e`（父版本 `b03a7b8`）仅增加可空收据字段；历史收据空值仍可重放。已拒收数据存在时降级拒绝。新 API 上线前必须完成同版本迁移，生产恢复能力目前未广告，不能将本轮专项测试视为断联真实模型评测。

验证：

- `.venv/bin/python -m pytest -q backend/tests/test_recovery_results.py backend/tests/test_result_sync_view.py backend/tests/test_worker_recovery_finish.py backend/tests/test_worker_recovery_execution.py`：104 通过、5 条 PostgreSQL 条件跳过；随后在隔离 PostgreSQL 补跑并发应用 2 条、迁移/降级保护 1 条，均通过。
- `PYTEST_ADDOPTS='--import-mode=importlib -k "recovery_results or recovery_finish or result_sync_view or worker_recovery_execution or worker_recovery_finish or batch_monitor"' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/recovery-result-sync-20261125`：182 通过、10 跳过、3468 未选择。统一脚本完整分母总体行 25.31% / 分支 8.98%，门槛退出 1；这是专项分母结果，不代表本项目完整覆盖率，也没有降低门槛。`recovery_results.py`、`recovery_finish.py` 在本专项覆盖全部行与分支；前端和未插桩子进程不计入 Python 覆盖率。
- `npx playwright test tests/batch-monitoring.spec.ts`：33 通过，包含同步中、延迟、拒收失败、日志缺口、历史执行和现有批次操作。`tsc --noEmit` 通过。OpenSpec strict validate 通过。
- 后端批次监控、结果统计、耗时和 Worker 回归组合复测：162 通过、5 条 PostgreSQL 条件跳过（隔离 PostgreSQL 专项另行补跑）；原始测试运行结果以本地命令和覆盖率产物为准。

失败证据：初次直接调用系统 `pytest` 因未使用项目 `.venv` 缺 FastAPI/模块路径而收集失败；两次输入不存在的测试文件、一次调用不存在的前端 `typecheck` 脚本均在收集/执行前退出。新增 API 贯通测试首次误用了不存在的 `Run.slot` 关系，改为按 `slot_id` 查询后通过。这些失败不计入通过数。

未完成：完整断联恢复故障矩阵、真实断联网恢复结果及页面验收、全项目覆盖率回归与生产发布。原有生产镜像仍为 `mini-eval-api:log-recovery-3216ae0`，数据库为 `b03a7b8`；本轮代码和迁移尚未部署。无需更新已有 Worker 才能读取历史 `legacy` 状态；启用新恢复协议仍须两端兼容与完整验收。

后续封口专项：`results-finished` 在 Worker 本地采集封口且 outbox 排空后是最终声明。新增缺失回合、成功率不匹配两类单元场景，Worker 收到 failed ACK 后退休 journal 的场景，以及隔离 PostgreSQL 上的 HTTP failed ACK/重放场景，最终均通过。组合回归 `backend/tests/test_recovery_finish.py`、`test_recovery_results.py`、`test_worker_recovery_finish.py` 为 108 通过、10 条条件跳过；HTTP 场景单独以隔离 PostgreSQL 补跑 1 通过。首次 HTTP 夹具在 `prepare()` 前设置了 evaluation 类型，随后被夹具改回 command 而失败；调整顺序后通过。Worker 测试首次错误地假定 journal 文件会删除，实际 `atomic_json(None)` 写入持久 null 墓碑；改为验证文件内容及内存状态后通过。这些初次失败保留为诊断边界，不计入通过数。

补充主循环故障注入：从真实 `Worker.execute` 入口进入，模拟服务端对一条结果返回 `application=rejected`、最终封口返回 `result_sync=failed`，验证仅启动一次业务程序、完成及清理各一次、原恢复 journal 退休；定向 1 通过。这是本机模拟 API 的短程序，不是实际 NPU/GPU 模型断联验收。

最终专项覆盖率同口径复跑：187 通过、11 条 PostgreSQL/现场条件跳过、3468 未选择；总体行 33.27% / 分支 13.02%，全项目门槛仍按完整分母退出 1。原始报告位于 `coverage-artifacts/recovery-result-sync-final-20261125/`。前端 TypeScript 检查与 OpenSpec 严格校验通过；此处不把专项覆盖率当作全量覆盖率达标。

本次主要逻辑模块在最终专项中的行/分支为：`recovery_finish.py` 96/97、45/46；`recovery_results.py` 55/55、24/24；`result_sync_view.py` 25/26、9/10；`worker/recovery_finish.py` 84/84、42/42。其余全项目代码未由专项覆盖，仍需完整回归。

追加边界验证：平台已请求取消或已判业务超时并清理旧执行后，创建新的重评测尝试，再补回旧执行的 STARTED/COMPLETED 回合结果并封口；旧执行分别保持 CANCELLED/TIMED_OUT，新尝试仍为 PENDING、未分配设备，旧结果只写入旧 Run。另用前后两个 FastAPI 实例模拟 API 提交结果成功后响应丢失与服务重启，Worker 保留原队列项、重发获得同一收据并 ACK；数据库仅存一条收据。这些场景由隔离数据库和短程序驱动，不涉及生产节点。

真实执行专项启用 `RECOVERY_RUNTIME_TEST=1`，经真实 systemd 宿主机/Docker、`Worker.execute`、隔离 FastAPI/SQLite 运行原有 12 个场景，12 通过（90.05 秒）；新增“断联期间程序先退出，随后恢复网络”在宿主机/Docker 两种模式各 1 通过（18.27 秒）。两模式均只创建一次进程，重连后原执行 SUCCEEDED、回合结果补报且清理确认。两轮使用的是合成短程序，不是 NPU/GPU 模型或生产页面验收。相关单元/Worker 组合回归 148 通过、11 条条件跳过，跳过主要为 PostgreSQL 专项前置条件。

测试编写时取消/超时后重评测用例曾误把新建 PENDING Run 的 `cleanup_confirmed` 默认值判为 false；核对模型默认值后改为验证新 Run 未分配执行 ID/Slot，两个参数分支均通过。该断言修正没有改变产品行为。新增测试仅扩充故障矩阵，恢复协议仍未在生产宣告或启用；完整模式、撤销/删除、存储压力、真机页面及最新全量覆盖率仍待验收。

按 `python -m scripts.test_coverage` 同一统计脚本对本轮相关测试筛选执行：187 通过、25 跳过、3460 未选择；完整项目分母行覆盖率 25.24%、分支覆盖率 8.90%，门槛检查退出 1。报告在 `coverage-artifacts/recovery-boundary-20261125/`。其中 14 条真实 systemd/Docker 用例在该次覆盖率命令中因未设置 `RECOVERY_RUNTIME_TEST` 跳过，已在上文单独启用运行；未合并子进程覆盖率。该专项结果不能用于宣称全项目覆盖率达标，也不能抹去此前完整测试的覆盖率证据。
