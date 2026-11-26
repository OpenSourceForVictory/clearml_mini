# Worker 断联期间完成任务的日志补报与升级验收（2026-11-27）

本机生产形态平台的 `local4090-151` 真实 GPU 节点执行可丢弃 Docker 普通命令；程序输出 `OFFLINE_STARTED`，运行 25 秒后输出 `OFFLINE_FINISHED`。节点到 API 的流量被临时网络规则阻断 70 秒，规则有自动恢复定时器，测试结束移除。平台 API 仍通过本机回环地址观察。该测试验证执行与诊断日志恢复，不是模型质量评测。

## 失败证据与原因

0.21.22 实测：程序成功退出 0，原执行只启动一次并清理，但页面只收到一条 Docker 缺口块，两个业务标记都未出现。失败 JSON 保留在 `.runtime/recovery-default-release-20261126/network-offline-complete-gpu-0.21.22-failure.json`（不提交）。

初次 0.21.23 Worker 实测仍复现：本地 `.logs` journal 已持久化 `OFFLINE_STARTED` 待上传块，节点日志报 `execution log upload stopped: HTTP 409`；平台控制通道已经确认原执行。断联超过租约后，旧 `logs-v2` API 只检查租约有效，拒绝恢复授权持有者补报。API 修复后，普通执行继续按租约校验；恢复执行仅在原 Worker、槽位和未撤销授权匹配且尚未终态时允许补报。发送器在终态前最多等待 5 秒并跳过常规 2 秒节流，失败仍标记缺口、继续完成与清理。

## 复测

部署配套 API，升级本机 Worker 到 0.21.23 后，再运行同一真机断联测试：批次 `7a6e0e0c-7511-41e4-8ef9-3acb2cdb80b1`、执行 `4833c2b8-b8c9-4241-b57f-8dd26549ad0e`。网络阻断探测返回 `000`，规则计数器命中；70 秒期间平台任务保持 RUNNING、Worker PID 未变。恢复后接口返回 `200`，原执行 SUCCEEDED、退出码 0、尝试次数仍为 1、执行 ID 不变、清理确认且节点持有执行数为 0。任务日志中 `OFFLINE_STARTED` 与 `OFFLINE_FINISHED` 各出现一次。测试模板已停用；完整机密无关 JSON 保留在 `.runtime/recovery-default-release-20261126/network-offline-complete-gpu.json`，不提交。

本次只验证一个短命令和 70 秒网络断联；不能据此保证所有长期断联日志都完整。环形窗口会淘汰较早内容，API 日志存储仍有 256 KiB 上限。

同日另一台真实 NPU 节点 `1.95.69.29` 的宿主机服务做独立功能复测：服务就绪后阻断节点本地控制 API 通道 70 秒，期间平台显示 UNKNOWN，但业务 PID 始终不变；恢复后重新 HEALTHY。`torch_npu` 在可见设备 `0` 上计算平方和为 1240，停服后清理确认、节点持有执行数为 0。该测试使用节点现有 Worker，验证的是宿主机 NPU 运行与控制断联恢复，不验证本次 GPU 容器退出日志补报，也不是模型评测。原始摘要在 `.runtime/recovery-default-release-20261126/network-disconnect-npu.json`。

## 独立升级执行器的侧车误判

首次升级本机 Worker 时，旧 `/opt/mini-eval-updater/updater.py` 把已结束执行留下的 `*.json.launch.json`、`*.json.supervision.json`、`*.json.runtime-exit.json` 也识别为活动执行 journal。它在替换代码前失败，回滚再次触发同一误判，平台显示 `rollback_failed`。旧 Worker 包仍在，停止并更新独立升级执行器后它完成回滚、原 Worker 恢复在线；管理员解除失败暂停并重新升级到 0.21.23，结果 SUCCEEDED。新逻辑仅把 `<执行UUID>.json` 当成活动 journal，真实未结束执行仍阻止升级。测试见 `backend/tests/test_updater_executor.py`，操作步骤见[节点升级说明](../worker-upgrades.md)。

专项回归：`backend/tests/test_execution_logs_v2.py`、`test_log_sender.py`、`test_worker_recovery_execution.py`、`test_updater_executor.py` 共 89 通过；另有真实 Docker 隔离回归 1 通过。首次全量 3571 通过、359 条件跳过、0 失败，但 `scripts` 行覆盖率 77.98% 未达 80% 门槛，因此命令退出 1。原始 JUnit、覆盖率和日志保留在 `.runtime/recovery-default-release-20261126/coverage-final/` 与 `full-tests-final.log`；没有降低门槛。随后在 `test_validate_multinode_batch.py` 增加有断言的 CLI 成功、预检失败及任务失败回归，定向 5 项通过。

同口径复测 `PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m scripts.test_coverage`：3574 通过、359 条件跳过、0 失败；总行/分支覆盖率 92.63%/85.58%，`scripts` 80.84%/79.15%，六个 Python 目录及关键功能组门槛均通过。真实业务子进程及前端未合并进 Python 覆盖率。复测原始报告保留在 `.runtime/recovery-default-release-20261126/coverage-final-recheck/`，日志为 `full-tests-final-recheck.log`；前端构建通过，Playwright 260 通过、10 条件跳过。两项 OpenSpec 变更均通过 `openspec validate --strict`。
