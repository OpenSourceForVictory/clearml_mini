# 验证记录

- 后端定向：批次优先级、调度、公平轮转及批次控制 **17 passed**。
- 前端：`batch-history.spec.ts` **15 passed**；`npm run build` 通过（Vite 提示现有 bundle 超过 500 kB）。
- 数据库：一次性 PostgreSQL 16.8 从空库 `alembic upgrade head` 和 `alembic check` 均通过，目标 head 为 `aa741b2`。临时容器已停止并清理。
- Python 全量：`PYTEST_ADDOPTS=--import-mode=importlib python -m scripts.test_coverage` **2278 passed / 179 skipped / 0 failed**，1694.35 秒。JUnit 和覆盖率报告保存在忽略目录 `.runtime/batch-priority-coverage-importlib-20261120/`。Importlib 模式仅避免 `backend/tests` 与 `backend/tests/integration` 中同名测试文件的收集冲突；未更改覆盖率过滤规则。
- 覆盖率：`backend/app` 95.48% 行 / 83.87% 分支；调度与资源 96.76% / 87.06%；`scheduling.py` 94.06% / 91.04%；新增 `batch_priority.py` 100% / 100%。
- 全仓门槛仍有既存缺口：`scripts` 为 78.38% 行 / 78.20% 分支，行覆盖率低于 AGENTS.md 要求的 80%。未调低阈值或排除脚本。最初两次覆盖率尝试因 pytest 同名模块收集失败，原始报告也保留在同级 `.runtime/batch-priority-coverage-*` 目录。
- 未部署，也未进行真机调度验收；该功能的排序和权限通过 API/调度集成测试验证。
