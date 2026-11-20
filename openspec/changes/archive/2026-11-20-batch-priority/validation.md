# 验证记录

- 后端定向：批次优先级、调度、公平轮转及批次控制 **17 passed**。
- 前端：`batch-history.spec.ts` **15 passed**；`npm run build` 通过（Vite 提示现有 bundle 超过 500 kB）。
- 数据库：一次性 PostgreSQL 16.8 从空库 `alembic upgrade head` 和 `alembic check` 均通过，目标 head 为 `aa741b2`。临时容器已停止并清理。
- Python 全量：`PYTEST_ADDOPTS=--import-mode=importlib python -m scripts.test_coverage` **2278 passed / 179 skipped / 0 failed**，1694.35 秒。JUnit 和覆盖率报告保存在忽略目录 `.runtime/batch-priority-coverage-importlib-20261120/`。Importlib 模式仅避免 `backend/tests` 与 `backend/tests/integration` 中同名测试文件的收集冲突；未更改覆盖率过滤规则。
- 覆盖率：`backend/app` 95.48% 行 / 83.87% 分支；调度与资源 96.76% / 87.06%；`scheduling.py` 94.06% / 91.04%；新增 `batch_priority.py` 100% / 100%。
- 全仓门槛仍有既存缺口：`scripts` 为 78.38% 行 / 78.20% 分支，行覆盖率低于 AGENTS.md 要求的 80%。未调低阈值或排除脚本。最初两次覆盖率尝试因 pytest 同名模块收集失败，原始报告也保留在同级 `.runtime/batch-priority-coverage-*` 目录。
- 2026-11-20 部署到本机 `mini-eval` Compose 平台：保留原有 Compose 文件/镜像覆盖顺序，构建 API、migrate、bootstrap 目标镜像；停止 API 前确认无非终态任务，备份数据库至忽略目录 `.runtime/batch-priority-deploy-20261120/eval-before-aa741b2.dump`（SHA-256 `3e8276a7850520063ce8073dcae2e668837cdb9aaa0b04d587d5771f1cccb856`），执行迁移并仅重建 API。数据库 current=`aa741b2`，priority 列及范围约束存在，115 个历史批次均默认为 0；`/healthz`、`/readyz` 返回 200，OpenAPI 暴露优先级 PATCH，已服务前端 bundle 包含优先级界面文案。管理员登录及真实批次列表 GET 均返回 200，批次视图包含 `priority` 和 `pending_count`。
- 部署后没有排队批次（所有任务均为终态），因此未在真实队列上操作保存和观察调度顺序；实际 API 保存/权限、调度排序及界面交互由上述后端和 Playwright 定向测试覆盖。未更新 Worker，也未进行 GPU/NPU 真机调度验收。
