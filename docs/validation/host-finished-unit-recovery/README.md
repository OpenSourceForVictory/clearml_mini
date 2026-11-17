# 宿主机已结束单元恢复验证

触发证据：现场启动时出现 `previously created container is missing; refusing duplicate execution`。仅凭该消息不能判定首次启动失败的具体原因；旧文案也无法区分 Docker 与宿主机任务。

修正边界：当宿主机 transient unit 已消失，但本地同一执行的 `launch.json` 标记已启动且 `exit.json` 有整数退出码时，Worker 按该退出码继续完成，不重新启动业务。缺少或损坏退出记录、执行 ID 不匹配时继续拒绝重复启动。Docker 缺失容器仍拒绝原地重建；错误文案改为明确的 `Docker container` 或 `host unit`。

验证：`python -m pytest backend/tests/test_host_backend_lifecycle.py backend/tests/test_worker_execution_recovery.py -q`，53 passed；使用本机缓存 Node 24 执行 TypeScript `tsc --noEmit`，通过。测试模拟 systemd 和 Docker 状态，不代表现场任务、真实 GPU/NPU 或模型评测验收。没有运行项目全量覆盖率；需要现场较早的 Worker 日志才能确定首次启动失败原因。
