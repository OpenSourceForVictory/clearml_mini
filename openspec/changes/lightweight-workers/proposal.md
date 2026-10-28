# 轻量 Worker 执行链路

用户决定以现有评测平台为基础实现简单易用的自托管方案，不再部署 ClearML Server。原始 `IMPLEMENTATION_SPEC.md` 中强制使用 ClearML 的条款由本变更覆盖；其余多用户、逐卡调度、隔离执行和验收目标继续适用。

控制节点只运行 API 和 PostgreSQL。各 GPU 机器运行一个轻量 Worker 进程，逐卡注册槽位、拉取任务、在预置 Docker 镜像中运行评测并回报结果。单机 mock 评测无需 GPU。RoboTwin 版本与资产仍待确定，先完成 mock 和调度。
