# GPU 任务在 Worker 暂停期间完成后的补报验收（2026-11-27）

本次在已纳管的 `local4090-151`（GPU 0）使用普通 Docker 命令测试断联恢复。业务程序只打印启动标记、等待 25 秒、打印结束标记并退出 0；**不是模型评测**。目标节点先通过平台的排空升级入口从 Worker 0.21.21 升到 0.21.22，回连后确认 `disconnected_execution_v1` 能力和升级执行器在线。测试批次开启 300 秒有限断联容忍，预检为 `ready`。

批次 `5bee772f-e841-46ac-a7d1-68101c135ba3`、任务 `e9f20e83-aa93-443c-b391-b415ab4b08bf`、执行 `019680ac-5b5b-4647-8328-8a88992f6b26`。任务进入 `RUNNING` 后，对该节点 Worker 主进程发送 `SIGSTOP` 70 秒，另设 systemd 定时 `SIGCONT` 作为故障恢复保障。业务容器未被暂停，因此在 Worker 无法上报期间自行结束。暂停期平台仍显示 `RUNNING`；恢复进程后，批次任务变为 `SUCCEEDED`、程序退出码为 0，前后均只有一次 attempt 且执行 ID 未变。日志的启动/结束标记各一次；`cleanup_confirmed=true`，节点占用执行数为 0。数据库 `execution_recovery_controls.snapshot.result_sync=complete`。测试后临时客户端模板已停用，Worker 服务和平台四个 API 角色健康。

第一次尝试用本机 `iptables` 的 UID 规则仅阻断 Worker 到 `192.168.8.151:18000`，但规则计数为 0，业务账号直连 `/healthz` 仍为 200。该次注入**没有形成网络断联**，已立即删除规则并取消临时批次 `f662d9ca-a91f-452f-81ea-e0787233c5f1`，不能作为断联恢复通过证据。随后改用进程暂停，验证的是“Worker 无法上报、业务进程结束、恢复后补报”路径；它不能替代物理断网下 Worker 持续运行的所有网络故障场景。NPU 节点的独立 70 秒实际网络阻断验收另见 [生产节点断联验证记录](disconnected-execution-20261124/production-node-20261126.md)。

测试证据原始 JSON 存放于本机 `.runtime/recovery-default-release-20261126/offline-complete-gpu.json`，不纳入 Git。未执行全量回归或长期压测；本次只验证单个 GPU 普通命令、一次 Worker 暂停和恢复。
