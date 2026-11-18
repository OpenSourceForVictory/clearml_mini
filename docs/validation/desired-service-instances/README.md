# 服务数量、实例和回合显示专项验证

2026-11-18，本地代码专项验证，未部署、未运行全量覆盖率门禁、未进行 GPU/NPU 模型验收。

- 首轮后端 127 项：118 通过、9 失败。5 项为新增测试误期待 422（项目返回 400）；4 项为编辑测试时错误替换 expected_status 变量导致 NameError。修复测试，保留此失败记录，不改变业务校验来迎合断言。
- 修正后相关后端（test_episode_visibility、test_worker_execution_recovery、test_worker_service_execution）：73 通过，含新增退出码迁移往返及协议拒绝后保留退出码。首次通过的调度、SDK/适配器、服务位置等用例无后续实现修改。
- command_sdk 与 sdk_reporting_contract 单独验证 21 通过。
- 前端 orchestration-status、orchestration-wizard、run-wizard、batch-monitoring：26 通过；普通命令只显示整次执行的边界修改后，batch-monitoring 6 项再次通过。
- TypeScript 与 Vite 构建通过；Vite 保留大于 500 kB chunk 提示。OpenSpec 严格校验通过。
- Python 测试保留 Starlette anyio 别名弃用警告。迁移测试使用本地 SQLite，不代表 PostgreSQL 发布迁移验收。

测试涉及共享/独占目标实例、部分容量恢复、全局/用户配额、暂停和结束、故障不无限复制、旧上限兼容；多实例节点/卡号、各自日志和失败重试；计划回合分页、权限、缺协议仍显示退出码、旧记录未知以及实际宿主机子进程逐回合退出码。

未运行 python -m scripts.test_coverage，因此本轮不宣称任何目录/功能行分支覆盖率达标；浏览器和独立业务子进程未插桩合并。发布需要数据库 x407a89 与 API/前端，再更新 Worker/Runner 0.18.2，才能收到新增退出码。
