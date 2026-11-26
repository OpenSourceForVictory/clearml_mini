# 2026-11-26 生产节点升级与断联恢复验证

本机平台使用原 `mini-eval` Compose 项目部署 `mini-eval-api:recovery-default-fix-20261126`，12 个四角色 API 容器健康，网关 `/healthz` 返回 200。部署前确认活动或待清理执行为 0，数据库迁移头与代码均为 `c14d09e`，此次无需迁移。部署和数据库备份留在本机私有 `.runtime/recovery-default-release-20261126/`，未纳入 Git。

通过平台在线升级将 `ascend910b3-29` 的 8 个 NPU Worker 升至 0.21.22。首次升级中，8 卡已上报新版本及 `disconnected_execution_v1`，但旧升级验收只豁免 `service_readiness_v1`，把节点动态恢复能力误判为发布包不一致，验证超时并回滚至 0.21.21。修正 `installation_capabilities` 后，取消失败升级并按原流程再次升级；状态 `SUCCEEDED`，8/8 在线，8/8 上报恢复能力。`test_reconnect_resilience.py` 与 `test_worker_upgrades.py` 专项共 39 项通过，并新增动态能力有/无及未知额外能力的回归断言。

真实执行验证使用平台在 NPU2 通过已批准 `ascend_cann` 宿主机环境启动短时 HTTP 服务。业务进程实际运行 `torch_npu` 张量平方和，结果 1240，设备可见变量为 `ASCEND_RT_VISIBLE_DEVICES=0`；这验证了 NPU 程序启动，不是模型质量评测。执行 ID 为 `3c956d34-c2b8-4304-bad2-8c72245640a2`，数据库存在对应原始恢复授权。服务达到 `HEALTHY` 后，仅向 NPU2 Worker 主进程发送 `SIGSTOP`，使其停止心跳 70 秒；业务 systemd 单元仍独立运行。暂停期间平台服务状态变为 `UNKNOWN`，原授权未撤销，业务 PID 始终为 3379836。`SIGCONT` 后同一执行 ID、同一业务 PID 的服务恢复 `HEALTHY`；随后经平台停止服务，清理确认，节点占用归零。原始脱敏结果见本机私有 `disconnect-npu.json`。

随后在同一 NPU2 卡位重复服务测试：节点配置通过 `127.0.0.1:19800` SSH 转发访问平台 API，仅对这个本机控制端口临时添加 OUTPUT 拒绝规则 70 秒，并配置独立的 105 秒 systemd 定时器自动撤销；SSH 与业务服务端口未被阻断。节点直连 `/healthz` 在阻断期返回 `000`，撤销后返回 `200`。恢复授权仍为 1，业务 PID 从阻断前到阻断后始终为 3474246；平台服务状态由 `HEALTHY` 变成 `UNKNOWN`，连接恢复后同一执行 ID `ee04e1e3-5bee-46ad-9379-2ad06164f590` 回到 `HEALTHY`，再经平台停止并确认清理、节点占用归零。该测试实际切断了整机 Worker 到平台的控制连接，未停止平台 API；原始脱敏结果留在私有 `network-disconnect-npu.json`。

本次验证了单卡真实 NPU 宿主机服务在 Worker 停止上报与节点控制网络阻断后持续运行、恢复对账；当时未模拟物理网线拔除、机器重启、断联期间业务退出后的结果补报，也未运行真实 RoboTwin 模型。节点使用 SSH 本地转发，阻断其本机控制端口等效于节点无法通过该连接访问平台，但不影响机器其他网络连接。全项目覆盖率未在本次发布中重跑。后续 GPU 普通命令在 Worker 暂停期间退出并补报的验证，见 [GPU 补报验收](../worker-offline-complete-gpu-20261127.md)。
