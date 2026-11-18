# 新纳管机器共享资产与缓存验证

2026-11-18：新纳管使用 `根路径/机器名/gpuN` 或 `npuN` 作为各卡独立工作目录，资产和缓存改为同机共享的 `根路径/机器名/assets`、`cache`。不同机器由机器名区分。重新纳管保留旧路径，不搬迁原每卡目录。Docker 挂载的原有读写模式不变。

## 相关验证

- `test_system_node_installer.py`、`test_root_onboarding_probe.py`、`test_node_installation.py`、`test_storage.py`：71 passed。包含 GPU/NPU 多卡规划、共享目录创建、已有资产/缓存文件和权限保留、Worker 存储校验、符号链接拒绝、安装回滚及旧安装保留。
- 为旧安装探测补充显式每卡路径断言后，单独重跑 `test_root_onboarding_probe.py`：13 passed。
- `frontend/tests/ssh-onboarding.spec.ts`：5 passed，API 使用 mock；检查提交根路径、计划中的工作与共享路径、已有节点和回连恢复。
- TypeScript `tsc --noEmit` 通过。

首次界面测试 5 个失败，均为 `page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:18080/`，原因是本地测试服务未启动。启动本地 Vite 服务后重跑 5 个通过，未修改业务实现绕过断言。

本次仅运行相关测试，没有执行全量覆盖率统计，不宣称达到项目目录/分支覆盖率门槛。没有真实节点纳管或真实 GPU/NPU 业务验收。
