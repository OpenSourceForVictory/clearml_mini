# 宿主机已结束单元恢复验证

触发证据：现场启动时出现 `previously created container is missing; refusing duplicate execution`。仅凭该消息不能判定首次启动失败的具体原因；旧文案也无法区分 Docker 与宿主机任务。

修正边界：当宿主机 transient unit 已消失，但本地同一执行的 `launch.json` 标记已启动且 `exit.json` 有整数退出码时，Worker 按该退出码继续完成，不重新启动业务。缺少或损坏退出记录、执行 ID 不匹配时继续拒绝重复启动。Docker 缺失容器仍拒绝原地重建；错误文案改为明确的 `Docker container` 或 `host unit`。

补充验证（2026-11-18）：平台库中仅查到一笔匹配该错误的历史执行，位于 29 节点，不能把它直接当作其他节点当前故障。新增 Worker 恢复路径测试，用本地启动标记、退出记录及模拟 systemd 卸载状态验证：退出码 0 可完成普通命令任务，非零退出码按失败上报，缺少或损坏退出记录时失败；四种情况均不得再次启动业务程序。`python -m pytest backend/tests/test_worker_execution_recovery.py backend/tests/test_host_backend_lifecycle.py -q`，58 passed。原有 TypeScript 检查结果属于先前验证，本次未重跑。测试模拟 systemd 状态，不代表现场任务、真实 GPU/NPU 或模型评测验收；按用户要求未运行全量测试或全量覆盖率。新出现的同类报错仍需该次执行更早的 Worker、systemd 和业务日志定位首次故障。
