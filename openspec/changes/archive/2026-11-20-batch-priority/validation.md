# 验证记录

- 后端定向：批次优先级、调度、公平轮转及批次控制 **17 passed**。
- 前端：`batch-history.spec.ts` **15 passed**；`npm run build` 通过（Vite 提示现有 bundle 超过 500 kB）。
- 数据库：一次性 PostgreSQL 16.8 从空库 `alembic upgrade head` 和 `alembic check` 均通过，目标 head 为 `aa741b2`。临时容器已停止并清理。
- Python 全量：`PYTEST_ADDOPTS=--import-mode=importlib python -m scripts.test_coverage` **2278 passed / 179 skipped / 0 failed**，1694.35 秒。JUnit 和覆盖率报告保存在忽略目录 `.runtime/batch-priority-coverage-importlib-20261120/`。Importlib 模式仅避免 `backend/tests` 与 `backend/tests/integration` 中同名测试文件的收集冲突；未更改覆盖率过滤规则。
- 覆盖率：`backend/app` 95.48% 行 / 83.87% 分支；调度与资源 96.76% / 87.06%；`scheduling.py` 94.06% / 91.04%；新增 `batch_priority.py` 100% / 100%。
- 无 PostgreSQL 的首轮全量报告中 `scripts` 为 78.38% 行 / 78.20% 分支，低于行覆盖率门槛；该基线随后由下方启用真实 PostgreSQL 的复测覆盖。未调低阈值或排除脚本。最初两次覆盖率尝试因 pytest 同名模块收集失败，原始报告也保留在同级 `.runtime/batch-priority-coverage-*` 目录。
- 2026-11-20 部署到本机 `mini-eval` Compose 平台：保留原有 Compose 文件/镜像覆盖顺序，构建 API、migrate、bootstrap 目标镜像；停止 API 前确认无非终态任务，备份数据库至忽略目录 `.runtime/batch-priority-deploy-20261120/eval-before-aa741b2.dump`（SHA-256 `3e8276a7850520063ce8073dcae2e668837cdb9aaa0b04d587d5771f1cccb856`），执行迁移并仅重建 API。数据库 current=`aa741b2`，priority 列及范围约束存在，115 个历史批次均默认为 0；`/healthz`、`/readyz` 返回 200，OpenAPI 暴露优先级 PATCH，已服务前端 bundle 包含优先级界面文案。管理员登录及真实批次列表 GET 均返回 200，批次视图包含 `priority` 和 `pending_count`。
- 部署最初没有排队批次，因此当时只做了列表读取，未观察现场调度顺序。2026-11-20 后续在已部署平台完成一次受限真实队列 smoke：创建临时 Docker command 模板，仅运行 `python3 -c "print('<smoke>:no-op')"`；为同一管理员创建两个各含 1 项的待调度批次，通过真实管理员 API PATCH 设置优先级为 1 和 9。先以不存在的 GPU 型号将任务留在队列中，确认优先级与 `pending_count=1` 后，再只允许唯一兼容的 Quadro RTX 8000 卡调度。高优先级批次先预留（09:11:26 UTC），低优先级批次后预留（09:11:32 UTC）；两项均在该卡上以退出码 0 成功结束。测试模板通过管理接口禁用后删除，两个批次通过正式删除 API 清理了任务/执行数据并留下隐藏 tombstone；复核无测试模板、无测试任务、平台无非终态任务。没有执行真实评测程序，也没有占用 NPU。详见[现场队列 smoke 记录](../../../../docs/validation/batch-priority-live-smoke-20261120/README.md)。
- 本次真实队列 smoke 不更新 Worker，也不等同于 NPU 或多物理节点调度验收。

## PostgreSQL 全量覆盖复测

- 启用一次性 PostgreSQL 的 `TEST_DATABASE_URL`、`LOG_TEST_DATABASE_URL` 和 `MIGRATION_DRIVER_TEST_URL`，按项目统一 `scripts.test_coverage` 口径执行，使用 importlib collection mode 规避既有同名测试模块冲突。
- 首轮 **2327 passed / 126 skipped / 4 failed**；4 项迁移回归的共享 fixture 使用当前 ORM 在旧 schema 插入批次，带入旧 schema 尚无的 `priority` 列。修正 fixture 为只写历史 schema 自始存在的列；真实 PostgreSQL 定向迁移回归 **5 passed**。
- 修正后完整全量 **2331 passed / 126 skipped / 0 failed**，1835.16 秒；总覆盖率 **92.00% 行 / 83.14% 分支**，`scripts` **80.81% 行 / 78.20% 分支**。所有目录及关键功能门槛通过；126 项条件跳过仍保留且未作为已验收功能。最终报告位于忽略目录 `.runtime/batch-priority-coverage-postgres-recheck-20261120/`，首轮失败证据位于 `.runtime/batch-priority-coverage-postgres-20261120/`。
