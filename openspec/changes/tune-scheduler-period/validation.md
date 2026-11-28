# 验证记录（2026-11-28）

触发条件：默认 2 秒的调度轮间等待使定时扫描较频繁地尝试全局调度锁；用户接受 10 秒默认等待。修正仅改变轮间等待，不改变单轮持锁范围、请求分类或数据库池容量，因此不能把它视为 `database busy` 的完整修复。

- 系统 Python 执行 `python -m pytest backend/tests/test_reservation_windows.py -k scheduler_loop -q` 在收集阶段失败：缺少 `fastapi`，未运行测试。
- 改用项目 `.venv/bin/python` 执行相同命令：2 通过，16 未选中。
- `.venv/bin/python -m pytest backend/tests/test_reservation_windows.py backend/tests/test_scheduling.py backend/tests/test_batch_controls.py backend/tests/test_scheduler_query_scope.py -q`：37 通过、1 跳过；跳过项为需要独立 PostgreSQL 的 `test_postgres_active_jobs_batched_and_unmodified`，当前未提供该测试库。此专项不代表全项目覆盖率，也未验证 10 秒时现场 503 比例或真实任务分配延迟。
- `git diff --check` 通过。OpenSpec CLI 在本机不可用（`openspec: command not found`），本变更按仓库现有 OpenSpec 文件布局记录，未声称 CLI 校验通过。

本机发布：确认 `evaluation_jobs` 无活动状态后，沿用当前容器标记中的 17 个 Compose 覆盖文件，仅以 `--no-deps --no-build --force-recreate` 重建 `worker-data-api`。该角色健康检查通过，容器 `SCHEDULER_TICK_SECONDS=10`；`scheduler_state` 心跳从 `08:34:31.780358+00:00` 更新至 `08:34:41.946680+00:00`。本轮复用现有 API 镜像，通过 Compose 环境值生效；未重建其他 API 角色或 Worker。两次采样只确认调度仍在推进，不能作为 503 下降或精确周期统计。其他平台必须采用新 Compose 默认值或显式配置并重建自己的调度角色。
